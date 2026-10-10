"""1536 维 OpenAI 兼容 embeddings；无 key 默认用明确标记的哈希桩。"""

import hashlib
import math
import re

import httpx

from app.config import Settings
from app.llm.client import LlmError

DIMENSIONS = 1536


class EmbeddingClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def is_stub(self) -> bool:
        return self.settings.embedding_provider == "stub"

    @property
    def model_key(self) -> str:
        if self.is_stub:
            return "stub:hash-v1:1536"
        identity = f"{self.settings.embedding_base_url.rstrip('/')}:{self.settings.embedding_model}"
        return "openai-compatible:" + hashlib.sha256(identity.encode()).hexdigest() + ":1536"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.is_stub:
            return [self._hash_vector(text) for text in texts]
        settings = self.settings
        if not settings.embedding_api_key:
            raise LlmError("EMBEDDING_PROVIDER=openai-compatible 需要 EMBEDDING_API_KEY")
        try:
            async with httpx.AsyncClient(timeout=settings.embedding_timeout_s) as client:
                response = await client.post(
                    f"{settings.embedding_base_url.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
                    json={
                        "model": settings.embedding_model,
                        "input": texts,
                        "dimensions": DIMENSIONS,
                        "encoding_format": "float",
                    },
                )
                response.raise_for_status()
                payload = response.json()
            records = sorted(payload["data"], key=lambda item: item["index"])
            if [item["index"] for item in records] != list(range(len(texts))):
                raise ValueError("向量条数或 index 不符")
            vectors = [[float(x) for x in item["embedding"]] for item in records]
            for vector in vectors:
                if len(vector) != DIMENSIONS or not all(math.isfinite(x) for x in vector):
                    raise ValueError("向量必须为 1536 维有限数值")
                if not any(vector):
                    raise ValueError("不接受零向量")
            return vectors
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise LlmError(
                f"嵌入服务调用失败：{type(exc).__name__}；请检查配置和 1536 维支持"
            ) from exc

    @staticmethod
    def _hash_vector(text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        tokens = re.findall(r"[a-z0-9+#.]+|[\u4e00-\u9fff]", text.lower())
        for token in tokens or [text or "empty"]:
            digest = hashlib.sha256(token.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % DIMENSIONS] += 1.0
        norm = math.sqrt(sum(x * x for x in vector))
        return [x / norm for x in vector]


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(str(x) for x in vector) + "]"
