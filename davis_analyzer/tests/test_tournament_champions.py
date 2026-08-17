"""champions 冠军存档测试。"""

from __future__ import annotations

from datetime import date

from davis_analyzer.tournament.champions import (
    ChampionRecord,
    incumbents,
    promote_champion,
    render_deploy_note,
    verify_sync,
)


def _rec(gen: int, params: dict | None = None) -> ChampionRecord:
    return ChampionRecord(
        champion_id=f"ch-{gen}", participant="davis_balanced", regime="risk_on",
        params=params or {"momentum_weight": round(0.2 + 0.1 * gen, 2)},
        version=f"v{gen}", generation=gen,
        evidence={"win_rate": 0.7, "median": 0.3, "p25": 0.1, "decay": 0.1,
                  "finals_pass": True},
        promoted_at=date(2025, 1, gen + 1), oos_consumed=1, is_incumbent=True,
    )


def test_slot_cap_two_history_plus_incumbent(tournament_db) -> None:
    for gen in range(5):
        promote_champion(tournament_db, _rec(gen))
    rows = tournament_db.execute(
        "SELECT COUNT(*) FROM tournament_champions WHERE participant='davis_balanced' "
        "AND regime='risk_on'"
    ).fetchone()[0]
    assert rows == 3  # 2 历史 + 1 现任


def test_incumbent_marking(tournament_db) -> None:
    promote_champion(tournament_db, _rec(1))
    inc = incumbents(tournament_db)
    assert len(inc) == 1 and inc[0].generation == 1


def test_verify_sync_detects_mismatch(tournament_db) -> None:
    promote_champion(tournament_db, _rec(1, params={"momentum_weight": 0.31}))
    problems = verify_sync(tournament_db, {"davis_balanced": {"momentum_weight": 0.31}})
    assert problems == []
    problems = verify_sync(tournament_db, {"davis_balanced": {"momentum_weight": 0.99}})
    assert problems and "davis_balanced" in problems[0]


def test_verify_sync_numeric_normalization(tournament_db) -> None:
    # M5：int/float 表示差异（10 vs 10.0）归一后视为一致，不再误报
    promote_champion(tournament_db, _rec(1, params={"momentum_weight": 0.31, "top_n": 10.0}))
    problems = verify_sync(
        tournament_db, {"davis_balanced": {"momentum_weight": 0.31, "top_n": 10}},
    )
    assert problems == []


def test_promote_from_ledger(tournament_db) -> None:
    from davis_analyzer.tournament.champions import promote_from_ledger
    from davis_analyzer.tournament.ledger import LedgerRecord, append_record

    append_record(tournament_db, LedgerRecord(
        op_type="evolve", run_date=date(2025, 1, 1),
        participants=[("davis_balanced", "v1")], params_version="campaign-x",
        oos_windows_used=20,
        detail={"ok": True, "best_params": {"momentum_weight": 0.35},
                "improvements": [0.1], "decay": 0.1, "finals_pass": True, "reasons": []},
    ))
    rec = promote_from_ledger(tournament_db)
    assert rec is not None and rec.participant == "davis_balanced"
    assert any(c.params == {"momentum_weight": 0.35} for c in incumbents(tournament_db))
    assert promote_from_ledger(tournament_db) is not None  # 幂等：第二次晋升最新同一条


def test_promote_generation_uses_max_not_count(tournament_db) -> None:
    # M6：代数取 MAX(generation)+1——历史冠军被满槽淘汰后 COUNT 会饱和复用代数
    from dataclasses import replace

    from davis_analyzer.tournament.champions import promote_from_ledger
    from davis_analyzer.tournament.ledger import LedgerRecord, append_record

    promote_champion(tournament_db, replace(_rec(7), regime="all"))  # promote 的 regime 恒为 all
    append_record(tournament_db, LedgerRecord(
        op_type="evolve", run_date=date(2025, 6, 1),
        participants=[("davis_balanced", "v1")], params_version="campaign-y",
        oos_windows_used=20,
        detail={"ok": True, "best_params": {"momentum_weight": 0.42},
                "improvements": [0.1], "decay": 0.1, "finals_pass": True, "reasons": []},
    ))
    rec = promote_from_ledger(tournament_db)
    assert rec is not None and rec.generation == 8  # MAX(7)+1，而非 COUNT(1)+1=2
    assert rec.version == "gen8"


def test_deploy_note_renders(tournament_db) -> None:
    promote_champion(tournament_db, _rec(1))
    text = render_deploy_note(incumbents(tournament_db))
    assert "CHAMPION_PRESETS" in text and "SOP" in text
