"""Offline HTTP protocol checks, not an evaluation of real model quality.

Ten JD fixtures return explicitly mocked model responses. Screenshots contain
the same JD text, but the mocked server does not read pixels.
"""

import base64
import json
import math
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont
from pydantic import ValidationError

from app.agents.jd_parser import JdParser
from app.config import Settings
from app.deps import get_jd_parser_dep
from app.llm.client import LlmClient, LlmError, get_llm_client
from app.llm.embeddings import DIMENSIONS, EmbeddingClient
from app.schemas.jd import JdImageParseRequest
from tests.fakes import API

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "m3_jds.json").read_text(encoding="utf-8")
)
REAL_ASYNC_CLIENT = httpx.AsyncClient


def screenshot(text="招聘后端工程师，必须熟悉 Python。", format="PNG", size=(1100, 240)):
    image = Image.new("RGB", size, "white")
    font_paths = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    font = next(
        (ImageFont.truetype(str(path), 22) for path in font_paths if path.exists()),
        ImageFont.load_default(),
    )
    ImageDraw.Draw(image).multiline_text((20, 20), text, fill="black", font=font)
    output = BytesIO()
    image.save(output, format=format)
    mime = "jpeg" if format == "JPEG" else format.lower()
    return f"data:image/{mime};base64," + base64.b64encode(output.getvalue()).decode()


def mock_http(monkeypatch, handler):
    def factory(**kwargs):
        return REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def chat_response(payload):
    return httpx.Response(
        200,
        json={
            "model": "mock-model",
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        },
    )


def settings(**overrides):
    return Settings(
        llm_provider="openai-compatible",
        llm_api_key="offline-test-key",
        llm_base_url="https://text.invalid/v1",
        llm_model="text-test-model",
        vision_api_key="offline-vision-key",
        vision_base_url="https://vision.invalid/v1",
        vision_model="vision-test-model",
        **overrides,
    )


@pytest.mark.parametrize("sample", FIXTURES, ids=lambda sample: sample["id"])
async def test_ten_jd_text_and_image_contracts_with_offline_mock(monkeypatch, sample):
    requests = []
    image_url = screenshot(sample["raw_text"])

    def handler(request):
        payload = json.loads(request.content)
        requests.append((request, payload))
        content = payload["messages"][-1]["content"]
        if isinstance(content, list):
            assert content[1] == {"type": "image_url", "image_url": {"url": image_url}}
            return chat_response(
                {"raw_text": sample["raw_text"], "profile": sample["expected_profile"]}
            )
        assert sample["raw_text"] in content
        return chat_response(sample["expected_profile"])

    mock_http(monkeypatch, handler)
    parser = JdParser(LlmClient(settings()))
    text_result = await parser.parse(sample["raw_text"])
    image_result = await parser.parse_image(image_url)
    assert text_result.value.model_dump() == sample["expected_profile"]
    assert image_result.value.profile == text_result.value
    assert image_result.value.raw_text == sample["raw_text"]
    assert text_result.llm.is_stub is False
    assert image_result.llm.is_stub is False
    assert str(requests[0][0].url) == "https://text.invalid/v1/chat/completions"
    assert requests[0][1]["model"] == "text-test-model"
    assert str(requests[1][0].url) == "https://vision.invalid/v1/chat/completions"
    assert requests[1][1]["model"] == "vision-test-model"
    assert requests[1][0].headers["authorization"] == "Bearer offline-vision-key"


async def test_vision_retry_keeps_identical_image_and_reports_schema_failure(monkeypatch):
    seen = []
    sample = FIXTURES[0]
    image_url = screenshot(sample["raw_text"])

    def handler(request):
        payload = json.loads(request.content)
        seen.append(payload)
        return chat_response(
            {}
            if len(seen) == 1
            else {
                "raw_text": sample["raw_text"],
                "profile": sample["expected_profile"],
            }
        )

    mock_http(monkeypatch, handler)
    outcome = await JdParser(LlmClient(settings())).parse_image(image_url)
    assert len(seen) == 2
    for request in seen:
        assert request["messages"][-1]["content"][1]["image_url"]["url"] == image_url
        assert "JSON Schema" in request["messages"][0]["content"]
    assert "无法通过校验" in seen[1]["messages"][-1]["content"][0]["text"]
    assert any("失败" in warning for warning in outcome.warnings)
    assert any("成功" in warning for warning in outcome.warnings)
    assert outcome.llm.input_tokens == 100


