"""Champion archive — multi-champion hall of fame + deploy sync (spec §5.7)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date

from loguru import logger

from davis_analyzer.constants import TOURNAMENT_CHAMPION_SLOTS

CHAMPIONS_DDL = """
CREATE TABLE IF NOT EXISTS tournament_champions (
    champion_id TEXT PRIMARY KEY,
    participant TEXT NOT NULL,
    regime TEXT NOT NULL,
    params_json TEXT NOT NULL,
    version TEXT NOT NULL,
    generation INTEGER NOT NULL,
    evidence_json TEXT NOT NULL,
    promoted_at TEXT NOT NULL,
    oos_consumed INTEGER NOT NULL DEFAULT 0,
    is_incumbent INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class ChampionRecord:
    champion_id: str
    participant: str
    regime: str
    params: dict[str, float]
    version: str
    generation: int
    evidence: dict
    promoted_at: date
    oos_consumed: int
    is_incumbent: bool


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(CHAMPIONS_DDL)
    conn.commit()


def promote_champion(conn: sqlite3.Connection, rec: ChampionRecord) -> str:
    """Insert a champion and enforce slot caps (2 history + 1 incumbent)."""
    champion_id = rec.champion_id or uuid.uuid4().hex[:12]
    conn.execute(
        "UPDATE tournament_champions SET is_incumbent=0 WHERE participant=? AND regime=?",
        (rec.participant, rec.regime),
    )
    conn.execute(
        "INSERT OR REPLACE INTO tournament_champions VALUES (?,?,?,?,?,?,?,?,?,?)",
        (champion_id, rec.participant, rec.regime,
         json.dumps(rec.params), rec.version, rec.generation,
         json.dumps(rec.evidence, ensure_ascii=False),
         rec.promoted_at.isoformat(), rec.oos_consumed, int(rec.is_incumbent)),
    )
    rows = conn.execute(
        "SELECT champion_id, promoted_at FROM tournament_champions "
        "WHERE participant=? AND regime=? AND is_incumbent=0 "
        "ORDER BY promoted_at DESC",
        (rec.participant, rec.regime),
    ).fetchall()
    for champion_id_old, promoted in rows[TOURNAMENT_CHAMPION_SLOTS:]:
        conn.execute("DELETE FROM tournament_champions WHERE champion_id=?", (champion_id_old,))
        logger.info("slot cap: dropped old champion {} ({} {})", champion_id_old, rec.participant, promoted)
    conn.commit()
    return champion_id


def incumbents(conn: sqlite3.Connection) -> list[ChampionRecord]:
    rows = conn.execute(
        "SELECT * FROM tournament_champions WHERE is_incumbent=1 ORDER BY participant, regime"
    ).fetchall()
    return [
        ChampionRecord(
            champion_id=r[0], participant=r[1], regime=r[2], params=json.loads(r[3]),
            version=r[4], generation=r[5], evidence=json.loads(r[6]),
            promoted_at=date.fromisoformat(r[7]), oos_consumed=r[8], is_incumbent=bool(r[9]),
        )
        for r in rows
    ]


def promote_from_ledger(conn: sqlite3.Connection) -> ChampionRecord | None:
    """Promote the latest passing evolve campaign into the archive.

    Reads tournament_ledger for the newest op_type='evolve' row whose detail
    has ok=true, archives it as the incumbent champion, and appends a
    'promote' ledger record.  Returns None when nothing qualifies.
    """
    from davis_analyzer.tournament.ledger import LedgerRecord, append_record

    rows = conn.execute(
        "SELECT id, participants, detail FROM tournament_ledger "
        "WHERE op_type='evolve' ORDER BY id DESC"
    ).fetchall()
    for _id, participants_json, detail_json in rows:
        detail = json.loads(detail_json or "{}")
        if not detail.get("ok"):
            continue
        participants = json.loads(participants_json or "[]")
        name = participants[0][0] if participants else "unknown"
        regime = "all"  # v1: campaign optimises over all segments (see Interfaces)
        params = {k: float(v) for k, v in detail.get("best_params", {}).items()}
        for inc in incumbents(conn):
            if inc.participant == name and inc.regime == regime and inc.params == params:
                return inc  # already promoted — idempotent
        gen_row = conn.execute(
            "SELECT COALESCE(MAX(generation), 0) FROM tournament_champions "
            "WHERE participant=? AND regime=?",
            (name, regime),
        ).fetchone()
        gen = int(gen_row[0]) + 1  # MAX+1：满槽淘汰历史冠军后仍单调递增，不复用代数
        rec = ChampionRecord(
            champion_id=uuid.uuid4().hex[:12],
            participant=name, regime=regime,
            params=params,
            version=f"gen{gen}",
            generation=gen,
            evidence={k: detail.get(k) for k in
                      ("improvements", "decay", "finals_pass", "reasons")},
            promoted_at=date.today(), oos_consumed=1, is_incumbent=True,
        )
        champion_id = promote_champion(conn, rec)
        append_record(conn, LedgerRecord(
            op_type="promote", run_date=rec.promoted_at,
            participants=[(name, rec.version)], params_version=rec.version,
            oos_windows_used=1, detail={"champion_id": champion_id},
        ))
        return rec
    return None


def _params_key(params: dict) -> str:
    """Canonical comparable form: both sides numeric-normalised (float), key-sorted."""
    return json.dumps({k: float(v) for k, v in sorted(params.items())}, sort_keys=True)


def verify_sync(conn: sqlite3.Connection, presets: dict[str, dict]) -> list[str]:
    """Champions deployed in constants.CHAMPION_PRESETS must match DB incumbents."""
    problems: list[str] = []
    current = incumbents(conn)  # 只取一次，比较与遍历共用
    deployed = set(presets)
    for c in current:
        if c.participant not in deployed:
            problems.append(f"{c.participant}: DB 现任冠军未部署到 CHAMPION_PRESETS")
    for name, params in presets.items():
        key = _params_key(params)
        if not any(c.participant == name and _params_key(c.params) == key for c in current):
            problems.append(f"{name}: CHAMPION_PRESETS 参数与 DB 现任冠军不一致")
    return problems


def render_deploy_note(recs: list[ChampionRecord]) -> str:
    lines = [
        "# 冠军部署说明（champions deploy 生成）",
        "",
        "将以下现任冠军参数同步进 `davis_analyzer/constants.py` 的 `CHAMPION_PRESETS`，",
        "并在 SOP.md 记录版本变更；同步后运行 `verify` 确认一致。",
        "",
    ]
    for c in recs:
        lines.append(f"## {c.participant}（regime={c.regime}, version={c.version}, gen={c.generation}）")
        lines.append("```python")
        lines.append(f'CHAMPION_PRESETS["{c.participant}"] = {json.dumps(c.params, ensure_ascii=False, indent=2)}')
        lines.append("```")
        lines.append(f"上位证据：{json.dumps(c.evidence, ensure_ascii=False)}\n")
    return "\n".join(lines)
