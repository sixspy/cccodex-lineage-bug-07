"""lineage-bug-07 验收：草稿确认账册、指纹闸、双轨禁止、并发串行。"""
from __future__ import annotations

import threading

import pytest

from app.services import draft_publish as dp
from app.services import edges, impact, nodes
from app.services.errors import ServiceError


def _draft_edge(db_path, code="E-T-1", up="N-DWD-ORD", down="N-ADS-DASH"):
    ns = {n["node_code"]: n for n in nodes.list_nodes(db_path=db_path)}
    return edges.create_edge(
        code, int(ns[up]["id"]), int(ns[down]["id"]), "直抽", db_path=db_path
    )


def test_confirm_writes_one_ledger_row_and_rejects_refresh(db_path):
    edge = _draft_edge(db_path)
    rec = dp.confirm_edge(int(edge["id"]), dp.FATE_SEAL, db_path=db_path)
    assert rec["edge_code"] == edge["edge_code"]
    assert rec["fate"] == "封草稿"
    rows = dp.ledger_rows(db_path)
    assert len(rows) == 1 and rows[0]["edge_id"] == edge["id"]
    # 拒再刷：同边第二次确认必须失败且不新增行
    with pytest.raises(ServiceError) as exc:
        dp.confirm_edge(int(edge["id"]), dp.FATE_SEAL, db_path=db_path)
    assert "拒再刷写" in exc.value.message
    assert len(dp.ledger_rows(db_path)) == 1


def test_sealed_draft_never_enters_reachable_set(db_path):
    # 新建孤立 ADS 节点：只有这条草稿边能到达它
    target = nodes.register_node("N-ISO-1", "ads_iso_target", "ADS", db_path=db_path)
    ns = {n["node_code"]: n for n in nodes.list_nodes(db_path=db_path)}
    edge = edges.create_edge(
        "E-SEAL-1", int(ns["N-ODS-ORD"]["id"]), int(target["id"]),
        "直抽", db_path=db_path,
    )
    dp.confirm_edge(int(edge["id"]), dp.FATE_SEAL, db_path=db_path)
    listed = next(e for e in edges.list_edges(db_path) if e["id"] == edge["id"])
    assert listed["status"] == "草稿" and listed["draft_source"] is True
    root = next(n for n in nodes.list_nodes(db_path=db_path) if n["node_code"] == "N-ODS-ORD")
    job = impact.start_impact(int(root["id"]), db_path=db_path)
    detail = impact.job_detail(int(job["id"]), db_path)
    hit_ids = {h["node_id"] for h in detail["hits"]}
    assert int(target["id"]) not in hit_ids
    assert job["unpublished_count"] >= 1


def test_fate_choices_produce_different_reachable_sets(db_path):
    # 两个孤立 ADS 节点，各自只靠一条新边与 N-ODS-PAY 相连
    iso_s = nodes.register_node("N-ISO-S", "ads_iso_seal", "ADS", db_path=db_path)
    iso_p = nodes.register_node("N-ISO-P", "ads_iso_pub", "ADS", db_path=db_path)
    ns = {n["node_code"]: n for n in nodes.list_nodes(db_path=db_path)}
    sealed = edges.create_edge(
        "E-FATE-S", int(ns["N-ODS-PAY"]["id"]), int(iso_s["id"]),
        "直抽", db_path=db_path,
    )
    dp.confirm_edge(int(sealed["id"]), dp.FATE_SEAL, db_path=db_path)
    root = next(n for n in nodes.list_nodes(db_path=db_path) if n["node_code"] == "N-ODS-PAY")
    job1 = impact.start_impact(int(root["id"]), db_path)
    hits1 = {h["node_id"] for h in impact.job_detail(int(job1["id"]), db_path)["hits"]}
    assert int(iso_s["id"]) not in hits1

    allowed = edges.create_edge(
        "E-FATE-P", int(ns["N-ODS-PAY"]["id"]), int(iso_p["id"]),
        "直抽", db_path=db_path,
    )
    dp.confirm_edge(int(allowed["id"]), dp.FATE_PUBLISH, db_path=db_path)
    job2 = impact.start_impact(int(root["id"]), db_path)
    hits2 = {h["node_id"] for h in impact.job_detail(int(job2["id"]), db_path)["hits"]}
    assert int(iso_p["id"]) in hits2
    assert int(iso_s["id"]) not in hits2
    assert hits1 != hits2


