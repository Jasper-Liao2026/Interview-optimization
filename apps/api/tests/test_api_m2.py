"""M2 的素材库写路径（M2-1 / M2-3 / M2-6）。

覆盖三件事：
  1. **量化结果**（metrics）能被写进去、原样读回来
  2. **多版本表述**（variants）能存多份，且同方向重复被拒
  3. **PUT 是全量替换**而不是合并 —— 这是接口契约里最容易被人误解的一条

排序（分类分组 + 组内时间倒序）是 SQL 行为，假仓储验证不了，
放在 `scripts/verify-m2.mjs` 里对真实数据库做断言。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from tests.fakes import API, DEV_USER

FULL_PAYLOAD: dict[str, Any] = {
    "kind": "internship",
    "org": "某互联网公司",
    "role": "后端实习生",
    "start_date": "2026-06-01",
    "end_date": "2026-08-31",
    "raw_description": "参与订单服务的接口开发与慢查询优化。",
    "skill_tags": ["Python", "PostgreSQL"],
    "highlights": ["负责订单查询接口的重构"],
    "metrics": [
        {"name": "订单查询接口 P99 延迟", "value": "800ms → 120ms", "context": "压测 5000 QPS 下"},
        {"name": "慢查询", "value": "7 条 → 0 条", "context": None},
    ],
    "variants": [
        {
            "direction": "后端开发",
            "text": "重构订单查询链路，P99 从 800ms 降到 120ms。",
            "note": "投后端岗",
        },
        {"direction": "数据工程", "text": "优化索引设计与慢查询治理。", "note": None},
    ],
}


def _first_id(wiring: dict[str, Any]) -> UUID:
    return wiring["experiences"].rows[0]["id"]


# ------------------------------------------------------- M2-1 量化结果 / 子结构
async def test_create_persists_metrics_and_variants(client, api_wiring: dict[str, Any]) -> None:
    response = await client.post(f"{API}/experiences", json=FULL_PAYLOAD)

    assert response.status_code == 201
    body = response.json()

    # 原样往返：数值写法（含箭头与空格）不得被规整，否则 M4-7 的数字校验会失去参照物
    assert body["metrics"] == FULL_PAYLOAD["metrics"]
    assert body["variants"] == FULL_PAYLOAD["variants"]
    # 旧的 highlights 仍在，且与 metrics 分开存放
    assert body["highlights"] == ["负责订单查询接口的重构"]


async def test_create_without_substructures_defaults_to_empty_lists(
    client, api_wiring: dict[str, Any]
) -> None:
    """M1 时代的老客户端（不传 metrics / variants）必须还能用。"""
    response = await client.post(
        f"{API}/experiences",
        json={
            "kind": "project",
            "org": "旧客户端项目",
            "role": "开发",
            "raw_description": "只传 M1 时代就有的字段。",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["metrics"] == []
    assert body["variants"] == []


# --------------------------------------------------------- M2-6 多版本表述约束
async def test_create_rejects_duplicate_variant_direction(
    client, api_wiring: dict[str, Any]
) -> None:
    """同一条经历、同一岗位方向只允许一份表述 —— 在提交时就报 422，不静默存两份。"""
    payload = {
        **FULL_PAYLOAD,
        "variants": [
            {"direction": "后端开发", "text": "第一份"},
            {"direction": "后端开发", "text": "第二份"},
        ],
    }

    response = await client.post(f"{API}/experiences", json=payload)

    assert response.status_code == 422
    assert "重复" in response.text


async def test_create_allows_same_direction_on_different_experiences(
    client, api_wiring: dict[str, Any]
) -> None:
    """唯一性只在**单条经历内**成立：两条不同经历当然可以都有「后端开发」版本。"""
    first = await client.post(f"{API}/experiences", json=FULL_PAYLOAD)
    second = await client.post(f"{API}/experiences", json=FULL_PAYLOAD)

    assert first.status_code == 201
    assert second.status_code == 201


# ------------------------------------------------------------- M2-1 时间区间校验
async def test_create_rejects_end_before_start(client, api_wiring: dict[str, Any]) -> None:
    """填错日期是用户侧问题，应当在提交时得到 422，而不是等到数据库 check 约束抛 500。"""
    response = await client.post(
        f"{API}/experiences",
        json={**FULL_PAYLOAD, "start_date": "2026-08-31", "end_date": "2026-06-01"},
    )

    assert response.status_code == 422
    assert "结束时间不能早于开始时间" in response.text


# ------------------------------------------------------------------ M2-3 PUT
async def test_update_replaces_the_experience(client, api_wiring: dict[str, Any]) -> None:
    target = _first_id(api_wiring)

    response = await client.put(f"{API}/experiences/{target}", json=FULL_PAYLOAD)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(target)
    assert body["org"] == "某互联网公司"
    assert body["metrics"][0]["name"] == "订单查询接口 P99 延迟"
    assert [variant["direction"] for variant in body["variants"]] == ["后端开发", "数据工程"]
    assert api_wiring["experiences"].updated == [target]


async def test_put_is_full_replace_not_merge(client, api_wiring: dict[str, Any]) -> None:
    """PUT 不传的字段等于清空 —— 这是与 PATCH 的关键差别，必须锁住。

    否则将来有人把实现改成「按传入字段合并」，这条测试会立刻失败，
    而不是等用户在编辑表单里发现标签莫名其妙没被删掉。
    """
    target = _first_id(api_wiring)

    response = await client.put(
        f"{API}/experiences/{target}",
        json={
            "kind": "project",
            "org": "换了个组织",
            "role": "换了个角色",
            "raw_description": "只提交这四个必填字段。",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["skill_tags"] == []
    assert body["highlights"] == []
    assert body["metrics"] == []
    assert body["variants"] == []
    assert body["start_date"] is None


async def test_update_ignores_client_supplied_identity(client, api_wiring: dict[str, Any]) -> None:
    """客户端塞进来的 id / user_id 一律不生效：写入对象由路径与服务端当前用户决定。"""
    target = _first_id(api_wiring)
    malicious = {**FULL_PAYLOAD, "id": str(uuid4()), "user_id": str(uuid4())}

    response = await client.put(f"{API}/experiences/{target}", json=malicious)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(target)
    assert body["user_id"] == str(DEV_USER)


async def test_update_unknown_experience_returns_404(client, api_wiring: dict[str, Any]) -> None:
    response = await client.put(f"{API}/experiences/{uuid4()}", json=FULL_PAYLOAD)
    assert response.status_code == 404


async def test_update_rejects_invalid_payload(client, api_wiring: dict[str, Any]) -> None:
    target = _first_id(api_wiring)
    response = await client.put(f"{API}/experiences/{target}", json={**FULL_PAYLOAD, "org": ""})
    assert response.status_code == 422


# ------------------------------------------------------------------ 删除闭环
async def test_delete_then_read_returns_404(client, api_wiring: dict[str, Any]) -> None:
    created = await client.post(f"{API}/experiences", json=FULL_PAYLOAD)
    new_id = created.json()["id"]

    deleted = await client.delete(f"{API}/experiences/{new_id}")
    assert deleted.status_code == 204

    assert (await client.get(f"{API}/experiences/{new_id}")).status_code == 404


async def test_delete_unknown_experience_returns_404(client, api_wiring: dict[str, Any]) -> None:
    response = await client.delete(f"{API}/experiences/{uuid4()}")
    assert response.status_code == 404
