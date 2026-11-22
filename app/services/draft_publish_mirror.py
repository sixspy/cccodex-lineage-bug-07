"""草稿确认镜像面：读库内账册物化成只读文件，不回写、不另立真相。"""
from __future__ import annotations

from app.db import get_db
from app.services.draft_publish import (
    _OK,
    dashboard,
    read_mirror,
    truth_flag,
    write_mirror,
)


def mirror_chip(db_path: str | None = None) -> str:
    snap = read_mirror(db_path)
    return _OK if snap.get("count", 0) > 0 else "无封存"


def seed_mirror(db_path: str | None = None) -> None:
    """按账册重新物化只读文件；不再写假芯片 meta。"""
    with get_db(db_path) as conn:
        write_mirror(conn, db_path)


def mirror_board(db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        write_mirror(conn, db_path)
    dash = dashboard(db_path)
    return {
        "mirror_chip": mirror_chip(db_path),
        "truth": truth_flag(db_path),
        "dash": dash,
        "panel_pack_mismatch": dash["panel_pack_mismatch"],
        "title": "草稿当已发布镜像",
        "mirror": read_mirror(db_path),
    }


def half_mirror(db_path: str | None = None) -> dict:
    # 半修只刷展示，账册无行则镜像无内容
    return {
        "mirror_chip": mirror_chip(db_path),
        "truth": truth_flag(db_path),
    }
