"""草稿确认独立账册：确认瞬间边号与命运的唯一落库点，拒再刷写。"""
from __future__ import annotations

from app.db import get_db
from app.services.draft_publish import (
    _TITLE,
    confirm_edge,
    dashboard,
    ledger_rows,
    panel_chip,
    truth_flag,
)


def append_ledger(ref_id: int, note: str, db_path: str | None = None) -> dict:
    """旧旁路入口：必须落到一条真实命运行；只写芯片不写命运行一律不认。"""
    if int(ref_id) <= 0:
        from app.services.errors import ServiceError

        raise ServiceError("缺少边号，无法记命运行")
    rec = confirm_edge(ref_id, note=note or _TITLE, db_path=db_path)
    return {
        "ref_id": ref_id,
        "note": note,
        "panel": panel_chip(db_path),
        "truth": truth_flag(db_path),
        "item": rec,
    }


def board(db_path: str | None = None) -> dict:
    dash = dashboard(db_path)
    dash["ledger"] = ledger_rows(db_path)
    return dash
