"""HTTP 端到端：登录 → 建边 → 两种命运确认 → 闸口 → 影响分析。"""
from __future__ import annotations

import httpx

from app.config import Config
from app.main import create_app


def _client(app=None):
    transport = httpx.WSGITransport(app=app or create_app())
    return httpx.Client(transport=transport, base_url="http://lineage"), transport.app


def _login(client):
    r = client.post(
        "/api/login",
        json={"username": Config.ADMIN_USER, "password": Config.ADMIN_PASSWORD},
    )
    assert r.status_code == 200


def test_full_draft_confirm_flow_over_http(db_path):
    client, app = _client()
    _login(client)

    nodes = client.get("/api/nodes").json()["items"]
    # 选未占用端点对（种子边均不含该组合）
    up = next(n for n in nodes if n["node_code"] == "N-DWD-ORD")
    down = next(n for n in nodes if n["node_code"] == "N-ADS-ALERT")

    created = client.post(
        "/api/edges",
        json={
            "edge_code": "E-HTTP-1",
            "upstream_id": up["id"],
            "downstream_id": down["id"],
            "transform_kind": "直抽",
        },
    ).json()["item"]
    assert created["status"] == "草稿"

    # 封草稿确认
    ok = client.post(
        f"/api/edges/{created['id']}/confirm",
        json={"fate": "封草稿"},
    )
    assert ok.status_code == 200
    assert ok.json()["item"]["fate"] == "封草稿"

    # 重复确认被拒，中文原因
    again = client.post(
        f"/api/edges/{created['id']}/confirm",
        json={"fate": "封草稿"},
    )
    assert again.status_code == 409 and "拒再刷写" in again.json()["error"]

    # 台面对账：边列表亮草稿来源，账册有对应命运行
    dash = client.get("/api/draft_publish/dash").json()
    edge_row = next(e for e in dash["edges"] if e["edge_code"] == "E-HTTP-1")
    assert edge_row["draft_source"] is True and edge_row["fate"] == "封草稿"
    assert any(r["edge_id"] == created["id"] for r in dash["ledger"])
    assert dash["unpublished_count"] >= 1

    # 影响分析亮未发布计数，且封草稿边不进可达集合
    started = client.post(
        "/api/impact/start", json={"root_node_id": up["id"]}
    ).json()["job"]
    assert started["status"] == "成功"
    assert started["unpublished_count"] >= 1
    jobs = client.get("/api/impact/jobs").json()["items"]
    assert jobs and all("unpublished_count" in j for j in jobs)

    # 闸口状态可查
    gate = client.get("/api/draft_publish/gate").json()
    assert gate["allowed"] is True

    # 半修只碰展示，不新增命运行
    before = len(dash["ledger"])
    half = client.post("/api/draft_publish/half").json()
    assert half["pack_count"] == before

    # 未登录请求被拒（同 app、无 cookie 的新客户端）
    anon = httpx.Client(
        transport=httpx.WSGITransport(app=app),
        base_url="http://lineage",
    )
    assert anon.get("/api/edges").status_code == 401
