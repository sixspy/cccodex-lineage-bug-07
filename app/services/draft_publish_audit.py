"""草稿确认审计：以独立账册行作为审计事件，展示无账册则无事件。"""
from __future__ import annotations

from app.services.draft_publish import ledger_rows
from app.services.draft_publish_mirror import mirror_board


def audit_list(db_path: str | None = None) -> list[dict]:
    return [
        {
            "key": "edge_confirm_ledger:%d" % r["id"],
            "value": "%s｜%s｜%s" % (r["edge_code"], r["fate"], r["confirmed_at"]),
        }
        for r in ledger_rows(db_path)
    ]


def audit_summary(db_path: str | None = None) -> dict:
    board = mirror_board(db_path)
    return {
        "title": "草稿当已发布审计",
        "events": audit_list(db_path),
        "board": board,
        "half": board["mirror_chip"],
    }