async def test_screenshot_is_persisted_as_extracted_text_and_profile(
    client, api_wiring, app, monkeypatch
):
    sample = FIXTURES[0]
    mock_http(
        monkeypatch,
        lambda request: chat_response(
            {
                "raw_text": sample["raw_text"],
                "profile": sample["expected_profile"],
            }
        ),
    )
    llm = LlmClient(settings())
    app.dependency_overrides[get_llm_client] = lambda: llm
    app.dependency_overrides[get_jd_parser_dep] = lambda: JdParser(llm)
    image_url = screenshot(sample["raw_text"])
    response = await client.post(f"{API}/jd/parse-image", json={"image_data_url": image_url})
    assert response.status_code == 200
    row = api_wiring["jds"].created[0]
    assert row["source_type"] == "image"
    assert row["raw_text"] == sample["raw_text"]
    assert row["parsed"] == sample["expected_profile"]
    assert image_url not in json.dumps(row, default=str)


async def test_independent_vision_provider_works_while_text_provider_is_stub(
    client,
    api_wiring,
    app,
    monkeypatch,
):
    sample = FIXTURES[0]
    requests = []

    def handler(request):
        requests.append(request)
        return chat_response(
            {"raw_text": sample["raw_text"], "profile": sample["expected_profile"]}
        )

    mock_http(monkeypatch, handler)
    configured = Settings(
        llm_provider="stub",
        vision_provider="openai-compatible",
        vision_api_key="independent-vision-key",
        vision_base_url="https://vision.invalid/v1",
        vision_model="independent-vision-model",
    )
    llm = LlmClient(configured)
    app.dependency_overrides[get_llm_client] = lambda: llm
    app.dependency_overrides[get_jd_parser_dep] = lambda: JdParser(llm)
    response = await client.post(
        f"{API}/jd/parse-image",
        json={
            "image_data_url": screenshot(sample["raw_text"]),
            "persist": False,
        },
    )
    assert response.status_code == 200
    assert response.json()["is_stub"] is False
    assert response.json()["jd_id"] is None
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer independent-vision-key"
    assert json.loads(requests[0].content)["model"] == "independent-vision-model"
    assert api_wiring["jds"].created == []


async def test_vision_failure_returns_502_and_never_persists(client, api_wiring, app, monkeypatch):
    mock_http(
        monkeypatch, lambda request: httpx.Response(400, json={"error": "unsupported vision"})
    )
    llm = LlmClient(settings())
    app.dependency_overrides[get_llm_client] = lambda: llm
    app.dependency_overrides[get_jd_parser_dep] = lambda: JdParser(llm)
    response = await client.post(f"{API}/jd/parse-image", json={"image_data_url": screenshot()})
    assert response.status_code == 502
    assert "截图解析失败" in response.json()["detail"]
    assert api_wiring["jds"].created == []


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
def test_supported_image_payloads_are_valid(format):
    image_url = screenshot(format=format)
    assert JdImageParseRequest(image_data_url=image_url).image_data_url == image_url


@pytest.mark.parametrize(
    "invalid",
    [
        "https://example.invalid/a.png",
        "data:image/svg+xml;base64,PHN2Zz4=",
        "data:image/png;base64,%%%",
        "data:image/png;base64,",
        "data:image/png;base64," + base64.b64encode(b"not an image").decode(),
    ],
)
async def test_invalid_images_are_rejected_before_model_call(client, api_wiring, invalid):
    response = await client.post(f"{API}/jd/parse-image", json={"image_data_url": invalid})
    assert response.status_code == 422
    assert api_wiring["jds"].created == []


def test_image_mime_mismatch_is_rejected():
    image_url = screenshot(format="JPEG").replace("image/jpeg", "image/png")
    with pytest.raises(ValidationError):
        JdImageParseRequest(image_data_url=image_url)


@pytest.mark.parametrize("format", ["JPEG", "WEBP"])
@pytest.mark.parametrize("removed_bytes", [2, 10])
async def test_truncated_pixels_are_rejected_before_model_call(
    client, api_wiring, format, removed_bytes
):
    image_url = screenshot(format=format, size=(32, 32))
    header, encoded = image_url.split(",", 1)
    truncated = base64.b64decode(encoded)[:-removed_bytes]
    response = await client.post(
        f"{API}/jd/parse-image",
        json={"image_data_url": header + "," + base64.b64encode(truncated).decode()},
    )
    assert response.status_code == 422
    assert api_wiring["jds"].created == []