def test_mirror_file_is_readonly_and_matches_ledger(db_path):
    edge = _draft_edge(db_path, code="E-MIR-1")
    dp.confirm_edge(int(edge["id"]), dp.FATE_SEAL, db_path=db_path)
    mirror = dp.read_mirror(db_path)
    # 只读消费契约：载荷标注 read_only，且只暴露读接口、无写回路径
    assert mirror["read_only"] is True and mirror["count"] == 1
    assert mirror["items"][0]["edge_code"] == edge["edge_code"]
    # 第二次确认仍能原子刷新物化文件（不被只读权限卡住）
    other = _draft_edge(
        db_path, code="E-MIR-2", up="N-DWD-PAY", down="N-ADS-ALERT"
    )
    dp.confirm_edge(int(other["id"]), dp.FATE_SEAL, db_path=db_path)
    assert dp.read_mirror(db_path)["count"] == 2


def test_fingerprint_drift_blocks_pipeline_with_chinese_reason(db_path):
    import sqlite3

    edge = _draft_edge(db_path, code="E-FP-1")
    dp.confirm_edge(int(edge["id"]), dp.FATE_SEAL, db_path=db_path)
    assert dp.gate_status(db_path)["allowed"] is True
    # 直接在库内篡改状态，模拟指纹与发布态不合
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE lineage_edges SET status='已发布' WHERE id=?", (edge["id"],))
    conn.commit()
    conn.close()
    status = dp.gate_status(db_path)
    assert status["allowed"] is False and status["violations"]
    root = next(n for n in nodes.list_nodes(db_path=db_path) if n["node_code"] == "N-DWD-ORD")
    with pytest.raises(ServiceError) as exc:
        impact.start_impact(int(root["id"]), db_path)
    assert "拦住解析入库" in exc.value.message


def test_concurrent_confirm_single_writer_no_double_row(db_path):
    edge = _draft_edge(db_path, code="E-CC-1")
    outcomes = []

    def worker():
        try:
            dp.confirm_edge(int(edge["id"]), dp.FATE_SEAL, db_path=db_path)
            outcomes.append("ok")
        except ServiceError:
            outcomes.append("rejected")

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert sorted(outcomes) == ["ok", "rejected"]
    assert len(dp.ledger_rows(db_path)) == 1


def test_half_fix_display_only_never_writes_fate_row(db_path):
    before = len(dp.ledger_rows(db_path))
    half = dp.reconcile_display(db_path)
    assert len(dp.ledger_rows(db_path)) == before
    # 无命运行时台面不得亮绿
    assert half["truth"] == dp._BAD
    dash = dp.dashboard(db_path)
    assert dash["durable_count"] == 0


def test_panel_matches_table_rows_when_aligned(db_path):
    edge = _draft_edge(db_path, code="E-ALIGN-1")
    dp.confirm_edge(int(edge["id"]), dp.FATE_SEAL, db_path=db_path)
    snap = dp.snapshot(db_path)
    assert snap["aligned"] is True
    assert snap["panel_chip"] == snap["truth"] == dp._OK
    assert snap["pack_count"] == len(dp.ledger_rows(db_path)) == 1


def test_commit_action_requires_edge_id_and_is_atomic(db_path):
    with pytest.raises(ServiceError):
        dp.commit_action({}, db_path=db_path)
    edge = _draft_edge(db_path, code="E-ACT-1")
    out = dp.commit_action(
        {"edge_id": int(edge["id"]), "fate": "封草稿"}, db_path
    )
    assert out["ok"] is True and out["pack_count"] == 1
    with pytest.raises(ServiceError):
        dp.commit_action(
            {"edge_id": int(edge["id"]), "fate": "封草稿"}, db_path
        )


def test_sealed_edge_cannot_be_published_no_dual_track(db_path):
    edge = _draft_edge(db_path, code="E-DUAL-1")
    dp.confirm_edge(int(edge["id"]), dp.FATE_SEAL, db_path=db_path)
    with pytest.raises(ServiceError) as exc:
        edges.publish_edge(int(edge["id"]), db_path)
    assert "双轨" in exc.value.message
    refreshed = next(e for e in edges.list_edges(db_path) if e["id"] == edge["id"])
    assert refreshed["status"] == "草稿"


def test_dashboard_carries_draft_source_and_unpublished_count(db_path):
    edge = _draft_edge(db_path, code="E-DASH-1")
    dp.confirm_edge(int(edge["id"]), dp.FATE_SEAL, db_path=db_path)
    dash = dp.dashboard(db_path)
    row = next(e for e in dash["edges"] if e["edge_id"] == edge["id"])
    assert row["draft_source"] is True and row["fate"] == "封草稿"
    assert dash["unpublished_count"] >= 1
