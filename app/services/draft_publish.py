"""草稿确认：确认瞬间封存为独立账册，单一落库，下游只读物化文件。

规则：
- 确认动作整笔事务完成；失败抛中文 ServiceError。
- 同一条边只许确认一次：账册 UNIQUE + 进程内串行闸，拒绝重复刷写。
- 账册记录确认瞬间边号、命运（封草稿/准发布）与状态指纹；
  指纹与发布态不合时，解析入库闸一律拦截。
- 封草稿边不进下游可达集合；物化文件只供下游只读，不回写库内命运。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path

from app.config import Config
from app.db import get_db
from app.services.errors import ServiceError

_OK = "草稿已确认"
_BAD = "草稿仍进"
_TITLE = "草稿当已发布"
FATE_SEAL = "封草稿"
FATE_PUBLISH = "准发布"
_FATES = (FATE_SEAL, FATE_PUBLISH)

# 确认动作串行闸：两名值班同时确认同一边时，后到者整笔被拒，禁止双写账册
_commit_lock = threading.Lock()


# ---------- 指纹 ----------

def edge_fingerprint(edge: dict) -> str:
    """确认瞬间的边状态指纹：边号/上下游/状态任一漂移即不合。"""
    raw = "|".join(
        str(edge.get(k, ""))
        for k in ("edge_code", "upstream_id", "downstream_id", "status")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_edge(conn, edge_id: int) -> dict:
    edge = conn.execute(
        "SELECT * FROM lineage_edges WHERE id=?", (edge_id,)
    ).fetchone()
    if not edge:
        raise ServiceError("边不存在", 404)
    return dict(edge)


# ---------- 物化文件（下游只读） ----------

def mirror_path(db_path: str | None = None) -> Path:
    base = Path(db_path or Config.database()).parent
    return base / "draft_publish_mirror.json"


def write_mirror(conn, db_path: str | None = None) -> Path:
    """由账册物化只读文件：库内账册是唯一落库真相，文件只供下游读取。

    只读是消费契约（read_mirror 不提供任何写入口），不给文件挂 0o444，
    否则下一次整笔确认的原子替换会因权限失败、拖垮同事务连接。
    """
    rows = conn.execute(
        "SELECT edge_id, edge_code, fate, fingerprint, edge_status, confirmed_at "
        "FROM edge_confirm_ledger ORDER BY id"
    ).fetchall()
    payload = {
        "title": _TITLE,
        "read_only": True,
        "count": len(rows),
        "items": [dict(r) for r in rows],
    }
    path = mirror_path(db_path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def read_mirror(db_path: str | None = None) -> dict:
    path = mirror_path(db_path)
    if not path.exists():
        return {"title": _TITLE, "read_only": True, "count": 0, "items": []}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------- 确认（整笔事务 + 串行闸） ----------

def confirm_edge(
    edge_id: int,
    fate: str = FATE_SEAL,
    operator: str = "",
    note: str = "",
    db_path: str | None = None,
) -> dict:
    """确认一条草稿边。整笔完成：账册落一行命运 + 物化文件刷新，二者同成同败。"""
    fate = (fate or FATE_SEAL).strip()
    if fate not in _FATES:
        raise ServiceError("命运只许「封草稿」或「准发布」")
    with _commit_lock:
        with get_db(db_path) as conn:
            edge = _load_edge(conn, edge_id)
            if edge["status"] != "草稿":
                raise ServiceError("仅草稿边可确认，当前状态：%s" % edge["status"], 409)
            dup = conn.execute(
                "SELECT id FROM edge_confirm_ledger WHERE edge_id=?", (edge_id,)
            ).fetchone()
            if dup:
                raise ServiceError("该边确认瞬间已封存，账册拒再刷写", 409)
            # 1) 先兑现命运：准发布在同事务内改为已发布，封草稿保持草稿
            if fate == FATE_PUBLISH:
                for nid in (edge["upstream_id"], edge["downstream_id"]):
                    node = conn.execute(
                        "SELECT status FROM lineage_nodes WHERE id=?", (nid,)
                    ).fetchone()
                    if not node or node["status"] == "停用":
                        raise ServiceError("上下游须有效，确认整笔回滚", 409)
                cur = conn.execute(
                    "UPDATE lineage_edges SET status='已发布' "
                    "WHERE id=? AND status='草稿'",
                    (edge_id,),
                )
                if cur.rowcount != 1:
                    raise ServiceError("准发布落库失败，确认整笔回滚", 409)
            # 封草稿：lineage_edges 状态保持「草稿」，不进已发布可达集合
            # 2) 按确认后的最终状态取指纹并落账册，保证指纹与发布态天然一致
            final_edge = _load_edge(conn, edge_id)
            fp = edge_fingerprint(final_edge)
            try:
                conn.execute(
                    "INSERT INTO edge_confirm_ledger(edge_id, edge_code, fate, "
                    "fingerprint, edge_status, operator, note) VALUES (?,?,?,?,?,?,?)",
                    (
                        edge_id,
                        final_edge["edge_code"],
                        fate,
                        fp,
                        final_edge["status"],
                        operator or "",
                        note or "",
                    ),
                )
            except sqlite3.IntegrityError as exc:
                # UNIQUE 兜底：即使串行闸失效也不许双写账册
                raise ServiceError("该边确认瞬间已封存，账册拒再刷写", 409) from exc
            # 3) 物化只读文件，与账册同事务成败
            write_mirror(conn, db_path)
            row = conn.execute(
                "SELECT * FROM edge_confirm_ledger WHERE edge_id=?", (edge_id,)
            ).fetchone()
            return dict(row)


# ---------- 解析入库闸：指纹与发布态不合即拦 ----------

def gate_status(db_path: str | None = None) -> dict:
    """逐条核对账册指纹与当前发布态；有一条不合即禁止解析继续入库。"""
    with get_db(db_path) as conn:
        rows = conn.execute("SELECT * FROM edge_confirm_ledger ORDER BY id").fetchall()
        violations: list[str] = []
        warnings: list[str] = []
        for r in rows:
            edge = conn.execute(
                "SELECT * FROM lineage_edges WHERE id=?", (r["edge_id"],)
            ).fetchone()
            if edge is None:
                violations.append("边号 %s 已被删除，账册指纹失锚" % r["edge_code"])
                continue
            edge = dict(edge)
            if edge_fingerprint(edge) != r["fingerprint"]:
                violations.append(
                    "边号 %s 状态已漂移（%s），与确认指纹不合"
                    % (r["edge_code"], edge["status"])
                )
                continue
            if r["fate"] == FATE_SEAL and edge["status"] != "草稿":
                violations.append(
                    "边号 %s 命运动封草稿却出现发布态 %s"
                    % (r["edge_code"], edge["status"])
                )
            if r["fate"] == FATE_PUBLISH and edge["status"] != "已发布":
                violations.append(
                    "边号 %s 命运准发布却未到已发布态（%s）"
                    % (r["edge_code"], edge["status"])
                )
        # 未确认草稿只告警不拦截；它们永不进入已发布可达集合（BFS 只认已发布）。
        # 真正拦截的是账册内已确认边的指纹/命运漂移，双轨无处可藏。
        pending = conn.execute(
            "SELECT edge_code FROM lineage_edges WHERE status='草稿' "
            "AND id NOT IN (SELECT edge_id FROM edge_confirm_ledger)"
        ).fetchall()
        if pending:
            warnings.append(
                "另有 %d 条未确认草稿边不进可达集合，待确认命运" % len(pending)
            )
        return {
            "allowed": not violations,
            "violations": violations,
            "warnings": warnings,
            "ledger_count": len(rows),
        }


def gate_allows_pipeline(db_path: str | None = None) -> bool:
    return gate_status(db_path)["allowed"]


def require_pack_or_raise(db_path: str | None = None) -> None:
    """解析入库前调用：账册缺命运行或指纹不合，直接中文拦截。"""
    status = gate_status(db_path)
    if not status["allowed"]:
        raise ServiceError(
            "草稿指纹与发布态不合，已拦住解析入库：" + "；".join(status["violations"]),
            409,
        )


# ---------- 台面对账 ----------

def ledger_rows(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM edge_confirm_ledger ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def unpublished_count(db_path: str | None = None) -> int:
    """未发布计数：状态仍为草稿的边（含已封草稿），不进下游可达集合。"""
    with get_db(db_path) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM lineage_edges WHERE status='草稿'"
            ).fetchone()["c"]
        )


def _panel_chip(conn) -> str:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM edge_confirm_ledger"
    ).fetchone()
    return _OK if int(row["c"]) > 0 else _BAD


def panel_chip(db_path: str | None = None) -> str:
    with get_db(db_path) as conn:
        return _panel_chip(conn)


def truth_flag(db_path: str | None = None) -> str:
    """库内真相：账册有命运行且指纹闸全过才算已确认，不看展示芯片。"""
    with get_db(db_path) as conn:
        total = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM edge_confirm_ledger"
            ).fetchone()["c"]
        )
        if total == 0:
            return _BAD
    return _OK if gate_status(db_path)["allowed"] else _BAD


def snapshot(db_path: str | None = None) -> dict:
    gate = gate_status(db_path)
    rows = ledger_rows(db_path)
    chip = panel_chip(db_path)
    truth = truth_flag(db_path)
    return {
        "title": _TITLE,
        "panel_chip": chip,
        "truth": truth,
        "pack_count": len(rows),
        "durable_count": len(rows),
        "unpublished_count": unpublished_count(db_path),
        "aligned": chip == truth == _OK and len(rows) > 0 and gate["allowed"],
        "gate_bypass": False,
        "gate": gate,
        "rows": rows,
        "mirror_file": str(mirror_path(db_path)),
    }


def dashboard(db_path: str | None = None) -> dict:
    snap = snapshot(db_path)
    with get_db(db_path) as conn:
        edges = conn.execute(
            "SELECT id, edge_code, status FROM lineage_edges ORDER BY id"
        ).fetchall()
        ledger = {
            int(r["edge_id"]): dict(r)
            for r in conn.execute("SELECT * FROM edge_confirm_ledger").fetchall()
        }
    edge_view = []
    for e in edges:
        rec = ledger.get(int(e["id"]))
        edge_view.append(
            {
                "edge_id": int(e["id"]),
                "edge_code": e["edge_code"],
                "status": e["status"],
                "draft_source": e["status"] == "草稿",
                "fate": rec["fate"] if rec else None,
                "confirmed": rec is not None,
            }
        )
    return {
        "title": _TITLE,
        "chip": snap["panel_chip"],
        "truth": snap["truth"],
        "pack_count": snap["pack_count"],
        "durable_count": snap["durable_count"],
        "unpublished_count": snap["unpublished_count"],
        "rows": snap["rows"],
        "edges": edge_view,
        "panel_pack_mismatch": snap["panel_chip"] == _OK and (
            snap["truth"] != _OK or snap["durable_count"] == 0
        ),
        "gate": snap["gate"],
        "ok_chip": _OK,
        "bad_chip": _BAD,
        "label": "草稿确认",
    }


def board(db_path: str | None = None) -> dict:
    return dashboard(db_path)


# ---------- 兼容旧入口：半修只许碰展示，永远写不进命运行 ----------

def paint_ok(db_path: str | None = None) -> str:
    """展示芯片由账册派生，无账册行时半修也亮不了绿。"""
    return panel_chip(db_path)


def half_fix_display(db_path: str | None = None) -> dict:
    return {
        "panel_chip": panel_chip(db_path),
        "truth": truth_flag(db_path),
        "pack_count": len(ledger_rows(db_path)),
        "half_fixed": True,
        "note": "半修只碰展示；无命运行台面与库内仍不对齐",
    }


def reconcile_display(db_path: str | None = None) -> dict:
    return half_fix_display(db_path)


def commit_action(payload: dict | None = None, db_path: str | None = None) -> dict:
    """旧落台入口：必须带边号与命运，转入整笔确认；不再只刷假芯片。"""
    payload = payload or {}
    edge_id = int(payload.get("edge_id") or payload.get("ref_id") or 0)
    if edge_id <= 0:
        raise ServiceError("缺少待确认边号，无法落命运行")
    fate = str(payload.get("fate") or FATE_SEAL)
    rec = confirm_edge(
        edge_id,
        fate=fate,
        operator=str(payload.get("operator") or ""),
        note=str(payload.get("note") or ""),
        db_path=db_path,
    )
    return {
        "ok": True,
        "chip": panel_chip(db_path),
        "title": _TITLE,
        "item": rec,
        "pack_count": len(ledger_rows(db_path)),
        "unpublished_count": unpublished_count(db_path),
        "message": "边号 %s 已按命运「%s」整笔封存" % (rec["edge_code"], rec["fate"]),
    }