def test_image_byte_limit_is_enforced():
    image_url = "data:image/png;base64," + base64.b64encode(b"a" * (5 * 1024 * 1024 + 1)).decode()
    with pytest.raises(ValidationError):
        JdImageParseRequest(image_data_url=image_url)


def test_image_pixel_limit_is_enforced():
    image_url = screenshot(size=(5001, 4000))
    with pytest.raises(ValidationError):
        JdImageParseRequest(image_data_url=image_url)


async def test_stub_vision_returns_honest_unavailable_status(client, api_wiring):
    response = await client.post(f"{API}/jd/parse-image", json={"image_data_url": screenshot()})
    assert response.status_code == 503
    assert "不识别截图" in response.json()["detail"]
    assert api_wiring["jds"].created == []


def embedding_settings(**updates):
    return Settings(
        embedding_provider="openai-compatible",
        embedding_api_key="offline-embedding-key",
        embedding_base_url="https://embed.invalid/v1",
        **updates,
    )


async def test_embedding_http_contract_preserves_input_order(monkeypatch):
    vectors = [[1.0] + [0.0] * (DIMENSIONS - 1), [0.0, 1.0] + [0.0] * (DIMENSIONS - 2)]

    def handler(request):
        assert str(request.url) == "https://embed.invalid/v1/embeddings"
        assert request.headers["authorization"] == "Bearer offline-embedding-key"
        assert json.loads(request.content) == {
            "model": "text-embedding-3-small",
            "input": ["first", "second"],
            "dimensions": DIMENSIONS,
            "encoding_format": "float",
        }
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": vectors[1]},
                    {"index": 0, "embedding": vectors[0]},
                ]
            },
        )

    mock_http(monkeypatch, handler)
    assert await EmbeddingClient(embedding_settings()).embed(["first", "second"]) == vectors


@pytest.mark.parametrize(
    "bad",
    [
        [],
        [{"index": 1, "embedding": [1] * DIMENSIONS}],
        [{"index": 0, "embedding": [1] * 3}],
        [{"index": 0, "embedding": [0] * DIMENSIONS}],
        [{"index": 0, "embedding": ["nan"] + [1] * (DIMENSIONS - 1)}],
        [{"index": 0, "embedding": ["inf"] + [1] * (DIMENSIONS - 1)}],
        [{"index": 0, "embedding": [1] * DIMENSIONS}, {"index": 0, "embedding": [1] * DIMENSIONS}],
    ],
)
async def test_invalid_embedding_responses_are_rejected(monkeypatch, bad):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={"data": bad}))
    with pytest.raises(LlmError, match="嵌入服务调用失败"):
        await EmbeddingClient(embedding_settings()).embed(["query"])


@pytest.mark.parametrize("status", [400, 401, 429, 500])
async def test_embedding_http_errors_are_explicit(monkeypatch, status):
    mock_http(
        monkeypatch, lambda request: httpx.Response(status, json={"error": "offline failure"})
    )
    with pytest.raises(LlmError):
        await EmbeddingClient(embedding_settings()).embed(["query"])


async def test_embedding_empty_input_never_calls_network(monkeypatch):
    def forbidden(request):
        pytest.fail("Empty input should not call the embedding endpoint")

    mock_http(monkeypatch, forbidden)
    assert await EmbeddingClient(embedding_settings()).embed([]) == []


async def test_stub_embedding_is_deterministic_finite_normalized_and_honest():
    client = EmbeddingClient(Settings())
    first = await client.embed(["Python 开发", ""])
    assert first == await client.embed(["Python 开发", ""])
    assert client.is_stub is True
    assert client.model_key.startswith("stub:")
    assert all(len(vector) == DIMENSIONS for vector in first)
    assert all(all(math.isfinite(value) for value in vector) for vector in first)
    assert all(sum(value * value for value in vector) == pytest.approx(1) for vector in first)


def test_embedding_cache_identity_changes_with_model_or_endpoint():
    base = EmbeddingClient(embedding_settings()).model_key
    assert EmbeddingClient(embedding_settings(embedding_model="another-model")).model_key != base
    changed = embedding_settings().model_copy(
        update={"embedding_base_url": "https://different.invalid/v1"}
    )
    assert EmbeddingClient(changed).model_key != base
