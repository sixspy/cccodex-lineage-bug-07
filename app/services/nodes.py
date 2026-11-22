"""血缘节点：登记、列表、停用。"""
from __future__ import annotations

from app.db import get_db
from app.services.errors import ServiceError


def list_nodes(layer: str | None = None, db_path: str | None = None) -> list[dict]:
    # 防御：调用方误把库路径位置传到 layer 时，归位而不是按层级过滤出空集
    if layer is not None and (
        layer.endswith(".db") or "/" in layer or "\\" in layer
    ):
        db_path, layer = layer, None
    # 芯片与未发布计数在同一连接内一次算好，禁止在连接上下文里再嵌套开连接争锁
    with get_db(db_path) as conn:
        if layer:
            rows = conn.execute(
                "SELECT * FROM lineage_nodes WHERE layer=? ORDER BY id",
                (layer,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM lineage_nodes ORDER BY id"
            ).fetchall()
        ledger_total = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM edge_confirm_ledger"
            ).fetchone()["c"]
        )
        map_chip = "草稿已确认" if ledger_total > 0 else "草稿仍进"
        unpublished = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM lineage_edges WHERE status='草稿'"
            ).fetchone()["c"]
        )
        items = [dict(r) for r in rows]
        for it in items:
            up = conn.execute(
                "SELECT COUNT(*) AS c FROM lineage_edges "
                "WHERE downstream_id=? AND status='已发布'",
                (it["id"],),
            ).fetchone()["c"]
            down = conn.execute(
                "SELECT COUNT(*) AS c FROM lineage_edges "
                "WHERE upstream_id=? AND status='已发布'",
                (it["id"],),
            ).fetchone()["c"]
            it["upstream_count"] = int(up)
            it["downstream_count"] = int(down)
            it["map_chip"] = map_chip
            it["unpublished_count"] = unpublished
            it["map_fields"] = 0
        return items


def get_node(node_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM lineage_nodes WHERE id=?", (node_id,)
        ).fetchone()
        if not row:
            raise ServiceError("节点不存在", 404)
        return dict(row)


def register_node(
    node_code: str,
    table_name: str,
    layer: str,
    owner_team: str = "",
    note: str = "",
    db_path: str | None = None,
) -> dict:
    node_code = (node_code or "").strip()
    table_name = (table_name or "").strip()
    layer = (layer or "").strip().upper()
    if not node_code or not table_name:
        raise ServiceError("节点编号与表名必填")
    if layer not in ("ODS", "DWD", "DWS", "ADS"):
        raise ServiceError("层级只许 ODS/DWD/DWS/ADS")
    with get_db(db_path) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO lineage_nodes(node_code, table_name, layer, owner_team, status, note) "
                "VALUES (?,?,?,?,'登记',?)",
                (node_code, table_name, layer, owner_team or "", note or ""),
            )
        except Exception as exc:
            raise ServiceError(f"登记失败：编号可能重复（{exc})") from exc
        return dict(
            conn.execute(
                "SELECT * FROM lineage_nodes WHERE id=?", (cur.lastrowid,)
            ).fetchone()
        )


def activate_node(node_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM lineage_nodes WHERE id=?", (node_id,)
        ).fetchone()
        if not row:
            raise ServiceError("节点不存在", 404)
        if row["status"] == "停用":
            raise ServiceError("已停用节点不可直接启用", 409)
        conn.execute(
            "UPDATE lineage_nodes SET status='在用' WHERE id=?", (node_id,)
        )
        return dict(
            conn.execute(
                "SELECT * FROM lineage_nodes WHERE id=?", (node_id,)
            ).fetchone()
        )


def retire_node(node_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM lineage_nodes WHERE id=?", (node_id,)
        ).fetchone()
        if not row:
            raise ServiceError("节点不存在", 404)
        live = conn.execute(
            "SELECT COUNT(*) AS c FROM lineage_edges "
            "WHERE (upstream_id=? OR downstream_id=?) AND status='已发布'",
            (node_id, node_id),
        ).fetchone()["c"]
        if int(live) > 0:
            raise ServiceError("仍有已发布边，不可停用", 409)
        conn.execute(
            "UPDATE lineage_nodes SET status='停用' WHERE id=?", (node_id,)
        )
        return dict(
            conn.execute(
                "SELECT * FROM lineage_nodes WHERE id=?", (node_id,)
            ).fetchone()
        )
