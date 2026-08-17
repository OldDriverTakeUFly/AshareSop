"""champions 冠军存档测试。"""

from __future__ import annotations

from datetime import date

import pytest

from davis_analyzer.tournament.champions import (
    ChampionRecord,
    incumbents,
    promote_champion,
    render_deploy_note,
    verify_sync,
)
from davis_analyzer.tournament.ledger import ensure_tables as ensure_ledger


def _rec(gen: int, params: dict | None = None) -> ChampionRecord:
    return ChampionRecord(
        champion_id=f"ch-{gen}", participant="davis_balanced", regime="risk_on",
        params=params or {"momentum_weight": round(0.2 + 0.1 * gen, 2)},
        version=f"v{gen}", generation=gen,
        evidence={"win_rate": 0.7, "median": 0.3, "p25": 0.1, "decay": 0.1,
                  "finals_pass": True},
        promoted_at=date(2025, 1, gen + 1), oos_consumed=1, is_incumbent=True,
    )


@pytest.fixture
def db():
    import sqlite3
    from davis_analyzer.tournament.champions import CHAMPIONS_DDL
    conn = sqlite3.connect(":memory:")
    ensure_ledger(conn)
    conn.executescript(CHAMPIONS_DDL)
    conn.commit()
    yield conn
    conn.close()


def test_slot_cap_two_history_plus_incumbent(db) -> None:
    for gen in range(5):
        promote_champion(db, _rec(gen))
    rows = db.execute(
        "SELECT COUNT(*) FROM tournament_champions WHERE participant='davis_balanced' "
        "AND regime='risk_on'"
    ).fetchone()[0]
    assert rows == 3  # 2 历史 + 1 现任


def test_incumbent_marking(db) -> None:
    promote_champion(db, _rec(1))
    inc = incumbents(db)
    assert len(inc) == 1 and inc[0].generation == 1


def test_verify_sync_detects_mismatch(db) -> None:
    promote_champion(db, _rec(1, params={"momentum_weight": 0.31}))
    problems = verify_sync(db, {"davis_balanced": {"momentum_weight": 0.31}})
    assert problems == []
    problems = verify_sync(db, {"davis_balanced": {"momentum_weight": 0.99}})
    assert problems and "davis_balanced" in problems[0]


def test_promote_from_ledger(db) -> None:
    from davis_analyzer.tournament.champions import promote_from_ledger
    from davis_analyzer.tournament.ledger import LedgerRecord, append_record

    append_record(db, LedgerRecord(
        op_type="evolve", run_date=date(2025, 1, 1),
        participants=[("davis_balanced", "v1")], params_version="campaign-x",
        oos_windows_used=20,
        detail={"ok": True, "best_params": {"momentum_weight": 0.35},
                "improvements": [0.1], "decay": 0.1, "finals_pass": True, "reasons": []},
    ))
    rec = promote_from_ledger(db)
    assert rec is not None and rec.participant == "davis_balanced"
    assert any(c.params == {"momentum_weight": 0.35} for c in incumbents(db))
    assert promote_from_ledger(db) is not None  # 幂等：第二次晋升最新同一条


def test_deploy_note_renders(db) -> None:
    promote_champion(db, _rec(1))
    text = render_deploy_note(incumbents(db))
    assert "CHAMPION_PRESETS" in text and "SOP" in text
