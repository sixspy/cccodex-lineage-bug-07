"""血缘边：创建、发布、作废。"""
from __future__ import annotations

from app.db import get_db
from app.services.draft_publish import FATE_PUBLISH, FATE_SEAL
from app.services.errors import ServiceError


def list_edges(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT e.*, u.node_code AS upstream_code, u.table_name AS upstream_table, "
            "d.node_code AS downstream_code, d.table_name AS downstream_table, "
            "l.fate AS confirm_fate, l.fingerprint AS confirm_fingerprint "
            "FROM lineage_edges e "
            "JOIN lineage_nodes u ON u.id=e.upstream_id "
            "JOIN lineage_nodes d ON d.id=e.downstream_id "
            "LEFT JOIN edge_confirm_ledger l ON l.edge_id=e.id "
            "ORDER BY e.id"
        ).fetchall()
        items = [dict(r) for r in rows]
        for it in items:
            # 草稿来源：凡仍是草稿态（含已封草稿）都亮明，不允许冒充已发布
            it["draft_source"] = it["status"] == "草稿"
            it["confirmed"] = it["confirm_fate"] is not None
            if it["confirm_fate"] == FATE_SEAL:
                it["fate_label"] = "已封草稿·不进可达集合"
            elif it["confirm_fate"] == FATE_PUBLISH:
                it["fate_label"] = "准发布·已兑现"
            else:
                it["fate_label"] = "未确认"
        return items


def create_edge(
    edge_code: str,
    upstream_id: int,
    downstream_id: int,
    transform_kind: str = "直抽",
    note: str = "",
    db_path: str | None = None,
) -> dict:
    edge_code = (edge_code or "").strip()
    if not edge_code:
        raise ServiceError("边编号必填")
    if upstream_id == downstream_id:
        raise ServiceError("上下游不能是同一节点")
    with get_db(db_path) as conn:
        up = conn.execute(
            "SELECT * FROM lineage_nodes WHERE id=?", (upstream_id,)
        ).fetchone()
        down = conn.execute(
            "SELECT * FROM lineage_nodes WHERE id=?", (downstream_id,)
        ).fetchone()
        if not up or not down:
            raise ServiceError("上下游节点不存在", 404)
        if up["status"] == "停用" or down["status"] == "停用":
            raise ServiceError("停用节点不可建边", 409)
        try:
            cur = conn.execute(
                "INSERT INTO lineage_edges(edge_code, upstream_id, downstream_id, "
                "transform_kind, status, note) VALUES (?,?,?,?,'草稿',?)",
                (
                    edge_code,
                    upstream_id,
                    downstream_id,
                    transform_kind or "直抽",
                    note or "",
                ),
            )
        except Exception as exc:
            raise ServiceError(f"建边失败：{exc}") from exc
        return dict(
            conn.execute(
                "SELECT * FROM lineage_edges WHERE id=?", (cur.lastrowid,)
            ).fetchone()
        )


def publish_edge(edge_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        edge = conn.execute(
            "SELECT * FROM lineage_edges WHERE id=?", (edge_id,)
        ).fetchone()
        if not edge:
            raise ServiceError("边不存在", 404)
        if edge["status"] != "草稿":
            raise ServiceError("仅草稿可发布", 409)
        sealed = conn.execute(
            "SELECT fate FROM edge_confirm_ledger WHERE edge_id=?", (edge_id,)
        ).fetchone()
        if sealed and sealed["fate"] == FATE_SEAL:
            raise ServiceError("该边已按命运封草稿，禁止改道发布形成双轨", 409)
        for nid in (edge["upstream_id"], edge["downstream_id"]):
            node = conn.execute(
                "SELECT status FROM lineage_nodes WHERE id=?", (nid,)
            ).fetchone()
            if not node or node["status"] == "停用":
                raise ServiceError("上下游须有效", 409)
            if node["status"] == "登记":
                conn.execute(
                    "UPDATE lineage_nodes SET status='在用' WHERE id=?", (nid,)
                )
        conn.execute(
            "UPDATE lineage_edges SET status='已发布' WHERE id=?", (edge_id,)
        )
        return dict(
            conn.execute(
                "SELECT * FROM lineage_edges WHERE id=?", (edge_id,)
            ).fetchone()
        )


def void_edge(edge_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        edge = conn.execute(
            "SELECT * FROM lineage_edges WHERE id=?", (edge_id,)
        ).fetchone()
        if not edge:
            raise ServiceError("边不存在", 404)
        if edge["status"] == "已作废":
            raise ServiceError("边已作废", 409)
        conn.execute(
            "UPDATE lineage_edges SET status='已作废' WHERE id=?", (edge_id,)
        )
        return dict(
            conn.execute(
                "SELECT * FROM lineage_edges WHERE id=?", (edge_id,)
            ).fetchone()
        )
