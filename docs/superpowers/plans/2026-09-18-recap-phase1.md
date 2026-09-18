# recap 复盘短视频子系统 一期实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建成 `davis_analyzer/recap/` 子系统一期:每晚自动「选片(戏剧性评分)→ LLM 双解说剧本 → 飞书录制单」,人工录屏后一键产「双TTS+字幕+数据卡原料包」(剪映人工拼),数据卡过视觉质检闸;二期(可选)ffmpeg 全自动合成。

**Architecture:** 独立子系统,与 limitup/thermometer 同级。只读两个既有库(stockhot.db JSON blob + market_data.db 结构化表),台账自管表 `recap_episodes` 挂 market_data.db(模式 B,limitup 先例)。文件即真相源(`recap/episodes/{day}/`),台账只记状态机。复用:cardgen 的 `Fact`/`unmatched_tokens`/敏感词表/`video.py` 的 ffmpeg 工具、`vision.py` 视觉质检、`llm_provider` LLM 客户端、daily_market_cards 的飞书推送模式。

**Tech Stack:** Python 3.11+(.venv),sqlite3(只读双库+自管台账),edge-tts 7.2.8(已装),playwright(已装,html→png),imageio_ffmpeg 的 ffmpeg 二进制(无 ffprobe,时长用 `ffmpeg -i` stderr 解析),stockhot.advisor.llm_provider(OpenAI 兼容)。

**设计 spec:** `docs/superpowers/specs/2026-09-18-recap-video-design.md`(拍板记录见 §一)

## Global Constraints

- 一律从仓库根目录 `/home/leo/Projects/CodeAgentDashboard/` 用 `.venv/bin/python` 运行(davis_analyzer 目录下运行会因 `types.py` 遮蔽标准库而崩)。
- 日志用 `loguru`;`print()` 只允许出现在 `cli.py`。
- 权重单一真相源:`RECAP_DRAMA_WEIGHTS` 放父级 `davis_analyzer/constants.py`(项目铁律,**覆盖 spec §三写的 recap/constants.py**——意图不变:单一真相源,位置遵项目惯例);其余 recap 配置(音色/额外敏感词/路径/文件名正则)放 `recap/constants.py`。
- 日期约定:recap 内部一律 `YYYY-MM-DD`(dash);查 `daily_price`/`index_daily`(YYYYMMDD 紧凑)时由 data.py 转换。`limit_pool.ts_code` 存在带后缀/裸码双写重复行——读取必须按 `(trade_date, ts_code)` 去重,统一保留带后缀码。`first_seal_time`/`last_seal_time` 为 HHMMSS 字符串(可能缺前导零),归一为 `HH:MM:SS`。
- 素材文件名协议:`{YYYYMMDD}_{ts_code}_{两位序号}.mp4`(如 `20260918_605577.SH_01.mp4`),正则 `^(\d{8})_([0-9]{6}\.(?:SH|SZ|BJ))_(\d{2})\.mp4$`。
- 发布永远人工;`published` 状态人工标记,系统永不自动发布。
- 数字纪律:解说词中任何数字必须命中 facts(`unmatched_tokens` 机器闸);LLM 只组织语言,数字全部由代码注入 prompt。
- 合规:解说只描述已发生事实;末段必含「不构成投资建议」原话;敏感词=cardgen 词表+recap 附加词(买入/抄底/上车/必涨/满仓/加仓/止盈/止损/目标价/建仓)。
- **不复用 cardgen `INDUCEMENT_PATTERNS` 的「你(应该|可以|要|不妨)」第二人称正则**(解说文体合法使用「你看/你看到没有」),改用收窄版 `(你应该|快去|赶紧)`;赌博化正则保留。此偏离已在计划中拍板,写进 recap/constants.py 注释。
- 飞书推送 tags/话题标签必须是消息最后一行;幂等锁目录 `logs/.recap_sheet/`。
- 提交规范:Conventional Commits 中文 scope,如 `feat(recap): 选片引擎落地`。

---

### Task 1: 子系统骨架 + 台账表 + status 命令

**Files:**
- Create: `davis_analyzer/recap/__init__.py`
- Create: `davis_analyzer/recap/__main__.py`
- Create: `davis_analyzer/recap/constants.py`
- Create: `davis_analyzer/recap/types.py`
- Create: `davis_analyzer/recap/db.py`
- Create: `davis_analyzer/recap/cli.py`(仅 status)
- Test: `davis_analyzer/tests/test_recap_db.py`

**Interfaces:**
- Produces: `db.ensure_tables(conn)`、`db.save_episode(conn, meta: dict)`、`db.update_status(conn, trade_date, status)`、`db.get_episode(conn, trade_date) -> dict | None`、`db.RECAP_STATUSES: tuple`;`types` 的 `Candidate`/`DramaEvent`/`DialogueLine`/`EpisodeSegment`/`Episode`(含 `to_dict`/`from_dict`);`constants.RECAP_ROOT`/`EPISODES_DIR`/`INBOX_DIR`/`VOICE_PB`/`VOICE_COLOR`/`EXTRA_SENSITIVE_WORDS`/`CLIP_FILENAME_RE`/`NBA_STYLE_TABLE`。CLI:`python -m davis_analyzer.recap status [--date YYYY-MM-DD]`。

- [ ] **Step 1: 写失败测试**

```python
# davis_analyzer/tests/test_recap_db.py
"""recap 台账:建表/保存/状态机/读取。"""
from __future__ import annotations

import sqlite3

from davis_analyzer.recap import db


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    db.ensure_tables(conn)
    return conn


def test_ensure_tables_idempotent():
    conn = sqlite3.connect(":memory:")
    db.ensure_tables(conn)
    db.ensure_tables(conn)  # 不抛异常
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='recap_episodes'"
    ).fetchone()
    assert row is not None


def test_save_and_get_roundtrip():
    conn = _conn()
    meta = {"trade_date": "2026-09-18", "status": "selected",
            "candidates_json": '[{"ts_code": "605577.SH"}]',
            "episode_json": None, "facts_json": None}
    db.save_episode(conn, meta)
    got = db.get_episode(conn, "2026-09-18")
    assert got["status"] == "selected"
    assert "605577.SH" in got["candidates_json"]


def test_update_status():
    conn = _conn()
    db.save_episode(conn, {"trade_date": "2026-09-18", "status": "selected"})
    db.update_status(conn, "2026-09-18", "scripted")
    assert db.get_episode(conn, "2026-09-18")["status"] == "scripted"


def test_update_status_rejects_unknown():
    conn = _conn()
    db.save_episode(conn, {"trade_date": "2026-09-18", "status": "selected"})
    try:
        db.update_status(conn, "2026-09-18", "nope")
        raise AssertionError("应拒绝未知状态")
    except ValueError:
        pass
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_db.py -v`
Expected: FAIL `ModuleNotFoundError: davis_analyzer.recap`

- [ ] **Step 3: 实现**

```python
# davis_analyzer/recap/__init__.py
"""每晚NBA解说式热点复盘短视频子系统(一期:选片+剧本+录制单+原料包)。

设计 spec: docs/superpowers/specs/2026-09-18-recap-video-design.md
实施计划: docs/superpowers/plans/2026-09-18-recap-phase1.md
"""
```

```python
# davis_analyzer/recap/__main__.py
"""Entry point for `python -m davis_analyzer.recap`."""
from __future__ import annotations

from davis_analyzer.recap.cli import main

main()
```

```python
# davis_analyzer/recap/constants.py
"""recap 子系统配置(戏剧权重在父级 constants.py,此处只放非权重配置)。"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RECAP_ROOT = Path(__file__).resolve().parent
EPISODES_DIR = RECAP_ROOT / "episodes"
INBOX_DIR = RECAP_ROOT / "inbox"

# ── TTS 双解说音色(edge-tts;实况=激情男声,嘉宾=沉稳女声)──
VOICE_PB = "zh-CN-YunjianNeural"
VOICE_COLOR = "zh-CN-XiaoxiaoNeural"

# ── 素材文件名协议:20260918_605577.SH_01.mp4 ──
CLIP_FILENAME_RE = re.compile(r"^(\d{8})_([0-9]{6}\.(?:SH|SZ|BJ))_(\d{2})\.mp4$")

# ── 视频专用附加敏感词(cardgen 词表之上;解说=资讯复盘,禁交易指令)──
EXTRA_SENSITIVE_WORDS: tuple[str, ...] = (
    "买入", "抄底", "上车", "必涨", "满仓", "加仓", "止盈", "止损", "目标价", "建仓",
)
# 收窄版诱导句式:不复用 cardgen 的 你(应该|可以|要|不妨)——解说文体合法用「你看/你看到没有」
INDUCEMENT_PATTERNS_NARROW: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"你应该|快去|赶紧"), "第二人称劝导(收窄版)"),
    (re.compile(r"赌[A-Za-z0-9\u4e00-\u9fa5]{1,6}|赌徒"), "赌博化表述"),
)

# ── NBA 转播语言映射(节目灵魂,scriptwriter prompt 引用)──
NBA_STYLE_TABLE: tuple[tuple[str, str], ...] = (
    ("涨停封板", "扣筐得手;尾盘封板=压哨绝杀"),
    ("炸板", "被帽/关键失误"),
    ("烂板回封", "被帽后再扣,统治力"),
    ("地天板", "大逆转,更衣室归来"),
    ("连板天梯", "积分榜/连胜纪录"),
    ("游资席位", "球星对位(席位=球员)"),
    ("三大指数+涨跌家数", "片头比分牌"),
    ("振幅/换手", "数据弹出卡"),
)

REQUIRED_DISCLAIMER = "不构成投资建议"
```

```python
# davis_analyzer/recap/types.py
"""recap 纯数据类型。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DramaEvent:
    kind: str            # reseal(炸板回封)/broken(收盘炸板)/earth_sky(地天板)/ladder(天梯)/lhb(龙虎榜)/long_leg(大长腿)
    label: str           # 中文标签,如「三度炸板回封」
    score: float
    detail: dict[str, object]  # 事件事实:封板时间/炸板次数/振幅等


@dataclass
class Candidate:
    ts_code: str         # 带后缀 605577.SH
    name: str
    sector: str
    drama_score: float
    events: list[DramaEvent] = field(default_factory=list)
    replay_start: str = "09:30"   # 回放区间(供录制单)
    replay_end: str = "15:00"
    notes: list[str] = field(default_factory=list)
    educational: bool = False      # 板块效应可讲=教育性段落
    facts: list[dict] = field(default_factory=list)  # Fact.to_dict() 形态


@dataclass
class DialogueLine:
    speaker: str         # "pb"(实况) / "color"(嘉宾)
    text: str


@dataclass
class EpisodeSegment:
    seg_id: str          # open / s1..sN / close
    kind: str            # scoreboard / stock / outlook
    ts_code: str | None
    lines: list[DialogueLine] = field(default_factory=list)


@dataclass
class Episode:
    trade_date: str      # YYYY-MM-DD
    title: str
    segments: list[EpisodeSegment] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "trade_date": self.trade_date, "title": self.title,
            "segments": [{
                "seg_id": s.seg_id, "kind": s.kind, "ts_code": s.ts_code,
                "lines": [{"speaker": l.speaker, "text": l.text} for l in s.lines],
            } for s in self.segments],
            "facts": self.facts,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Episode:
        return cls(
            trade_date=d["trade_date"], title=d["title"], facts=d.get("facts", []),
            segments=[EpisodeSegment(
                seg_id=s["seg_id"], kind=s["kind"], ts_code=s.get("ts_code"),
                lines=[DialogueLine(speaker=l["speaker"], text=l["text"]) for l in s.get("lines", [])],
            ) for s in d.get("segments", [])],
        )
```

```python
# davis_analyzer/recap/db.py
"""recap 台账:market_data.db 自管表 recap_episodes(模式 B,limitup 先例)。"""
from __future__ import annotations

import json
import sqlite3
import time

RECAP_STATUSES: tuple[str, ...] = (
    "selected", "scripted", "sheeted", "recorded", "packed", "composed", "published",
)


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS recap_episodes ("
        "trade_date TEXT PRIMARY KEY, "
        "status TEXT NOT NULL, "
        "candidates_json TEXT, "
        "episode_json TEXT, "
        "facts_json TEXT, "
        "pushed_at TEXT, "
        "packed_at TEXT, "
        "created_at REAL, "
        "updated_at REAL)"
    )
    conn.commit()


def save_episode(conn: sqlite3.Connection, meta: dict) -> None:
    row = conn.execute("SELECT created_at FROM recap_episodes WHERE trade_date=?",
                       (meta["trade_date"],)).fetchone()
    now = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO recap_episodes "
        "(trade_date, status, candidates_json, episode_json, facts_json, "
        " pushed_at, packed_at, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (meta["trade_date"], meta["status"],
         meta.get("candidates_json"), meta.get("episode_json"), meta.get("facts_json"),
         meta.get("pushed_at"), meta.get("packed_at"),
         row[0] if row else now, now),
    )
    conn.commit()


def update_status(conn: sqlite3.Connection, trade_date: str, status: str) -> None:
    if status not in RECAP_STATUSES:
        raise ValueError(f"未知状态 {status!r},合法: {RECAP_STATUSES}")
    conn.execute("UPDATE recap_episodes SET status=?, updated_at=? WHERE trade_date=?",
                 (status, time.time(), trade_date))
    conn.commit()


def get_episode(conn: sqlite3.Connection, trade_date: str) -> dict | None:
    row = conn.execute(
        "SELECT trade_date, status, candidates_json, episode_json, facts_json, "
        "pushed_at, packed_at FROM recap_episodes WHERE trade_date=?", (trade_date,)
    ).fetchone()
    if not row:
        return None
    return {"trade_date": row[0], "status": row[1],
            "candidates": json.loads(row[2]) if row[2] else None,
            "episode": json.loads(row[3]) if row[3] else None,
            "facts": json.loads(row[4]) if row[4] else None,
            "pushed_at": row[5], "packed_at": row[6]}
```

```python
# davis_analyzer/recap/cli.py
"""recap CLI:python -m davis_analyzer.recap {run|select|script|sheet|audio|post|status}。"""
from __future__ import annotations

import argparse
from datetime import datetime


def _conn():
    from stockhot.data_layer.market_db import get_connection
    return get_connection()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recap", description="每晚NBA解说式复盘短视频")
    sub = p.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status", help="查看台账")
    st.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    st.set_defaults(func=cmd_status)
    return p


def cmd_status(args) -> None:
    from davis_analyzer.recap import db
    conn = _conn()
    try:
        db.ensure_tables(conn)
        row = db.get_episode(conn, args.date)
    finally:
        conn.close()
    if not row:
        print(f"{args.date}: 无台账(未选片)")
        return
    seg_n = len((row.get("episode") or {}).get("segments", []))
    print(f"{args.date}: status={row['status']} "
          f"candidates={len(row.get('candidates') or [])} segments={seg_n}")


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)
```

- [ ] **Step 4: 运行测试通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_db.py -v`
Expected: 4 passed

- [ ] **Step 5: 烟测 CLI 并提交**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m davis_analyzer.recap status --date 2026-09-18`(预期输出「无台账」)

```bash
git add davis_analyzer/recap/ davis_analyzer/tests/test_recap_db.py
git commit -m "feat(recap): 子系统骨架+recap_episodes台账+status命令"
```

---

### Task 2: data.py 双库读取层

**Files:**
- Create: `davis_analyzer/recap/data.py`
- Test: `davis_analyzer/tests/test_recap_data.py`

**Interfaces:**
- Consumes: 无(只读 sqlite)。
- Produces: `data.stockhot_db_path() -> Path`、`data.market_db_path() -> Path`、`data.fetch_bundle(day_dash: str) -> dict`(缺涨停池抛 `data.DailyDataMissing`)。bundle 键:`pool`(涨停池行,ts_code 带后缀、seal 时间 `HH:MM:SS`、`consecutive_boards`/`broken_count` int)、`broken`(收盘炸板行)、`down`(曾跌停行)、`boards`(天梯 `[{"board_count": int, "stocks": [{"code","name"}]}]` 降序)、`lhb_codes`(set[str] 龙虎榜个股码)、`brokers`(席位 list)、`index`(`[{"code","name","close","pct_chg"}]` 三大指数)、`amplitude_top`(`[{"ts_code","amplitude_pct"}]` 振幅≥12 top30)、`breadth`(`{"up": int, "down": int}`)、`names`(ts_code→name dict)、`limit_up_count`(int)。Task 3/5/9 消费此结构。

- [ ] **Step 1: 写失败测试**

```python
# davis_analyzer/tests/test_recap_data.py
"""recap 数据层:双库 bundle、去重、时间归一、日期转换。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from davis_analyzer.recap import data


def _mk_stockhot_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE daily_data (trade_date TEXT, data_type TEXT, data_json TEXT);
    CREATE TABLE analysis_results (trade_date TEXT, analysis_type TEXT, result_json TEXT);
    """)
    con.execute("INSERT INTO daily_data VALUES (?,?,?)", ("2026-09-18", "limit_up_pool", json.dumps([
        {"code": "605577.SH", "name": "龙版传媒", "sector": "出版", "change_pct": 9.97,
         "consecutive_boards": 3, "broken_count": 2, "first_seal_time": "94700",
         "last_seal_time": "143500", "turnover_rate": 11.87},
        # 双写重复行(裸码),应被去重
        {"code": "605577", "name": "龙版传媒", "sector": "出版", "change_pct": 9.97,
         "consecutive_boards": 3, "broken_count": 2, "first_seal_time": "94700",
         "last_seal_time": "143500", "turnover_rate": 11.87},
    ])))
    con.execute("INSERT INTO daily_data VALUES (?,?,?)", ("2026-09-18", "broken_pool", json.dumps([
        {"code": "688296.SH", "name": "和达科技", "sector": "软件开发", "change_pct": 9.83,
         "broken_count": 1}])))
    con.execute("INSERT INTO daily_data VALUES (?,?,?)", ("2026-09-18", "limit_down_pool", json.dumps([
        {"code": "002163.SZ", "name": "海南发展", "sector": "装修装饰", "change_pct": -9.97}])))
    con.execute("INSERT INTO analysis_results VALUES (?,?,?)", ("2026-09-18", "limit_up_analysis", json.dumps(
        {"consecutive_boards": [{"board_count": 3, "stocks": [{"code": "605577.SH", "name": "龙版传媒"}]}]})))
    con.execute("INSERT INTO analysis_results VALUES (?,?,?)", (
        "2026-09-18", "dragon_tiger", json.dumps(
            {"brokers": [{"broker_name": "某营业部", "net_amount": 4.5e8}]})))
    con.execute("INSERT INTO daily_data VALUES (?,?,?)", ("2026-09-18", "dragon_tiger_detail", json.dumps([
        {"code": "605577.SH", "name": "龙版传媒", "net_buy_amount": 1.2e8}])))
    con.commit(); con.close()


def _mk_market_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE daily_price (ts_code TEXT, trade_date TEXT, close REAL, pre_close REAL,
                              high REAL, low REAL, pct_chg REAL, PRIMARY KEY (ts_code, trade_date));
    CREATE TABLE index_daily (ts_code TEXT, trade_date TEXT, close REAL, pct_chg REAL);
    """)
    for code, close, pre, high, low, pct in [
        ("605577.SH", 10.97, 9.97, 11.0, 9.0, 10.0),
        ("000001.SH", 3875.6, 3891.6, 3900.0, 3860.0, -0.41),   # 上证也会进 daily_price
        ("300XXX.SZ", 20.0, 15.0, 21.0, 15.5, 33.3),            # 振幅 36%
    ]:
        con.execute("INSERT INTO daily_price VALUES (?,?,?,?,?,?)",
                    (code, "20260918", close, pre, high, low, pct))
    con.execute("INSERT INTO index_daily VALUES (?,?,?,?)", ("000001.SH", "20260918", 3875.6, -0.411))
    con.execute("INSERT INTO index_daily VALUES (?,?,?,?)", ("399001.SZ", "20260918", 12345.6, 0.52))
    con.execute("INSERT INTO index_daily VALUES (?,?,?,?)", ("399006.SZ", "20260918", 2710.2, 0.85))
    con.commit(); con.close()


@pytest.fixture()
def bundle(tmp_path, monkeypatch):
    sh, mk = tmp_path / "stockhot.db", tmp_path / "market_data.db"
    _mk_stockhot_db(sh)
    _mk_market_db(mk)
    monkeypatch.setattr(data, "stockhot_db_path", lambda: sh)
    monkeypatch.setattr(data, "market_db_path", lambda: mk)
    return data.fetch_bundle("2026-09-18")


def test_pool_dedup_and_time_normalize(bundle):
    codes = [r["ts_code"] for r in bundle["pool"]]
    assert codes.count("605577.SH") == 1          # 双写去重
    row = bundle["pool"][0]
    assert row["first_seal_time"] == "09:47:00"   # 94700 → HH:MM:SS
    assert row["consecutive_boards"] == 3 and row["broken_count"] == 2


def test_bundle_keys_and_shapes(bundle):
    assert bundle["boards"][0]["board_count"] == 3
    assert "605577.SH" in bundle["lhb_codes"]
    assert bundle["brokers"][0]["net_amount"] == 4.5e8
    idx = {r["code"] for r in bundle["index"]}
    assert idx == {"000001.SH", "399001.SZ", "399006.SZ"}
    assert any(a["ts_code"] == "300XXX.SZ" for a in bundle["amplitude_top"])
    assert bundle["limit_up_count"] == 1


def test_missing_pool_raises(tmp_path, monkeypatch):
    sh, mk = tmp_path / "s.db", tmp_path / "m.db"
    _mk_stockhot_db(sh)
    con = sqlite3.connect(sh)
    con.execute("DELETE FROM daily_data WHERE data_type='limit_up_pool'")
    con.commit(); con.close()
    _mk_market_db(mk)
    monkeypatch.setattr(data, "stockhot_db_path", lambda: sh)
    monkeypatch.setattr(data, "market_db_path", lambda: mk)
    with pytest.raises(data.DailyDataMissing):
        data.fetch_bundle("2026-09-18")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_data.py -v`
Expected: FAIL `ModuleNotFoundError`(data 未建)

- [ ] **Step 3: 实现**

```python
# davis_analyzer/recap/data.py
"""recap 只读数据层:stockhot.db JSON blob + market_data.db 结构化表 → 当日 bundle。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from davis_analyzer.recap.constants import REPO_ROOT

_DEFAULT_STOCKHOT = REPO_ROOT / "storage" / "database" / "stockhot.db"
_DEFAULT_MARKET = REPO_ROOT / "storage" / "database" / "market_data.db"

INDEX_CODES = ("000001.SH", "399001.SZ", "399006.SZ")   # 上证/深成/创业板
INDEX_NAMES = {"000001.SH": "上证指数", "399001.SZ": "深证成指", "399006.SZ": "创业板指"}
MIN_AMPLITUDE, AMPLITUDE_TOP_N = 12.0, 30


class DailyDataMissing(RuntimeError):
    """当日采集数据不完整,拒绝选片(与 cardgen 同口径)。"""


def stockhot_db_path() -> Path:
    import os
    return Path(os.environ.get("RECAP_STOCKHOT_DB", _DEFAULT_STOCKHOT))


def market_db_path() -> Path:
    import os
    return Path(os.environ.get("RECAP_MARKET_DB", _DEFAULT_MARKET))


def _ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _norm_time(s: object) -> str:
    """'94700'/'144600' → '09:47:00'/'14:46:00';空/脏值 → ''。"""
    if not s or not str(s).strip():
        return ""
    t = str(s).strip().zfill(6)
    if not t.isdigit() or len(t) != 6:
        return ""
    return f"{t[:2]}:{t[2:4]}:{t[4:6]}"


def _dedup_pool(rows: list[dict]) -> list[dict]:
    """limit_up_pool 双写(带后缀+裸码)按 ts_code 根去重,保留带后缀行。"""
    out: dict[str, dict] = {}
    for r in rows:
        code = str(r.get("code", ""))
        root = code.split(".")[0]
        if root not in out or "." in code:
            r = dict(r, ts_code=code if "." in code else code, ts_root=root)
            out[root] = r
    return list(out.values())


def fetch_bundle(day_dash: str) -> dict:
    """一站式当日 bundle;缺 limit_up_pool / 天梯抛 DailyDataMissing。"""
    con = _ro(stockhot_db_path())

    def dj(dt: str) -> list[dict]:
        row = con.execute("SELECT data_json FROM daily_data WHERE trade_date=? AND data_type=?",
                          (day_dash, dt)).fetchone()
        return json.loads(row[0]) if row else []

    def aj(at: str) -> dict | None:
        row = con.execute("SELECT result_json FROM analysis_results WHERE trade_date=? AND analysis_type=?",
                          (day_dash, at)).fetchone()
        return json.loads(row[0]) if row else None

    try:
        lu = aj("limit_up_analysis")
        pool_raw = dj("limit_up_pool")
        if not lu or not lu.get("consecutive_boards") or not pool_raw:
            raise DailyDataMissing(
                f"{day_dash} 缺 limit_up_analysis/limit_up_pool(盘面扫描未完成?)")
        pool = _dedup_pool(pool_raw)
        broken = _dedup_pool(dj("broken_pool"))
        down = _dedup_pool(dj("limit_down_pool"))
        lhb_detail = dj("dragon_tiger_detail")
        dt = aj("dragon_tiger") or {}
    finally:
        con.close()

    day_compact = day_dash.replace("-", "")
    mcon = _ro(market_db_path())
    try:
        index = []
        for code, close, pct in mcon.execute(
                "SELECT ts_code, close, pct_chg FROM index_daily WHERE trade_date=? "
                f"AND ts_code IN ({','.join('?' * len(INDEX_CODES))})",
                (day_compact, *INDEX_CODES)):
            index.append({"code": code, "name": INDEX_NAMES[code],
                          "close": float(close), "pct_chg": float(pct)})
        amp = [{"ts_code": r[0], "amplitude_pct": float(r[1])} for r in mcon.execute(
            "SELECT ts_code, ROUND((high-low)/pre_close*100,2) AS amp FROM daily_price "
            "WHERE trade_date=? AND pre_close>0 AND high>0 "
            "ORDER BY amp DESC LIMIT ?", (day_compact, AMPLITUDE_TOP_N))]
        amp = [a for a in amp if a["amplitude_pct"] >= MIN_AMPLITUDE]
        up, down_n = mcon.execute(
            "SELECT SUM(pct_chg>0), SUM(pct_chg<0) FROM daily_price WHERE trade_date=?",
            (day_compact,)).fetchone()
    finally:
        mcon.close()

    names: dict[str, str] = {}
    for rowset in (pool, broken, down):
        for r in rowset:
            names[r["ts_code"]] = str(r.get("name", ""))

    return {
        "pool": [dict(r, first_seal_time=_norm_time(r.get("first_seal_time")),
                      last_seal_time=_norm_time(r.get("last_seal_time")),
                      consecutive_boards=int(r.get("consecutive_boards") or 1),
                      broken_count=int(r.get("broken_count") or 0)) for r in pool],
        "broken": broken, "down": down,
        "boards": sorted(lu["consecutive_boards"], key=lambda t: -int(t["board_count"])),
        "lhb_codes": {str(r.get("code", "")) for r in lhb_detail},
        "lhb_detail": lhb_detail,
        "brokers": dt.get("brokers") or [],
        "index": index, "amplitude_top": amp,
        "breadth": {"up": int(up or 0), "down": int(down_n or 0)},
        "names": names, "limit_up_count": len(pool),
    }
```

- [ ] **Step 4: 运行测试通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_data.py -v`
Expected: 3 passed

- [ ] **Step 5: 真库烟测并提交**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -c "from davis_analyzer.recap.data import fetch_bundle; b=fetch_bundle('2026-09-17'); print(len(b['pool']), b['limit_up_count'], b['breadth'])"`
(用最近一个真实交易日验证真库可读;失败则核对当日数据是否已采集)

```bash
git add davis_analyzer/recap/data.py davis_analyzer/tests/test_recap_data.py
git commit -m "feat(recap): 双库读取层fetch_bundle(去重/时间归一/日期转换)"
```

---

### Task 3: selector.py 选片引擎 + 戏剧权重入 constants

**Files:**
- Modify: `davis_analyzer/constants.py`(文件末尾追加 RECAP 段)
- Create: `davis_analyzer/recap/selector.py`
- Test: `davis_analyzer/tests/test_recap_selector.py`

**Interfaces:**
- Consumes: Task 2 的 bundle 结构;Task 1 的 `Candidate`/`DramaEvent`。
- Produces: `selector.score_day(bundle) -> list[Candidate]`(全量打分,降序)、`selector.select_candidates(bundle, max_count=5, per_sector_cap=2) -> list[Candidate]`(多样性筛选+教育性保底;空列表=冰点日)。`Candidate.facts` 装满 `Fact.to_dict()` 形态(Task 4/5 直接用)。

- [ ] **Step 1: 写失败测试**

```python
# davis_analyzer/tests/test_recap_selector.py
"""recap 选片:戏剧性评分/多样性/教育性/冰点降级/facts 装配。"""
from __future__ import annotations

from davis_analyzer.recap.selector import score_day, select_candidates


def _bundle(**over):
    b = {
        "pool": [
            {"ts_code": "605577.SH", "name": "龙版传媒", "sector": "出版", "change_pct": 9.97,
             "consecutive_boards": 5, "broken_count": 3, "first_seal_time": "09:47:00",
             "last_seal_time": "14:46:00", "turnover_rate": 11.9},
            {"ts_code": "001216.SZ", "name": "华瓷股份", "sector": "陶瓷", "change_pct": 10.0,
             "consecutive_boards": 1, "broken_count": 0, "first_seal_time": "09:35:00",
             "last_seal_time": "09:35:00", "turnover_rate": 3.0},
            {"ts_code": "600001.SH", "name": "出版A", "sector": "出版", "change_pct": 10.0,
             "consecutive_boards": 1, "broken_count": 0, "first_seal_time": "10:00:00",
             "last_seal_time": "10:00:00", "turnover_rate": 5.0},
        ],
        "broken": [], "down": [],
        "boards": [{"board_count": 5, "stocks": [{"code": "605577.SH", "name": "龙版传媒"}]},
                   {"board_count": 1, "stocks": [{"code": "001216.SZ"}, {"code": "600001.SH"}]}],
        "lhb_codes": {"605577.SH"}, "lhb_detail": [{"code": "605577.SH", "net_buy_amount": 1.2e8}],
        "brokers": [{"broker_name": "X营业部", "net_amount": 2.0e8}],
        "index": [{"code": "000001.SH", "name": "上证指数", "close": 3875.6, "pct_chg": -0.411}],
        "amplitude_top": [{"ts_code": "605577.SH", "amplitude_pct": 18.5}],
        "breadth": {"up": 3200, "down": 1900}, "names": {}, "limit_up_count": 3,
    }
    b.update(over)
    return b


def test_score_day_ranks_drama():
    cands = score_day(_bundle())
    assert cands[0].ts_code == "605577.SH"      # 5板+3炸回封+尾盘回封+龙虎榜=最高分
    top = cands[0]
    kinds = {e.kind for e in top.events}
    assert "reseal" in kinds and "ladder" in kinds and "lhb" in kinds
    assert top.replay_start < "09:47:00" < top.replay_end  # 回放窗覆盖首封时间


def test_select_diversity_and_educational():
    picked = select_candidates(_bundle(), max_count=2, per_sector_cap=1)
    sectors = [c.sector for c in picked]
    assert len(sectors) == len(set(sectors))    # 同板块上限 1
    # 出版板块 3 家涨停=板块效应,605577 educational=True 且必入选
    assert any(c.ts_code == "605577.SH" and c.educational for c in picked)


def test_ice_day_returns_empty():
    empty = _bundle(pool=[], broken=[], boards=[], amplitude_top=[], limit_up_count=0)
    assert select_candidates(empty) == []
    assert score_day(empty) == []


def test_broken_pool_scores():
    b = _bundle(pool=[], boards=[], broken=[
        {"ts_code": "688296.SH", "name": "和达科技", "sector": "软件", "broken_count": 2}])
    cands = score_day(b)
    assert cands and cands[0].ts_code == "688296.SH"
    assert cands[0].events[0].kind == "broken"


def test_facts_attached():
    top = score_day(_bundle())[0]
    ids = {f["id"] for f in top.facts}
    assert "605577.SH_boards" in ids and "605577.SH_broken" in ids
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_selector.py -v`
Expected: FAIL `cannot import name 'selector'`

- [ ] **Step 3: 实现**

先在 `davis_analyzer/constants.py` 末尾追加(带分段框线,风格对齐 THERMOMETER 段):

```python
# ── 复盘视频选片戏剧权重(spec 2026-09-18 §三;节目效果分,非投资分;勿运行时修改)──
RECAP_DRAMA_WEIGHTS: dict[str, float] = {
    "limit_up_base": 20.0,     # 收盘涨停基础分
    "reseal_per_broken": 8.0,  # 每次炸板后回封(烂板回封戏剧性)
    "reseal_late": 15.0,       # 尾盘(>=14:30)最后回封=压哨绝杀
    "ladder_top": 25.0,        # 当日最高板=积分榜首
    "ladder_extra_per_board": 5.0,   # 每多 1 板
    "streak_3plus": 10.0,      # 3 板及以上=连胜纪录
    "earth_sky": 35.0,         # 地天板(曾跌停→收盘涨停)=大逆转
    "broken_close_base": 15.0, # 收盘炸板(被帽戏码)基础分
    "long_leg_amp": 10.0,      # 振幅>=15 大长腿基础分
    "lhb_listed": 15.0,        # 上龙虎榜=球星对位入场
    "lhb_big_broker": 10.0,    # 席位净额>1亿=巨星对决
}
```

```python
# davis_analyzer/recap/selector.py
"""recap 选片引擎:当日戏剧性评分(节目效果分,非投资分)。"""
from __future__ import annotations

from decimal import Decimal

from davis_analyzer.constants import RECAP_DRAMA_WEIGHTS as W
from davis_analyzer.recap.types import Candidate, DramaEvent

_LATE_SEAL = "14:30:00"
_EDU_SECTOR_LIMITUPS = 3     # 板块涨停家数门槛=可讲板块叙事(教育性段落)
_AMP_FOR_LONG_LEG = 15.0


def _fact(fid: str, value, unit: str, display: str, day: str, ref: str) -> dict:
    """Fact.to_dict 兼容形态(source=stockhot 指纹,当日过期)。"""
    s = format(Decimal(str(value)), "f")
    return {"id": fid, "value": s.rstrip("0").rstrip(".") if "." in s else s,
            "unit": unit, "display": display, "as_of": day,
            "source": {"kind": "stockhot", "ref": ref}}


def _window(first: str, last: str) -> tuple[str, str]:
    """回放窗:首封前 20 分钟 ~ 最后封板后 5 分钟,夹在 09:30-15:00 内。"""
    def shift(hhmmss: str, minutes: int) -> str:
        h, m, _ = (int(x) for x in hhmmss.split(":"))
        total = max(9 * 60 + 30, min(15 * 60, h * 60 + m + minutes))
        return f"{total // 60:02d}:{total % 60:02d}:00"
    start = shift(first, -20) if first else "09:30:00"
    end = shift(last, 5) if last else "15:00:00"
    return start, end


def score_day(bundle: dict, day: str = "1970-01-01") -> list[Candidate]:
    cands: dict[str, Candidate] = {}

    def get(code: str, name: str, sector: str) -> Candidate:
        if code not in cands:
            cands[code] = Candidate(ts_code=code, name=name, sector=sector, drama_score=0.0)
        return cands[code]

    max_board = max((int(t["board_count"]) for t in bundle.get("boards", [])), default=0)
    sector_counts: dict[str, int] = {}
    for r in bundle.get("pool", []):
        sector_counts[r.get("sector", "")] = sector_counts.get(r.get("sector", ""), 0) + 1
    amp_map = {a["ts_code"]: a["amplitude_pct"] for a in bundle.get("amplitude_top", [])}
    down_roots = {r["ts_code"].split(".")[0] for r in bundle.get("down", [])}
    top_broker_net = max((float(b.get("net_amount") or 0) for b in bundle.get("brokers", [])),
                         default=0.0)
    lhb_map = {str(r.get("code")): r for r in bundle.get("lhb_detail", [])}

    for r in bundle.get("pool", []):
        code, name, sector = r["ts_code"], r.get("name", ""), r.get("sector", "")
        c, notes = get(code, name, sector), []
        ref = f"stockhot.db:limit_up_pool@{day}:{code}"

        def add(kind: str, label: str, score: float, detail: dict) -> None:
            c.events.append(DramaEvent(kind, label, score, detail))
            c.drama_score += score
            notes.append(label)

        add("limit_up", f"涨停收盘({r.get('change_pct', 0):+.2f}%)",
            W["limit_up_base"], {"change_pct": r.get("change_pct")})
        c.facts.append(_fact(f"{code}_chg", abs(r.get("change_pct", 0)), "%",
                             f"{r.get('change_pct', 0):+.2f}%", day, ref))
        broken = int(r.get("broken_count") or 0)
        if broken:
            add("reseal", f"{broken} 度炸板后回封", W["reseal_per_broken"] * broken,
                {"broken_count": broken})
            c.facts.append(_fact(f"{code}_broken", broken, "次", f"{broken}次炸板", day, ref))
        last_seal = r.get("last_seal_time", "")
        if last_seal >= _LATE_SEAL:
            add("reseal_late", f"尾盘回封({last_seal[:5]})", W["reseal_late"],
                {"last_seal_time": last_seal})
            c.facts.append(_fact(f"{code}_lastseal", last_seal[:5].replace(":", ""),
                                 "", f"{last_seal[:5]}回封", day, ref))
        boards = int(r.get("consecutive_boards") or 1)
        c.facts.append(_fact(f"{code}_boards", boards, "板", f"{boards}连板", day, ref))
        if boards == max_board and max_board >= 2:
            add("ladder", f"积分榜最高板({boards}板)",
                W["ladder_top"] + W["ladder_extra_per_board"] * (boards - 1),
                {"boards": boards})
        elif boards >= 3:
            add("ladder", f"{boards}连板", W["streak_3plus"], {"boards": boards})
        if code.split(".")[0] in down_roots:
            add("earth_sky", "地天板级大逆转", W["earth_sky"], {})
        amp = amp_map.get(code)
        if amp is not None and amp >= _AMP_FOR_LONG_LEG:
            add("long_leg", f"大长腿(振幅{amp:.1f}%)", W["long_leg_amp"], {"amplitude_pct": amp})
            c.facts.append(_fact(f"{code}_amp", amp, "%", f"振幅{amp:.1f}%", day, ref))
        if code in bundle.get("lhb_codes", set()):
            add("lhb", "龙虎榜球星对位", W["lhb_listed"], {})
            net = float(lhb_map.get(code, {}).get("net_buy_amount") or 0)
            if abs(net) > 1e8:
                add("lhb", "亿元级席位净买(巨星对决)", W["lhb_big_broker"], {})
                c.facts.append(_fact(f"{code}_lhbnb", round(net / 1e8, 2), "亿",
                                     f"龙虎榜净买{net / 1e8:+.2f}亿", day, ref))
        c.replay_start, c.replay_end = _window(r.get("first_seal_time", ""), last_seal)
        c.notes = notes
        c.educational = sector_counts.get(sector, 0) >= _EDU_SECTOR_LIMITUPS

    for r in bundle.get("broken", []):
        code, name, sector = r["ts_code"], r.get("name", ""), r.get("sector", "")
        c = get(code, name, sector)
        broken = int(r.get("broken_count") or 1)
        c.events.append(DramaEvent("broken", f"收盘炸板({broken}次炸开)", 
                                   W["broken_close_base"] + W["reseal_per_broken"] * (broken - 1),
                                   {"broken_count": broken}))
        c.drama_score += W["broken_close_base"] + W["reseal_per_broken"] * (broken - 1)
        c.notes.append(f"收盘炸板(被帽戏码)")
        c.facts.append(_fact(f"{code}_brkclose", broken, "次", f"收盘仍炸板({broken}次)",
                             day, f"stockhot.db:broken_pool@{day}:{code}"))

    for a in bundle.get("amplitude_top", []):
        code = a["ts_code"]
        if code in cands:          # 已入池的由 pool 路径记 long_leg
            continue
        name = bundle.get("names", {}).get(code, "")
        c = get(code, name, "")
        c.events.append(DramaEvent("long_leg", f"大长腿(振幅{a['amplitude_pct']:.1f}%)",
                                   W["long_leg_amp"], {"amplitude_pct": a["amplitude_pct"]}))
        c.drama_score += W["long_leg_amp"]
        c.facts.append(_fact(f"{code}_amp", a["amplitude_pct"], "%",
                             f"振幅{a['amplitude_pct']:.1f}%", day,
                             f"market_data.db:daily_price@{day}:{code}"))

    return sorted(cands.values(), key=lambda c: -c.drama_score)


def select_candidates(bundle: dict, day: str = "1970-01-01",
                      max_count: int = 5, per_sector_cap: int = 2) -> list[Candidate]:
    cands = score_day(bundle, day)
    if not cands:
        return []
    picked: list[Candidate] = []
    sector_n: dict[str, int] = {}
    for c in cands:
        if len(picked) >= max_count:
            break
        if sector_n.get(c.sector, 0) >= per_sector_cap:
            continue
        picked.append(c)
        sector_n[c.sector] = sector_n.get(c.sector, 0) + 1
    if not any(c.educational for c in picked):     # 教育性保底:换入最高分教育候选
        edu = next((c for c in cands if c.educational and c not in picked), None)
        if edu and picked:
            picked[-1] = edu
    return picked
```

- [ ] **Step 4: 运行测试通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_selector.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/constants.py davis_analyzer/recap/selector.py davis_analyzer/tests/test_recap_selector.py
git commit -m "feat(recap): 选片引擎score_day/select_candidates+戏剧权重入constants"
```

---

### Task 4: validator.py 三道闸(数字/敏感词/完整性)

**Files:**
- Create: `davis_analyzer/recap/validator.py`
- Test: `davis_analyzer/tests/test_recap_validator.py`

**Interfaces:**
- Consumes: `cardgen.numbers.unmatched_tokens(text, facts: Iterable[cardgen.types.Fact])`、`cardgen.compliance.load_words()`;Task 1 `Episode`、`constants.EXTRA_SENSITIVE_WORDS`/`INDUCEMENT_PATTERNS_NARROW`/`REQUIRED_DISCLAIMER`。
- Produces: `validator.validate_episode(ep: Episode) -> list[str]`([] = 通过;否则中文失败项列表)。Task 5 的 LLM 自纠错循环与 cli 的人工闸都消费它。

- [ ] **Step 1: 写失败测试**

```python
# davis_analyzer/tests/test_recap_validator.py
"""recap 三道闸:数字全命中 facts / 敏感词 / 完整性+免责。"""
from __future__ import annotations

from davis_analyzer.recap.types import DialogueLine, Episode, EpisodeSegment


def _fact(fid, value, unit, display):
    return {"id": fid, "value": value, "unit": unit, "display": display,
            "as_of": "2026-09-18", "source": {"kind": "stockhot", "ref": f"x@{fid}"}}


def _ep(lines_open=None, lines_close=None, facts=None, segments=None):
    segs = segments if segments is not None else [
        EpisodeSegment("open", "scoreboard", None,
                       lines_open or [DialogueLine("pb", "今日战报,上证收3875点。")]),
        EpisodeSegment("s1", "stock", "605577.SH",
                       [DialogueLine("color", "5连板,3次炸板后回封,戏剧性拉满。")]),
        EpisodeSegment("close", "outlook", None,
                       lines_close or [DialogueLine("pb", "明日看点看天梯。本内容仅为盘面复盘记录,不构成投资建议。")]),
    ]
    return Episode("2026-09-18", "测试", segs,
                   facts if facts is not None else [
                       _fact("idx_sh_close", "3875", "点", "上证收3875点"),
                       _fact("605577.SH_boards", "5", "板", "5连板"),
                       _fact("605577.SH_broken", "3", "次", "3次炸板"),
                   ])


def test_clean_episode_passes():
    from davis_analyzer.recap.validator import validate_episode
    assert validate_episode(_ep(), min_seconds=5.0) == []


def test_number_not_in_facts_fails():
    from davis_analyzer.recap.validator import validate_episode
    ep = _ep(lines_open=[DialogueLine("pb", "上证大涨百分之2。")])
    fails = validate_episode(ep)
    assert any("数字" in f for f in fails)


def test_sensitive_word_fails():
    from davis_analyzer.recap.validator import validate_episode
    ep = _ep(lines_open=[DialogueLine("pb", "这位置可以上车,上证收3875点。")])
    fails = validate_episode(ep)
    assert any("敏感" in f for f in fails)


def test_missing_disclaimer_fails():
    from davis_analyzer.recap.validator import validate_episode
    ep = _ep(lines_close=[DialogueLine("pb", "明天见。")])
    fails = validate_episode(ep)
    assert any("不构成投资建议" in f for f in fails)


def test_bad_speaker_and_length_fails():
    from davis_analyzer.recap.validator import validate_episode
    segs = [EpisodeSegment("open", "scoreboard", None, [DialogueLine("narrator", "开场")]),
            EpisodeSegment("close", "outlook", None,
                           [DialogueLine("pb", "本内容仅为盘面复盘记录,不构成投资建议。" * 60)])]
    fails = validate_episode(Episode("2026-09-18", "t", segs, []))
    assert any("speaker" in f for f in fails)
    assert any("时长" in f for f in fails)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_validator.py -v`
Expected: FAIL `No module named ... validator`

- [ ] **Step 3: 实现**

```python
# davis_analyzer/recap/validator.py
"""recap 三道闸:①完整性(骨架/speaker/免责/时长预算) ②数字全命中 facts ③敏感词。"""
from __future__ import annotations

import re

from davis_analyzer.cardgen.compliance import load_words
from davis_analyzer.cardgen.numbers import unmatched_tokens
from davis_analyzer.cardgen.types import Fact
from davis_analyzer.recap.constants import (
    EXTRA_SENSITIVE_WORDS, INDUCEMENT_PATTERNS_NARROW, REQUIRED_DISCLAIMER,
)
from davis_analyzer.recap.types import Episode

_MIN_SECONDS, _MAX_SECONDS = 40.0, 125.0
_CHARS_PER_SECOND = 4.2          # edge-tts 中文语速经验值
_MAX_LINE_SECONDS = 20.0


def _facts_of(ep: Episode) -> list[Fact]:
    """facts + 无单位变体(解说常省略单位:「3876」vs fact「3876点」;只放宽单位、
    数值本身仍必须命中,LLM 编不出新数字)。不动 cardgen/numbers.py 共享代码。"""
    out: list[Fact] = []
    for d in ep.facts:
        f = Fact.from_dict(d)
        out.append(f)
        if f.unit:
            out.append(Fact(id=f"{f.id}_bare", value=f.value, unit="", display=f.display,
                            as_of=f.as_of, source_kind=f.source_kind, source_ref=f.source_ref))
    return out


def validate_episode(ep: Episode, min_seconds: float = _MIN_SECONDS,
                     max_seconds: float = _MAX_SECONDS) -> list[str]:
    fails: list[str] = []
    if not ep.segments:
        return ["剧本为空"]
    if ep.segments[0].kind != "scoreboard":
        fails.append("完整性: 首段必须为 scoreboard(片头比分牌)")
    if ep.segments[-1].kind != "outlook":
        fails.append("完整性: 末段必须为 outlook(明日看点)")

    facts = _facts_of(ep)
    words = set(load_words()) | set(EXTRA_SENSITIVE_WORDS)
    total_chars = 0
    for seg in ep.segments:
        for line in seg.lines:
            total_chars += len(line.text)
            if line.speaker not in ("pb", "color"):
                fails.append(f"完整性: {seg.seg_id} 出现非法 speaker={line.speaker!r}(只允许 pb/color)")
            unmatched = unmatched_tokens(line.text, facts)
            if unmatched:
                toks = ", ".join(t.raw for t in unmatched[:3])
                fails.append(f"数字闸: {seg.seg_id}[{line.speaker}] 数字未命中 facts: {toks}")
            for w in sorted(words):
                if w and w in line.text:
                    fails.append(f"敏感词: {seg.seg_id}[{line.speaker}] 命中「{w}」")
            for pat, desc in INDUCEMENT_PATTERNS_NARROW:
                if pat.search(line.text):
                    fails.append(f"敏感词: {seg.seg_id}[{line.speaker}] {desc}")
            if len(line.text) / _CHARS_PER_SECOND > _MAX_LINE_SECONDS:
                fails.append(f"完整性: {seg.seg_id}[{line.speaker}] 单句超{_MAX_LINE_SECONDS:.0f}s,请拆句")
    close_text = "".join(l.text for l in ep.segments[-1].lines)
    if REQUIRED_DISCLAIMER not in close_text:
        fails.append(f"完整性: 末段缺少免责原话「不构成投资建议」")
    est = total_chars / _CHARS_PER_SECOND
    if not (min_seconds <= est <= max_seconds):
        fails.append(f"完整性: 预计时长 {est:.0f}s 超出预算 {min_seconds:.0f}-{max_seconds:.0f}s")
    return fails
```

注意:数字闸用 cardgen 的 `Fact.from_dict`(Task 3 已产出同构 dict)。测试里「百分之2」的「2」是数字 token 且不在 facts → 命中。

- [ ] **Step 4: 运行测试通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_validator.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/recap/validator.py davis_analyzer/tests/test_recap_validator.py
git commit -m "feat(recap): validator三道闸(数字facts/敏感词/完整性+免责+时长预算)"
```

---

### Task 5: scriptwriter.py LLM 双解说剧本

**Files:**
- Create: `davis_analyzer/recap/scriptwriter.py`
- Test: `davis_analyzer/tests/test_recap_scriptwriter.py`

**Interfaces:**
- Consumes: Task 3 `Candidate`/bundle、Task 4 `validate_episode`、`constants.NBA_STYLE_TABLE`;LLM 客户端 `stockhot.advisor.llm_provider.get_provider()`(惰性 import,`provider.complete(prompt, system=..., max_tokens=..., temperature=...)` 返回带 `.content` 的对象)。
- Produces: `scriptwriter.ScriptGenError`、`scriptwriter.generate_episode(trade_date, cands: list[Candidate], bundle: dict, provider=None) -> Episode`(provider 可注入 mock;内置最多 2 次校验自纠错;失败抛 ScriptGenError,cli 保持 selected 状态)。Episode.facts = 比分牌 facts + 全部候选 facts(Task 9 数据卡复用)。

- [ ] **Step 1: 写失败测试**

```python
# davis_analyzer/tests/test_recap_scriptwriter.py
"""recap 剧本:prompt 注入/LLM JSON 解析/自纠错循环/Episode 装配。"""
from __future__ import annotations

import json

import pytest

from davis_analyzer.recap.scriptwriter import (
    ScriptGenError, assemble_episode, build_user_prompt, generate_episode, parse_episode_json,
)
from davis_analyzer.recap.types import Candidate


def _cand():
    return Candidate(
        ts_code="605577.SH", name="龙版传媒", sector="出版", drama_score=98.0,
        replay_start="09:27:00", replay_end="14:51:00",
        notes=["5 连板", "3 度炸板后回封"], educational=True,
        facts=[{"id": "605577.SH_boards", "value": "5", "unit": "板", "display": "5连板",
                "as_of": "2026-09-18", "source": {"kind": "stockhot", "ref": "r"}},
               {"id": "605577.SH_broken", "value": "3", "unit": "次", "display": "3次炸板",
                "as_of": "2026-09-18", "source": {"kind": "stockhot", "ref": "r"}}])


def _bundle():
    return {"index": [{"code": "000001.SH", "name": "上证指数", "close": 3875.6, "pct_chg": -0.411},
                      {"code": "399001.SZ", "name": "深证成指", "close": 12345.6, "pct_chg": 0.52},
                      {"code": "399006.SZ", "name": "创业板指", "close": 2710.2, "pct_chg": 0.85}],
            "breadth": {"up": 3200, "down": 1900}, "limit_up_count": 62,
            "boards": [{"board_count": 5, "stocks": [{"code": "605577.SH", "name": "龙版传媒"}]}],
            "names": {}, "pool": [], "broken": [], "down": [], "lhb_codes": set(),
            "lhb_detail": [], "brokers": [], "amplitude_top": []}


def _llm_json():
    return json.dumps({"title": "五连板封神与三度回封之夜", "segments": [
        {"seg_id": "open", "kind": "scoreboard", "ts_code": None, "lines": [
            {"speaker": "pb",
             "text": "今日战报,欢迎收看A股全场回放:上证收在3876点,下跌0.41%,"
                     "全场3200家上涨、1900家下跌,今晚的高光时刻一个比一个精彩。"}]},
        {"seg_id": "s1", "kind": "stock", "ts_code": "605577.SH", "lines": [
            {"speaker": "pb",
             "text": "看这段回放,5连板!第3次炸板,又给硬生生封了回去,这个统治力什么水平?"},
            {"speaker": "color",
             "text": "出版板块今天集体起立,资金抱团的意图非常明确,每一波炸板都被更坚决的买盘接住,"
                     "这就是今天最硬的高光时刻。"}]},
        {"seg_id": "close", "kind": "outlook", "ts_code": None, "lines": [
            {"speaker": "color",
             "text": "天梯的高度明天继续量,断板与晋级的故事还会上演。"
                     "本内容仅为盘面复盘记录,不构成投资建议。"}]},
    ]}, ensure_ascii=False)


class _FakeProvider:
    def __init__(self, contents: list[str]):
        self._contents = list(contents)
        self.calls: list[str] = []

    def complete(self, prompt: str, system: str = "", max_tokens: int = 800,
                 temperature: float = 0.3):
        self.calls.append(prompt)
        class _R:
            content = self._contents.pop(0)
        return _R()


def test_prompt_contains_facts_and_style():
    p = build_user_prompt([_cand()], _bundle())
    assert "5连板" in p and "605577.SH" in p
    assert "压哨绝杀" in p            # NBA 映射表注入
    assert "09:27" in p               # 回放窗注入


def test_parse_episode_json_extracts_from_markdown_fence():
    content = f"好的,以下是剧本:\n```json\n{_llm_json()}\n```"
    d = parse_episode_json(content)
    assert d["title"].startswith("五连板")


def test_generate_ok_and_validated():
    ep = generate_episode("2026-09-18", [_cand()], _bundle(),
                          provider=_FakeProvider([_llm_json()]))
    assert ep.segments[0].kind == "scoreboard"
    assert ep.facts and any(f["id"] == "idx_sh_close" for f in ep.facts)


def test_generate_retries_then_raises():
    bad = json.dumps({"title": "x", "segments": [
        {"seg_id": "open", "kind": "stock", "ts_code": None, "lines":
         [{"speaker": "pb", "text": "这票可以买入,涨3个点。"}]}]}, ensure_ascii=False)
    with pytest.raises(ScriptGenError):
        generate_episode("2026-09-18", [_cand()], _bundle(),
                         provider=_FakeProvider([bad, bad, bad]))


def test_assemble_rejects_unknown_segment():
    with pytest.raises(ScriptGenError):
        assemble_episode("2026-09-18", [_cand()], _bundle(),
                         {"title": "t", "segments": [
                             {"seg_id": "x", "kind": "wild", "ts_code": None, "lines": []}]})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_scriptwriter.py -v`
Expected: FAIL `No module named ... scriptwriter`

- [ ] **Step 3: 实现**

```python
# davis_analyzer/recap/scriptwriter.py
"""recap 剧本生成:LLM 双解说(NBA 风格),数字全注入、validator 自纠错循环。"""
from __future__ import annotations

import json
import re
from decimal import Decimal

from loguru import logger

from davis_analyzer.recap.constants import NBA_STYLE_TABLE
from davis_analyzer.recap.types import Candidate, DialogueLine, Episode, EpisodeSegment
from davis_analyzer.recap.validator import validate_episode

_MAX_ATTEMPTS = 2
_TEMPERATURE = 0.7


class ScriptGenError(RuntimeError):
    """剧本生成失败(LLM 不可用/解析失败/自纠错后仍不过闸)。"""


_SYSTEM = (
    "你是A股盘后复盘短视频的金牌编剧,风格对标NBA赛事转播解说。"
    "两位解说:pb=实况解说(激情,喊动作、短句、有画面感),color=嘉宾分析(娓娓道来,讲资金与板块逻辑)。"
    "铁律:①解说词中任何数字(点位/涨幅/连板数/炸板次数/振幅/金额/家数)只准使用【可用事实】清单里给出的数字,"
    "一个都不能自己编、不能换算;②只描述已发生的事实,不给任何操作建议;"
    "③末段(outlook)必须原话包含「不构成投资建议」;④输出只给一个JSON对象,不要多余文字。"
)


def _fact(fid: str, value, unit: str, display: str, day: str, ref: str) -> dict:
    s = format(Decimal(str(value)), "f")
    return {"id": fid, "value": s.rstrip("0").rstrip(".") if "." in s else s, "unit": unit,
            "display": display, "as_of": day, "source": {"kind": "stockhot", "ref": ref}}


def scoreboard_facts(bundle: dict, day: str) -> list[dict]:
    """片头比分牌 facts:三大指数 close/pct_chg + 涨跌家数 + 涨停家数。"""
    out: list[dict] = []
    for ix in bundle.get("index", []):
        key = {"000001.SH": "sh", "399001.SZ": "sz", "399006.SZ": "cyb"}[ix["code"]]
        # value 与 display 同口径取整(3875.6→3876):解说文本只能复述 display,
        # 若 value 存原始小数,数字闸会永远对不上
        out.append(_fact(f"idx_{key}_close", f"{ix['close']:.0f}", "点",
                         f"{ix['name']}{ix['close']:.0f}点", day,
                         f"market_data.db:index_daily@{day}:{ix['code']}:close"))
        out.append(_fact(f"idx_{key}_chg", f"{abs(ix['pct_chg']):.2f}", "%",
                         f"{ix['name']}{ix['pct_chg']:+.2f}%", day,
                         f"market_data.db:index_daily@{day}:{ix['code']}:pct_chg"))
    br = bundle.get("breadth", {})
    out.append(_fact("breadth_up", br.get("up", 0), "家", f"上涨{br.get('up', 0)}家", day,
                     f"market_data.db:daily_price@{day}:pct_chg>0"))
    out.append(_fact("breadth_down", br.get("down", 0), "家", f"下跌{br.get('down', 0)}家", day,
                     f"market_data.db:daily_price@{day}:pct_chg<0"))
    out.append(_fact("limit_up_count", bundle.get("limit_up_count", 0), "家",
                     f"涨停{bundle.get('limit_up_count', 0)}家", day,
                     f"stockhot.db:limit_up_pool@{day}:count"))
    return out


def build_user_prompt(cands: list[Candidate], bundle: dict) -> str:
    style = "\n".join(f"- {a} → {b}" for a, b in NBA_STYLE_TABLE)
    idx = ";".join(f"{i['name']} {i['close']:.0f}点 {i['pct_chg']:+.2f}%" for i in bundle["index"])
    lines = [
        f"## 今日战报(片头比分牌素材)\n{idx};上涨{bundle['breadth']['up']}家/下跌{bundle['breadth']['down']}家;"
        f"涨停{bundle['limit_up_count']}家。",
        "## 高光候选(按戏剧性降序)",
    ]
    for i, c in enumerate(cands, 1):
        facts = ";".join(f["display"] for f in c.facts) or "无"
        lines.append(
            f"{i}. {c.name}({c.ts_code},板块:{c.sector or '未知'}) 剧情点:{'、'.join(c.notes) or '常规'};"
            f"回放窗 {c.replay_start[:5]}-{c.replay_end[:5]};事实:{facts}"
            + (";教育性段落(讲板块逻辑)" if c.educational else ""))
    ladder = ";".join(
        f"{t['board_count']}板:" + ",".join(s.get("name", "?") for s in t["stocks"][:3])
        for t in bundle.get("boards", [])[:3])
    lines.append(f"## 连板天梯(积分榜)\n{ladder or '无'}")
    lines.append("## NBA 转播语言映射(风格参考,别逐字照搬)\n" + style)
    all_facts = scoreboard_facts(bundle, cands[0].facts[0]["as_of"] if cands and cands[0].facts else "1970-01-01") \
        + [f for c in cands for f in c.facts]
    lines.append("## 可用事实(数字唯一来源,逐条给出 value+unit)\n" +
                 "\n".join(f"- {f['display']}" for f in all_facts))
    lines.append(
        "## 输出 JSON schema(严格照此结构)\n"
        '{"title": "本期标题(15字内,有NBA味)", "segments": ['
        '{"seg_id": "open", "kind": "scoreboard", "ts_code": null, "lines": [{"speaker": "pb", "text": "..."}]},'
        '... 每只候选一个 {"seg_id": "s1", "kind": "stock", "ts_code": "605577.SH", '
        '"lines": [pb/color 交替 3-6 句,回放窗时间内讲故事]} ...,'
        '{"seg_id": "close", "kind": "outlook", "ts_code": null, '
        '"lines": [2-3句,末句含免责原话]}]}\n'
        "总时长预算:全部台词合计 180-500 字。")
    return "\n\n".join(lines)


def parse_episode_json(content: str) -> dict:
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        raise ScriptGenError(f"LLM 输出无 JSON 对象: {content[:120]!r}")
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ScriptGenError(f"LLM JSON 解析失败: {e}") from e
    if "segments" not in d or "title" not in d:
        raise ScriptGenError("LLM JSON 缺 title/segments")
    return d


def assemble_episode(trade_date: str, cands: list[Candidate], bundle: dict, data: dict) -> Episode:
    allowed_kinds = {"scoreboard", "stock", "outlook"}
    segs: list[EpisodeSegment] = []
    for s in data["segments"]:
        if s.get("kind") not in allowed_kinds:
            raise ScriptGenError(f"非法 segment kind={s.get('kind')!r}")
        segs.append(EpisodeSegment(
            seg_id=str(s.get("seg_id", "")), kind=s["kind"],
            ts_code=s.get("ts_code"),
            lines=[DialogueLine(speaker=l["speaker"], text=str(l["text"]).strip())
                   for l in s.get("lines", []) if l.get("text")]))
    facts = scoreboard_facts(bundle, trade_date) + [f for c in cands for f in c.facts]
    return Episode(trade_date=trade_date, title=str(data["title"]), segments=segs, facts=facts)


def generate_episode(trade_date: str, cands: list[Candidate], bundle: dict,
                     provider=None) -> Episode:
    if not cands:
        raise ScriptGenError("候选为空(冰点日应由调用方走降级剧本)")
    if provider is None:
        from stockhot.advisor.llm_provider import get_provider
        provider = get_provider()
    prompt = build_user_prompt(cands, bundle)
    last_fails: list[str] = []
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = provider.complete(prompt, system=_SYSTEM, max_tokens=2000,
                                     temperature=_TEMPERATURE)
            data = parse_episode_json(resp.content)
        except ScriptGenError as e:
            last_fails = [f"attempt {attempt}: {e}"]
            logger.warning(f"recap 剧本 attempt{attempt} 失败: {e}")
            continue
        ep = assemble_episode(trade_date, cands, bundle, data)
        last_fails = validate_episode(ep)
        if not last_fails:
            return ep
        logger.warning(f"recap 剧本 attempt{attempt} 未过闸: {last_fails[:3]}")
        prompt = (f"{build_user_prompt(cands, bundle)}\n\n"
                  f"## 上一稿未通过质检,必须修复以下问题后重写\n"
                  + "\n".join(f"- {f}" for f in last_fails))
    raise ScriptGenError(f"自纠错 {_MAX_ATTEMPTS} 次仍未过闸: {last_fails[:5]}")
```

- [ ] **Step 4: 运行测试通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_scriptwriter.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/recap/scriptwriter.py davis_analyzer/tests/test_recap_scriptwriter.py
git commit -m "feat(recap): scriptwriter LLM双解说剧本(facts注入+自纠错循环)"
```

---

### Task 6: recorder_sheet.py 录制单 + 文件名协议 + 飞书推送

**Files:**
- Create: `davis_analyzer/recap/recorder_sheet.py`
- Test: `davis_analyzer/tests/test_recap_sheet.py`

**Interfaces:**
- Consumes: `Episode`/`Candidate`;`constants.CLIP_FILENAME_RE`、`INBOX_DIR`;飞书模式照抄 `scripts/daily_market_cards.py:97-142`(EnterpriseFeishuNotifier + `FEISHU_XHS_CHAT_ID` + 幂等锁)。
- Produces: `recorder_sheet.parse_clip_filename(name) -> tuple[str, str, int] | None`、`recorder_sheet.build_sheet_markdown(ep, cands) -> str`、`recorder_sheet.push_sheet(day_dash, markdown, dry_run=False) -> bool`、`recorder_sheet.match_clips(day_dash, ep) -> tuple[dict[str, Path], list[str]]`(seg_id `s1..sN` ↔ 序号 `01..N`;返回对位表+缺失清单)。Task 8 的 make_pack 消费 match_clips。

- [ ] **Step 1: 写失败测试**

```python
# davis_analyzer/tests/test_recap_sheet.py
"""recap 录制单:文件名协议/单据生成/素材对位/推送幂等。"""
from __future__ import annotations

import json

from davis_analyzer.recap import recorder_sheet as rs
from davis_analyzer.recap.types import Candidate, Episode


def _ep():
    return Episode.from_dict({
        "trade_date": "2026-09-18", "title": "测试之夜",
        "facts": [],
        "segments": [
            {"seg_id": "open", "kind": "scoreboard", "ts_code": None, "lines": []},
            {"seg_id": "s1", "kind": "stock", "ts_code": "605577.SH", "lines": []},
            {"seg_id": "s2", "kind": "stock", "ts_code": "001216.SZ", "lines": []},
            {"seg_id": "close", "kind": "outlook", "ts_code": None, "lines": []},
        ]})


def _cands():
    return [Candidate(ts_code="605577.SH", name="龙版传媒", sector="出版", drama_score=98,
                      replay_start="09:27:00", replay_end="14:51:00", notes=["5连板", "3度炸板回封"]),
            Candidate(ts_code="001216.SZ", name="华瓷股份", sector="陶瓷", drama_score=70,
                      replay_start="09:30:00", replay_end="15:00:00", notes=["首板"])]


def test_parse_clip_filename():
    assert rs.parse_clip_filename("20260918_605577.SH_01.mp4") == ("20260918", "605577.SH", 1)
    assert rs.parse_clip_filename("20260918_605577_01.mp4") is None
    assert rs.parse_clip_filename("xx.mp4") is None


def test_sheet_markdown_contains_ops_info():
    md = rs.build_sheet_markdown(_ep(), _cands())
    assert "605577.SH" in md and "龙版传媒" in md
    assert "09:27-14:51" in md           # 回放窗
    assert "20260918_605577.SH_01.mp4" in md   # 文件名协议指令
    assert "保存为" in md


def test_match_clips(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "INBOX_DIR", tmp_path)
    (tmp_path / "20260918_605577.SH_01.mp4").write_bytes(b"x")
    (tmp_path / "20260918_001216.SZ_02.mp4").write_bytes(b"x")
    matched, missing = rs.match_clips("2026-09-18", _ep())
    assert matched["s1"].name == "20260918_605577.SH_01.mp4"
    assert matched["s2"].name == "20260918_001216.SZ_02.mp4"
    assert missing == []


def test_match_clips_reports_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "INBOX_DIR", tmp_path)
    matched, missing = rs.match_clips("2026-09-18", _ep())
    assert "s1" in missing and "s2" in missing
    assert matched == {}


def test_push_sheet_dry_run_and_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "REPO_ROOT", tmp_path)
    assert rs.push_sheet("2026-09-18", "# 单", dry_run=True) is True
    lock = tmp_path / "logs" / ".recap_sheet" / "2026-09-18.ok"
    assert not lock.exists()          # dry_run 不落锁
    # 无 FEISHU 环境变量:跳过推送但成功返回(与 push_one 同口径)
    monkeypatch.delenv("FEISHU_XHS_CHAT_ID", raising=False)
    assert rs.push_sheet("2026-09-18", "# 单") is True
    assert lock.exists()              # 幂等锁已落
    assert rs.push_sheet("2026-09-18", "# 单") is True   # 二次调用走锁跳过
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_sheet.py -v`
Expected: FAIL `No module named ... recorder_sheet`

- [ ] **Step 3: 实现**

```python
# davis_analyzer/recap/recorder_sheet.py
"""recap 录制任务单:生成 markdown、飞书推送(幂等)、素材文件名协议对位。"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from loguru import logger

from davis_analyzer.recap.constants import CLIP_FILENAME_RE, INBOX_DIR, REPO_ROOT
from davis_analyzer.recap.types import Candidate, Episode

def _lock_path(day_dash: str) -> Path:
    """运行时解析(测试可 monkeypatch REPO_ROOT;勿提升为模块级常量——import 时固化)。"""
    return REPO_ROOT / "logs" / ".recap_sheet" / f"{day_dash}.ok"


def parse_clip_filename(name: str) -> tuple[str, str, int] | None:
    m = CLIP_FILENAME_RE.match(name)
    if not m:
        return None
    return m.group(1), m.group(2), int(m.group(3))


def build_sheet_markdown(ep: Episode, cands: list[Candidate]) -> str:
    day_compact = ep.trade_date.replace("-", "")
    stock_cands = {c.ts_code: c for c in cands}
    lines = [f"## {ep.trade_date} 录制单 · {ep.title}",
             "", f"素材投递目录: `davis_analyzer/recap/inbox/{ep.trade_date}/`(一票一文件)", ""]
    stock_segs = [s for s in ep.segments if s.kind == "stock"]
    for i, seg in enumerate(stock_segs, 1):
        c = stock_cands.get(seg.ts_code)
        if c is None:
            continue
        lines += [
            f"{i}️⃣ {c.ts_code} {c.name}(板块:{c.sector or '未知'})",
            f"   剧情点: {'、'.join(c.notes) or '常规涨停'}",
            "   App路径: 搜索代码 → 分时 → 盘口回放",
            f"   回放区间: {c.replay_start[:5]}-{c.replay_end[:5]} | 建议倍速: 1x",
            f"   保存为: {day_compact}_{c.ts_code}_{i:02d}.mp4", "",
        ]
    lines.append("录完把文件丢进 inbox 目录,然后跑 `python -m davis_analyzer.recap audio`。")
    return "\n".join(lines)


def match_clips(day_dash: str, ep: Episode) -> tuple[dict[str, Path], list[str]]:
    """inbox 文件名对位剧本段落:s1↔_01 ... sN↔_N;返回 (对位表, 缺失 seg_id 清单)。"""
    day_compact = day_dash.replace("-", "")
    day_dir = INBOX_DIR / day_dash
    stock_segs = [s for s in ep.segments if s.kind == "stock"]
    matched: dict[str, Path] = {}
    if day_dir.exists():
        for p in sorted(day_dir.glob("*.mp4")):
            parsed = parse_clip_filename(p.name)
            if not parsed or parsed[0] != day_compact:
                logger.warning(f"recap inbox 未识别文件名: {p.name}")
                continue
            _, ts_code, seq = parsed
            seg = next((s for s in stock_segs
                        if s.ts_code == ts_code and s.seg_id == f"s{seq}"), None)
            if seg is None:
                seg = next((s for s in stock_segs if s.ts_code == ts_code), None)
            if seg is not None:
                matched[seg.seg_id] = p
    missing = [s.seg_id for s in stock_segs if s.seg_id not in matched]
    return matched, missing


def push_sheet(day_dash: str, markdown: str, dry_run: bool = False) -> bool:
    """推录制单到红薯运营群(纯文本);幂等锁 logs/.recap_sheet/{day}.ok;失败不阻断。"""
    lock = _lock_path(day_dash)
    if lock.exists():
        logger.info(f"recap 录制单 {day_dash} 已推过,跳过")
        return True
    if not dry_run:
        try:
            import asyncio
            from dotenv import load_dotenv
            load_dotenv(REPO_ROOT / ".env")
            chat = os.environ.get("FEISHU_XHS_CHAT_ID", "")
            if chat:
                from stockhot.notification.feishu_bot import EnterpriseFeishuNotifier

                async def _send() -> None:
                    n = EnterpriseFeishuNotifier(os.environ["FEISHU_APP_ID"],
                                                 os.environ["FEISHU_APP_SECRET"], chat)
                    # 无 tags 场景;若日后加话题标签,必须保持最后一行
                    await n.send_text(f"【{day_dash} 复盘视频录制单·照单录,发布人工】\n\n{markdown}")

                asyncio.run(_send())
            else:
                logger.info("未配置 FEISHU_XHS_CHAT_ID,跳过录制单推送")
        except Exception as e:  # noqa: BLE001 —— 推送失败不阻断流程
            logger.warning(f"录制单推送失败({e!r})")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    return True
```

- [ ] **Step 4: 运行测试通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_sheet.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/recap/recorder_sheet.py davis_analyzer/tests/test_recap_sheet.py
git commit -m "feat(recap): 录制单生成+文件名协议对位+飞书推送幂等"
```

---

### Task 7: cli 串联(run/select/script/sheet)+ 冰点降级 + systemd timer + AGENTS.md

**Files:**
- Modify: `davis_analyzer/recap/cli.py`(扩子命令)
- Create: `~/.config/systemd/user/recap-run.service`、`~/.config/systemd/user/recap-run.timer`
- Modify: `davis_analyzer/AGENTS.md`(模块划分段新增 recap 段落,插在「每日复盘卡」段之后、`**输出层**` 之前)
- Test: `davis_analyzer/tests/test_recap_cli.py`

**Interfaces:**
- Consumes: Task 2-6 全部(`fetch_bundle`/`select_candidates`/`generate_episode`/`validate_episode`/`build_sheet_markdown`/`push_sheet`/db)。
- Produces: `python -m davis_analyzer.recap run [--date YYYY-MM-DD]`(= select→script→sheet 一条龙,写 `episodes/{day}/candidates.json`+`episode.json`+`facts.json`+`录制单.md`,台账状态推进,任何一步失败非零退出);子命令 `select`/`script`/`sheet` 可单独重跑。冰点日(无候选)生成极简剧本(代码内置模板,不走 LLM)并照常推单。

- [ ] **Step 1: 写失败测试**

```python
# davis_analyzer/tests/test_recap_cli.py
"""recap CLI:run 串联/冰点降级/台账推进/文件落盘。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """注入:内存台账 conn + 假 bundle + 假 LLM + 假推送。"""
    from davis_analyzer.recap import cli
    conn = sqlite3.connect(":memory:")
    from davis_analyzer.recap import db
    db.ensure_tables(conn)
    monkeypatch.setattr(cli, "_conn", lambda: conn)
    monkeypatch.setattr(cli, "EPISODES_DIR", tmp_path / "episodes")
    bundle = {
        "pool": [{"ts_code": "605577.SH", "name": "龙版传媒", "sector": "出版",
                  "change_pct": 9.97, "consecutive_boards": 5, "broken_count": 3,
                  "first_seal_time": "09:47:00", "last_seal_time": "14:46:00",
                  "turnover_rate": 11.9}],
        "broken": [], "down": [],
        "boards": [{"board_count": 5, "stocks": [{"code": "605577.SH", "name": "龙版传媒"}]}],
        "lhb_codes": set(), "lhb_detail": [], "brokers": [],
        "index": [{"code": "000001.SH", "name": "上证指数", "close": 3875.6, "pct_chg": -0.411},
                  {"code": "399001.SZ", "name": "深证成指", "close": 12345.6, "pct_chg": 0.52},
                  {"code": "399006.SZ", "name": "创业板指", "close": 2710.2, "pct_chg": 0.85}],
        "amplitude_top": [], "breadth": {"up": 3200, "down": 1900},
        "names": {}, "limit_up_count": 1,
    }
    monkeypatch.setattr(cli.data, "fetch_bundle", lambda day: bundle)
    pushed: list[str] = []
    monkeypatch.setattr(cli.sheet, "push_sheet", lambda day, md, dry_run=False:
                        pushed.append(day) or True)

    class _OK:
        content = json.dumps({"title": "五连板之夜", "segments": [
            {"seg_id": "open", "kind": "scoreboard", "ts_code": None, "lines": [
                {"speaker": "pb", "text": "今日战报:上证收在3876点,下跌0.41%,"
                                          "全场3200家上涨、1900家下跌。"}]},
            {"seg_id": "s1", "kind": "stock", "ts_code": "605577.SH", "lines": [
                {"speaker": "pb", "text": "看这段回放,5连板!第3次炸板又硬生生封回去,"
                                          "统治力拉满,这就是今天最硬的高光时刻。"},
                {"speaker": "color", "text": "出版板块今天集体起立,资金抱团意图明确,"
                                             "每一波炸板都被更坚决的买盘接住。"}]},
            {"seg_id": "close", "kind": "outlook", "ts_code": None, "lines": [
                {"speaker": "color", "text": "天梯高度明天继续量,断板与晋级的故事还会上演。"
                                             "本内容仅为盘面复盘记录,不构成投资建议。"}]},
        ]}, ensure_ascii=False)

    class _Provider:
        def complete(self, prompt, system="", max_tokens=800, temperature=0.3):
            return _OK()

    # 先取原函数再包装(直接在 lambda 里再调 cli.scriptwriter.generate_episode 会递归)
    _orig_gen = cli.scriptwriter.generate_episode
    monkeypatch.setattr(
        cli.scriptwriter, "generate_episode",
        lambda day, cands, b, provider=None: _orig_gen(day, cands, b, provider=_Provider()))
    return {"conn": conn, "tmp": tmp_path, "pushed": pushed, "bundle": bundle}


def test_run_full_flow(env):
    from davis_analyzer.recap import cli, db
    args = cli.build_parser().parse_args(["run", "--date", "2026-09-18"])
    args.func(args)
    row = db.get_episode(env["conn"], "2026-09-18")
    assert row["status"] == "sheeted"
    assert env["pushed"] == ["2026-09-18"]
    ep_dir = env["tmp"] / "episodes" / "2026-09-18"
    assert (ep_dir / "episode.json").exists()
    assert (ep_dir / "candidates.json").exists()
    assert (ep_dir / "facts.json").exists()
    assert (ep_dir / "录制单.md").exists()


def test_run_ice_day_degrades(env, monkeypatch):
    """冰点日:无候选 → 内置极简剧本照常推单。"""
    from davis_analyzer.recap import cli, db
    empty = dict(env["bundle"], pool=[], boards=[], amplitude_top=[], limit_up_count=0,
                 broken=[], down=[])
    monkeypatch.setattr(cli.data, "fetch_bundle", lambda day: empty)
    args = cli.build_parser().parse_args(["run", "--date", "2026-09-18"])
    args.func(args)
    row = db.get_episode(env["conn"], "2026-09-18")
    assert row["status"] == "sheeted"
    ep = json.loads((env["tmp"] / "episodes" / "2026-09-18" / "episode.json").read_text("utf-8"))
    assert ep["segments"][0]["kind"] == "scoreboard"
    assert "不构成投资建议" in ep["segments"][-1]["lines"][-1]["text"]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_cli.py -v`
Expected: FAIL(`run` 子命令不存在)

- [ ] **Step 3: 实现 cli.py 完整版**

将 `davis_analyzer/recap/cli.py` 整体替换为:

```python
# davis_analyzer/recap/cli.py
"""recap CLI:python -m davis_analyzer.recap {run|select|script|sheet|audio|post|status}。"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from loguru import logger

from davis_analyzer.recap import data, recorder_sheet as sheet, scriptwriter  # noqa: F401 (测试 monkeypatch 锚点)
from davis_analyzer.recap.constants import EPISODES_DIR
from davis_analyzer.recap.types import Candidate, DialogueLine, Episode, EpisodeSegment


def _conn():
    from stockhot.data_layer.market_db import get_connection
    return get_connection()


# ── 编排助手 ────────────────────────────────────────────────────────────

def _ep_dir(day: str) -> Path:
    d = EPISODES_DIR / day
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dump(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _do_select(day: str) -> list[Candidate]:
    from davis_analyzer.recap import db, selector
    bundle = data.fetch_bundle(day)               # 缺数据 → DailyDataMissing,非零退出
    cands = selector.select_candidates(bundle, day=day)
    conn = _conn()
    try:
        db.ensure_tables(conn)
        prev = db.get_episode(conn, day) or {}
        db.save_episode(conn, {"trade_date": day, "status": "selected",
                               "candidates_json": json.dumps(
                                   [asdict(c) for c in cands], ensure_ascii=False),
                               "episode_json": prev.get("episode") and json.dumps(
                                   prev["episode"], ensure_ascii=False),
                               "facts_json": prev.get("facts") and json.dumps(
                                   prev["facts"], ensure_ascii=False)})
    finally:
        conn.close()
    _dump(_ep_dir(day) / "candidates.json", [asdict(c) for c in cands])
    print(f"select: {len(cands)} 只候选" + ("" if cands else "(冰点日,走降级剧本)"))
    return cands


def _ice_episode(day: str, bundle: dict) -> Episode:
    """冰点日内置极简剧本:比分牌+一段嘉宾点评(数字与 facts 同口径取整)。"""
    facts = scriptwriter.scoreboard_facts(bundle, day)
    idx = "、".join(f"{i['name']}收在{i['close']:.0f}点({i['pct_chg']:+.2f}%)"
                    for i in bundle["index"])
    up, down = bundle["breadth"]["up"], bundle["breadth"]["down"]
    lu = bundle["limit_up_count"]
    return Episode(trade_date=day, title="今日无战事", facts=facts, segments=[
        EpisodeSegment("open", "scoreboard", None, [DialogueLine(
            "pb", f"今日战报,欢迎收看A股全场回放:{idx}。全场上涨{up}家、下跌{down}家,"
                  f"涨停{lu}家——今晚的集锦室有点空,但比分牌还是要念的。")]),
        EpisodeSegment("close", "outlook", None, [
            DialogueLine("color",
                         "没有高光时刻的日子,也是市场周期的一部分;缩量与分歧之后,"
                         "故事往往在无人注意时重新开始。本内容仅为盘面复盘记录,不构成投资建议。")]),
    ])


def _do_script(day: str, cands: list[Candidate]) -> Episode:
    from davis_analyzer.recap import db
    from davis_analyzer.recap.validator import validate_episode
    bundle = data.fetch_bundle(day)
    ep = (scriptwriter.generate_episode(day, cands, bundle) if cands
          else _ice_episode(day, bundle))
    # 冰点模板是极简版,时长下限放宽;常规剧本 40s 起步
    fails = validate_episode(ep, min_seconds=15.0 if not cands else 40.0)
    if fails:
        raise SystemExit(f"剧本未过闸(冰点模板也须过闸): {fails[:5]}")
    conn = _conn()
    try:
        db.ensure_tables(conn)
        db.update_status(conn, day, "scripted")
    finally:
        conn.close()
    _dump(_ep_dir(day) / "episode.json", ep.to_dict())
    _dump(_ep_dir(day) / "facts.json", {"facts": ep.facts})
    print(f"script: {ep.title}({len(ep.segments)} 段,{sum(len(s.lines) for s in ep.segments)} 句)")
    return ep


def _do_sheet(day: str, ep: Episode, cands: list[Candidate]) -> None:
    from davis_analyzer.recap import db
    md = sheet.build_sheet_markdown(ep, cands)
    (_ep_dir(day) / "录制单.md").write_text(md, encoding="utf-8")
    sheet.push_sheet(day, md)
    conn = _conn()
    try:
        db.ensure_tables(conn)
        db.update_status(conn, day, "sheeted")
    finally:
        conn.close()
    print("sheet: 录制单已生成并推送(若配置飞书)")


# ── 子命令 ──────────────────────────────────────────────────────────────

def cmd_run(args) -> None:
    cands = _do_select(args.date)
    ep = _do_script(args.date, cands)
    _do_sheet(args.date, ep, cands)


def cmd_select(args) -> None:
    _do_select(args.date)


def cmd_script(args) -> None:
    from davis_analyzer.recap.types import Candidate
    p = _ep_dir(args.date) / "candidates.json"
    if not p.exists():
        raise SystemExit(f"先跑 select: 缺 {p}")
    cands = [Candidate(**d) for d in json.loads(p.read_text(encoding="utf-8"))]
    _do_script(args.date, cands)


def cmd_sheet(args) -> None:
    p = _ep_dir(args.date) / "episode.json"
    if not p.exists():
        raise SystemExit(f"先跑 script: 缺 {p}")
    ep = Episode.from_dict(json.loads(p.read_text(encoding="utf-8")))
    cp = _ep_dir(args.date) / "candidates.json"
    cands = ([Candidate(**d) for d in json.loads(cp.read_text(encoding="utf-8"))]
             if cp.exists() else [])
    _do_sheet(args.date, ep, cands)


def cmd_audio(args) -> None:   # Task 8 实现
    from davis_analyzer.recap.audio_pack import make_pack
    out = make_pack(args.date)
    print(f"audio: 原料包 → {out}")


def cmd_post(args) -> None:    # 二期(Task 11)实现
    from davis_analyzer.recap.post_compose import compose
    print(f"post: {compose(args.date)}")


def cmd_status(args) -> None:
    from davis_analyzer.recap import db
    conn = _conn()
    try:
        db.ensure_tables(conn)
        row = db.get_episode(conn, args.date)
    finally:
        conn.close()
    if not row:
        print(f"{args.date}: 无台账(未选片)")
        return
    seg_n = len((row.get("episode") or {}).get("segments", []))
    print(f"{args.date}: status={row['status']} "
          f"candidates={len(row.get('candidates') or [])} segments={seg_n}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recap", description="每晚NBA解说式复盘短视频")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_, func in (
        ("run", "一条龙:select→script→sheet(timer 入口)", cmd_run),
        ("select", "选片(戏剧性评分)", cmd_select),
        ("script", "生成剧本(需先 select)", cmd_script),
        ("sheet", "生成+推送录制单(需先 script)", cmd_sheet),
        ("audio", "生成原料包(需 inbox 素材)", cmd_audio),
        ("post", "(二期)自动合成成片", cmd_post),
        ("status", "查看台账", cmd_status),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
        sp.set_defaults(func=func)
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)
```

要点:①`data`/`sheet`/`scriptwriter` 三个锚点 import 已在文件顶部,子命令函数内**不得再局部 import 这三个名字**(局部 import 会绕过测试的 monkeypatch);②候选序列化用 `dataclasses.asdict`——嵌套 `DramaEvent` 一并转 dict,`c.__dict__` 会留下 dataclass 对象导致 json.dumps 崩;③`cmd_script` 读回 candidates dict 直接 `Candidate(**d)` 构造(events 为 list[dict],鸭子类型够用,录制单只读 notes/facts)。

- [ ] **Step 4: 运行测试通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_cli.py davis_analyzer/tests/test_recap_db.py -v`
Expected: 全部 passed(cmd_audio/cmd_post 此时尚未实现,不在测试范围)

- [ ] **Step 5: systemd timer + AGENTS.md**

写 `~/.config/systemd/user/recap-run.service`:

```ini
[Unit]
Description=复盘视频:选片+剧本+录制单(每晚NBA解说式复盘)

[Service]
Type=oneshot
WorkingDirectory=/home/leo/Projects/CodeAgentDashboard
ExecStart=/home/leo/Projects/CodeAgentDashboard/.venv/bin/python -m davis_analyzer.recap run
StandardOutput=append:/home/leo/Projects/CodeAgentDashboard/logs/recap_run.log
StandardError=append:/home/leo/Projects/CodeAgentDashboard/logs/recap_run.log
```

写 `~/.config/systemd/user/recap-run.timer`:

```ini
[Unit]
Description=复盘视频选片剧本录制单(工作日19:40,温度计19:35错峰)

[Timer]
OnCalendar=Mon..Fri *-*-* 19:40:00
Persistent=true

[Install]
WantedBy=timers.target
```

启用:

```bash
systemctl --user daemon-reload
systemctl --user enable --now recap-run.timer
systemctl --user list-timers recap-run.timer
```

在 `davis_analyzer/AGENTS.md` 「每日复盘卡」段之后、`**输出层**` 之前插入(格式照 thermometer 段):

```markdown
**复盘视频子系统**(独立):`recap/`(每晚NBA解说式热点复盘短视频:选片→剧本→录制单→人工手机App盘口回放录屏→原料包,CLI: python -m davis_analyzer.recap {run|select|script|sheet|audio|post|status})。选片=「节目效果分」非投资分,权重单一真相源 constants.py RECAP_DRAMA_WEIGHTS(2026-09-18 spec);数字全锚 facts(cardgen Fact 复用+unmatched_tokens 机器闸),解说=资讯复盘不荐股、末段必含「不构成投资建议」、敏感词=cardgen 词表+recap 附加词(不复用 cardgen 第二人称正则,解说文体允许「你看」);素材文件名协议 `{YYYYMMDD}_{ts_code}_{NN}.mp4` 一票一文件丢 recap/inbox/{day}/;**调度**:user systemd timer recap-run 工作日19:40(select→script→sheet→飞书推单,发布永远人工);台账号 market_data.db recap_episodes(模式B自管表)。设计spec见 docs/superpowers/specs/2026-09-18-recap-video-design.md,实施计划 docs/superpowers/plans/2026-09-18-recap-phase1.md。
```

- [ ] **Step 6: 手动烟测一次 run 并提交**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m davis_analyzer.recap run --date 2026-09-17`(用最近真实交易日;LLM 真调用一次,检查 episodes/2026-09-17/ 四件套)

```bash
git add davis_analyzer/recap/cli.py davis_analyzer/tests/test_recap_cli.py davis_analyzer/AGENTS.md
git commit -m "feat(recap): run一条龙串联+冰点降级+systemd timer+AGENTS.md段落"
```

(systemd 单元文件在 ~/.config,不进 git,记录于本计划即可)

---

### Task 8: audio_pack.py 原料包(双TTS/SRT/守恒校验/拼接说明)

**Files:**
- Create: `davis_analyzer/recap/audio_pack.py`
- Test: `davis_analyzer/tests/test_recap_audio.py`

**Interfaces:**
- Consumes: `cardgen.video` 的 `ffmpeg()` 与 `audio_duration(path) -> float`(直接 import 复用);`edge_tts.Communicate(text, voice).save(path)`;`recorder_sheet.match_clips`;`constants.VOICE_PB`/`VOICE_COLOR`;Episode/candidates 文件(Task 7 落盘格式)。
- Produces: `audio_pack.make_pack(day_dash) -> Path`(产出 `episodes/{day}/原料包/`:`audio/{seg_id}_{i}_{speaker}.mp3`、`durations.json`、`字幕.srt`、`拼接说明.md`、`音频守恒报告.txt`);子过程函数 `synth_lines(ep, outdir) -> list[dict]`(每句一条,含时长)、`build_srt(timings) -> str`、`fit_report(ep, timings, clip_dur) -> list[str]`。Task 10 在 make_pack 末尾接入视觉质检。

- [ ] **Step 1: 写失败测试**(TTS/ffmpeg 全 mock,不碰网络)

```python
# davis_analyzer/tests/test_recap_audio.py
"""recap 原料包:SRT 时间轴/守恒校验/对位报告(TTS 全 mock)。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from davis_analyzer.recap import audio_pack as ap
from davis_analyzer.recap.types import Episode


def _ep() -> Episode:
    return Episode.from_dict({
        "trade_date": "2026-09-18", "title": "t", "facts": [],
        "segments": [
            {"seg_id": "open", "kind": "scoreboard", "ts_code": None, "lines": [
                {"speaker": "pb", "text": "第一句开场白。"}]},
            {"seg_id": "s1", "kind": "stock", "ts_code": "605577.SH", "lines": [
                {"speaker": "pb", "text": "五连板!"},
                {"speaker": "color", "text": "出版板块集体起立。"}]},
            {"seg_id": "close", "kind": "outlook", "ts_code": None, "lines": [
                {"speaker": "color", "text": "本内容仅为盘面复盘记录,不构成投资建议。"}]},
        ]})


def test_build_srt_cumulative_timeline():
    timings = [
        {"seg_id": "open", "speaker": "pb", "text": "第一句开场白。", "dur": 2.0, "file": "a.mp3"},
        {"seg_id": "open", "speaker": "pb", "text": "(第二句)", "dur": 3.0, "file": "b.mp3"},
    ]
    srt = ap.build_srt(timings)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,000")
    assert "00:00:02,200 --> 00:00:05,200" in srt   # 0.2s 句间隙


def test_fit_report_flags_short_clip():
    ep = _ep()
    timings = [{"seg_id": "s1", "speaker": "pb", "text": "x", "dur": 9.0, "file": "f"},
               {"seg_id": "s1", "speaker": "color", "text": "y", "dur": 9.5, "file": "f"}]
    clip_dur = {"s1": 12.0}   # 解说 18.5s > 素材 12s
    rep = ap.fit_report(ep, timings, clip_dur)
    assert any("s1" in r and ("慢放" in r or "砍" in r) for r in rep)
    assert ap.fit_report(ep, timings, {"s1": 30.0}) == []


def test_make_pack_end_to_end(tmp_path, monkeypatch):
    # episodes/{day}/episode.json + inbox 素材 + mock TTS/时长
    day = "2026-09-18"
    ep_dir = tmp_path / "episodes" / day
    ep_dir.mkdir(parents=True)
    (ep_dir / "episode.json").write_text(json.dumps(_ep().to_dict(), ensure_ascii=False), "utf-8")
    inbox = tmp_path / "inbox" / day
    inbox.mkdir(parents=True)
    (inbox / "20260918_605577.SH_01.mp4").write_bytes(b"fake")
    from davis_analyzer.recap import recorder_sheet as rs
    monkeypatch.setattr(rs, "INBOX_DIR", tmp_path / "inbox")  # match_clips 在 recorder_sheet 内读自己的 INBOX_DIR

    async def fake_communicate(text, voice):
        class _C:
            async def save(self, path):
                Path(path).write_bytes(b"mp3")
        return _C()

    import edge_tts
    monkeypatch.setattr(edge_tts, "Communicate", fake_communicate)
    monkeypatch.setattr(ap, "audio_duration", lambda p: 2.0)   # 每句固定 2s
    monkeypatch.setattr(ap, "EPISODES_DIR", tmp_path / "episodes")
    monkeypatch.setattr(ap, "INBOX_DIR", tmp_path / "inbox")

    out = ap.make_pack(day)
    assert (out / "字幕.srt").exists()
    assert (out / "durations.json").exists()
    assert (out / "拼接说明.md").exists()
    assert (out / "音频守恒报告.txt").exists()
    durs = json.loads((out / "durations.json").read_text("utf-8"))
    assert durs["segments"]["s1"] == pytest.approx(4.2)   # 两句 2s + 2×0.2 间隙? → 4.4-0.2
```

注意 `durations.json` 结构:`{"lines": [timings...], "segments": {seg_id: 段合计(含句间隙, 段尾去间隙)}}`。`s1` 两句 2.0+2.0,间隙 0.2×2,段尾去尾隙 → 2.0+0.2+2.0 = 4.2。实现按此口径。

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_audio.py -v`
Expected: FAIL `No module named ... audio_pack`

- [ ] **Step 3: 实现**

```python
# davis_analyzer/recap/audio_pack.py
"""recap 原料包:双TTS分段配音 + SRT 字幕 + 音画守恒校验 + 拼接说明。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger

from davis_analyzer.cardgen.video import audio_duration, ffmpeg  # noqa: F401 (ffmpeg 复用)
from davis_analyzer.recap.constants import EPISODES_DIR, INBOX_DIR, VOICE_COLOR, VOICE_PB
from davis_analyzer.recap.types import Episode

_GAP = 0.2           # 句间隙
VOICE_MAP = {"pb": VOICE_PB, "color": VOICE_COLOR}


def synth_lines(ep: Episode, outdir: Path) -> list[dict]:
    """逐句 TTS(edge-tts),返回 timings(含 dur/file)。"""
    import edge_tts

    timings: list[dict] = []
    outdir.mkdir(parents=True, exist_ok=True)
    for seg in ep.segments:
        for i, line in enumerate(seg.lines):
            rel = f"audio/{seg.seg_id}_{i:02d}_{line.speaker}.mp3"
            path = outdir / rel
            asyncio.run(edge_tts.Communicate(line.text, VOICE_MAP[line.speaker]).save(str(path)))
            timings.append({"seg_id": seg.seg_id, "speaker": line.speaker,
                            "text": line.text, "dur": audio_duration(path), "file": rel})
    return timings


def _fmt_ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


def build_srt(timings: list[dict]) -> str:
    blocks, t = [], 0.0
    for i, item in enumerate(timings, 1):
        blocks.append(f"{i}\n{_fmt_ts(t)} --> {_fmt_ts(t + item['dur'])}\n{item['text']}\n")
        t += item["dur"] + _GAP
    return "\n".join(blocks)


def seg_durations(timings: list[dict]) -> dict[str, float]:
    """段合计(句间含 0.2s 间隙,段尾不计):s1 两句 2.0+2.0 → 4.2s。"""
    total: dict[str, float] = {}
    counts: dict[str, int] = {}
    for item in timings:
        total[item["seg_id"]] = total.get(item["seg_id"], 0.0) + item["dur"]
        counts[item["seg_id"]] = counts.get(item["seg_id"], 0) + 1
    return {k: v + _GAP * (n - 1) for k, v in total.items() for n in [counts[k]]}


def fit_report(ep: Episode, timings: list[dict], clip_dur: dict[str, float]) -> list[str]:
    rep: list[str] = []
    for k, need in seg_durations(timings).items():
        have = clip_dur.get(k)
        if have is None:
            rep.append(f"{k}: 未找到素材文件")
        elif have + 0.3 < need:
            rep.append(f"{k}: 解说 {need:.1f}s > 素材 {have:.1f}s —— 建议回放 0.8x 慢放重录,或砍一句解说")
    return rep


def _build_notes(ep: Episode, timings: list[dict], clip_map: dict[str, Path]) -> str:
    lines = [f"# {ep.trade_date} 拼接说明(剪映)", "",
             "1. 新建 1080x1920 竖屏项目", "2. 按下表顺序拖入素材与音轨,字幕导入 字幕.srt(套大字样式)",
             "3. 每段素材时长若长于解说,可加变速/卡点;数据卡 PNG 垫在片头与每段开头 2s", "",
             "| 段 | 素材 | 音轨文件 | 字幕行 | 段解说时长 |", "|---|---|---|---|---|"]
    row_i = 1
    for seg in ep.segments:
        seg_lines = [t for t in timings if t["seg_id"] == seg.seg_id]
        n = len(seg_lines)
        clip = clip_map.get(seg.seg_id)
        clip_s = clip.name if clip else ("(数据卡/比分牌静态段)" if seg.kind != "stock" else "缺失!")
        audios = " + ".join(t["file"] for t in seg_lines) or "-"
        dur = seg_durations(timings).get(seg.seg_id, 0.0)
        lines.append(f"| {seg.seg_id}({seg.kind}) | {clip_s} | {audios} | {row_i}-{row_i + n - 1} | {dur:.1f}s |")
        row_i += n
    return "\n".join(lines)


def make_pack(day_dash: str) -> Path:
    ep_dir = EPISODES_DIR / day_dash
    ep = Episode.from_dict(json.loads((ep_dir / "episode.json").read_text(encoding="utf-8")))
    out = ep_dir / "原料包"
    timings = synth_lines(ep, out)
    (out / "durations.json").write_text(
        json.dumps({"lines": timings, "segments": seg_durations(timings)},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "字幕.srt").write_text(build_srt(timings), encoding="utf-8")

    from davis_analyzer.recap.recorder_sheet import match_clips
    clip_map, missing = match_clips(day_dash, ep)
    clip_dur: dict[str, float] = {}
    for seg_id, p in clip_map.items():
        try:
            clip_dur[seg_id] = audio_duration(p)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{p} 时长解析失败: {e!r}")
    rep = fit_report(ep, timings, clip_dur)
    (out / "音频守恒报告.txt").write_text("\n".join(rep) or "全部通过", encoding="utf-8")
    (out / "拼接说明.md").write_text(_build_notes(ep, timings, clip_map), encoding="utf-8")
    logger.info(f"recap 原料包完成: {out}(守恒 {'通过' if not rep else rep})")
    return out
```

- [ ] **Step 4: 运行测试通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_audio.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/recap/audio_pack.py davis_analyzer/tests/test_recap_audio.py davis_analyzer/recap/cli.py
git commit -m "feat(recap): audio原料包(双TTS/SRT/守恒校验/拼接说明)+cli audio接入"
```

---

### Task 9: card_renderer.py 数据卡(比分牌+个股卡 html→png)

**Files:**
- Create: `davis_analyzer/recap/card_renderer.py`
- Test: `davis_analyzer/tests/test_recap_cards.py`

**Interfaces:**
- Consumes: Episode/facts(Task 7 落盘)、bundle(Task 2);playwright(照 render_longpics.py:launch→viewport→goto file://→wait→screenshot)。
- Produces: `card_renderer.scoreboard_html(ep: Episode) -> str`(1080×1920 全屏片头)、`card_renderer.stock_card_html(cand_dict: dict) -> str`(1080×420 下三分之一条)、`card_renderer.render_cards(day_dash) -> list[Path]`(产 `原料包/cards/*.png`,顺序:s1 前 scoreboard,其后每票一张)。卡上所有数字直接取 facts `display` 字符串(不经 LLM,天然过数字闸)。Task 10 对这些 PNG 跑视觉质检。

- [ ] **Step 1: 写失败测试**

```python
# davis_analyzer/tests/test_recap_cards.py
"""recap 数据卡:html 生成(数字=display 原样)/渲染落盘(playwright 真跑)。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from davis_analyzer.recap import card_renderer as cr


def _ep_dir(tmp_path):
    day = "2026-09-18"
    d = tmp_path / "episodes" / day
    d.mkdir(parents=True)
    (d / "episode.json").write_text(json.dumps({
        "trade_date": day, "title": "五连板之夜",
        "facts": [
            {"id": "idx_sh_close", "value": "3875", "unit": "点", "display": "上证指数3876点",
             "as_of": day, "source": {"kind": "stockhot", "ref": "r"}},
            {"id": "idx_sh_chg", "value": "-0.411", "unit": "%", "display": "上证指数-0.41%",
             "as_of": day, "source": {"kind": "stockhot", "ref": "r"}},
            {"id": "breadth_up", "value": "3200", "unit": "家", "display": "上涨3200家",
             "as_of": day, "source": {"kind": "stockhot", "ref": "r"}},
        ],
        "segments": [{"seg_id": "s1", "kind": "stock", "ts_code": "605577.SH", "lines": []}],
    }, ensure_ascii=False), "utf-8")
    (d / "candidates.json").write_text(json.dumps([{
        "ts_code": "605577.SH", "name": "龙版传媒", "sector": "出版", "drama_score": 98.0,
        "events": [], "replay_start": "09:27:00", "replay_end": "14:51:00",
        "notes": ["5连板", "3度炸板后回封"], "educational": True,
        "facts": [{"id": "605577.SH_boards", "value": "5", "unit": "板", "display": "5连板",
                   "as_of": day, "source": {"kind": "stockhot", "ref": "r"}}],
    }], ensure_ascii=False), "utf-8")
    return tmp_path


def test_scoreboard_html_uses_display_verbatim(tmp_path):
    ep = json.loads((_ep_dir(tmp_path) / "episodes" / "2026-09-18" / "episode.json").read_text("utf-8"))
    html = cr.scoreboard_html(ep)
    assert "上证指数3876点" in html          # display 原样,不重排数字
    assert "上涨3200家" in html
    assert "1080" in html                    # 竖屏宽度声明


def test_stock_card_html(tmp_path):
    _ep_dir(tmp_path)
    cand = json.loads((_ep_dir(tmp_path) / "episodes" / "2026-09-18" / "candidates.json")
                      .read_text("utf-8"))[0]
    html = cr.stock_card_html(cand)
    assert "龙版传媒" in html and "5连板" in html and "09:27-14:51" in html


@pytest.mark.integration
def test_render_cards_smoke(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    _ep_dir(tmp_path)
    monkeypatch.setattr(cr, "EPISODES_DIR", tmp_path / "episodes")
    pngs = cr.render_cards("2026-09-18")
    assert pngs and all(p.suffix == ".png" and p.stat().st_size > 10_000 for p in pngs)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_cards.py -v -m "not integration"`
Expected: 2 passed 前先 FAIL(模块不存在;integration 用例单跑)

- [ ] **Step 3: 实现**

```python
# davis_analyzer/recap/card_renderer.py
"""recap 数据卡:片头比分牌(1080x1920)+个股下三分之一条(1080x420),html→png。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger

from davis_analyzer.recap.constants import EPISODES_DIR

_CSS = """
body{margin:0;font-family:'PingFang SC','Noto Sans SC',sans-serif;background:transparent}
.scoreboard{width:1080px;height:1920px;box-sizing:border-box;padding:80px 60px;
  background:linear-gradient(160deg,#0b1220 0%,#101a30 60%,#0b1220 100%);color:#eef2f8}
.title{font-size:72px;font-weight:800;letter-spacing:4px;margin:0 0 8px}
.date{font-size:34px;color:#8fa3c0;margin-bottom:56px}
.idxrow{display:flex;justify-content:space-between;align-items:center;
  background:#16233c;border-radius:24px;padding:36px 44px;margin-bottom:28px}
.idxname{font-size:44px;font-weight:700}.idxval{font-size:40px;color:#c9d6ea}
.idxchg{font-size:46px;font-weight:800}
.up{color:#ff4d57}.dn{color:#2ecc8f}
.breadth{display:flex;gap:28px;margin-top:48px}
.bcard{flex:1;background:#16233c;border-radius:24px;padding:34px;text-align:center}
.bnum{font-size:66px;font-weight:800}.blab{font-size:32px;color:#8fa3c0;margin-top:8px}
.footer{position:absolute;bottom:60px;left:60px;right:60px;font-size:28px;color:#63748f}
.stockcard{width:1080px;height:420px;box-sizing:border-box;padding:40px 56px;
  background:linear-gradient(90deg,#101a30ee,#0b1220ee);color:#eef2f8;
  display:flex;flex-direction:column;justify-content:center}
.sname{font-size:58px;font-weight:800}.scode{font-size:32px;color:#8fa3c0;margin-left:20px}
.stags{margin-top:18px;font-size:36px;color:#ffd34d;font-weight:700}
.sfacts{margin-top:16px;font-size:34px;color:#c9d6ea}
"""


def _page(body: str) -> str:
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{_CSS}</style></head><body>{body}</body></html>"


def scoreboard_html(ep: dict) -> str:
    facts = {f["id"]: f["display"] for f in ep.get("facts", [])}

    def row(key: str, name: str) -> str:
        val, chg = facts.get(f"idx_{key}_close", "-"), facts.get(f"idx_{chg_key(key)}", "")
        cls = "up" if chg.startswith("+") or "涨" in chg else "dn"
        return (f"<div class='idxrow'><span class='idxname'>{name}</span>"
                f"<span class='idxval'>{val}</span>"
                f"<span class='idxchg {cls}'>{chg}</span></div>")

    def chg_key(key: str) -> str:
        return f"{key}_chg"

    breadth = (f"<div class='bcard'><div class='bnum up'>{facts.get('breadth_up', '-')}</div>"
               f"<div class='blab'>上涨家数</div></div>"
               f"<div class='bcard'><div class='bnum dn'>{facts.get('breadth_down', '-')}</div>"
               f"<div class='blab'>下跌家数</div></div>"
               f"<div class='bcard'><div class='bnum'>{facts.get('limit_up_count', '-')}</div>"
               f"<div class='blab'>涨停家数</div></div>")
    body = (f"<div class='scoreboard'><h1 class='title'>今日战报</h1>"
            f"<div class='date'>{ep['trade_date']} · A股全场回放</div>"
            + row("sh", "上证指数") + row("sz", "深证成指") + row("cyb", "创业板指")
            + f"<div class='breadth'>{breadth}</div>"
            f"<div class='footer'>数据来源:盘后公开行情 · 仅为盘面复盘记录,不构成投资建议</div></div>")
    return _page(body)


def stock_card_html(cand: dict) -> str:
    tags = " · ".join(cand.get("notes", [])[:4]) or "今日高光"
    fact_disp = " / ".join(f["display"] for f in cand.get("facts", [])[:6])
    replay = f"{cand['replay_start'][:5]}-{cand['replay_end'][:5]}"
    body = (f"<div class='stockcard'><div><span class='sname'>{cand['name']}</span>"
            f"<span class='scode'>{cand['ts_code']} · {cand.get('sector') or ''} · 回放 {replay}</span></div>"
            f"<div class='stags'>{tags}</div>"
            f"<div class='sfacts'>{fact_disp}</div></div>")
    return _page(body)


async def _shoot(html: str, out: Path, w: int, h: int) -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
        await page.set_content(html, wait_until="load")
        await page.wait_for_timeout(400)
        await page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": w, "height": h})
        await browser.close()


def render_cards(day_dash: str) -> list[Path]:
    ep_dir = EPISODES_DIR / day_dash
    ep = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    cands = json.loads((ep_dir / "candidates.json").read_text(encoding="utf-8"))
    out_dir = ep_dir / "原料包" / "cards"
    out_dir.mkdir(parents=True, exist_ok=True)
    pngs: list[Path] = []
    board = out_dir / "scoreboard.png"
    asyncio.run(_shoot(scoreboard_html(ep), board, 1080, 1920))
    pngs.append(board)
    for i, c in enumerate(cands, 1):
        card = out_dir / f"stock_{i:02d}_{c['ts_code'].split('.')[0]}.png"
        asyncio.run(_shoot(stock_card_html(c), card, 1080, 420))
        pngs.append(card)
    logger.info(f"recap 数据卡 {len(pngs)} 张 → {out_dir}")
    return pngs
```

- [ ] **Step 4: 运行测试通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_cards.py -v -m "not integration" && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_cards.py -v -m integration`
Expected: 全部 passed(playwright 渲染烟测出 2160×3840/2160×840 PNG)

pytest 无 `integration` marker 注册时在 `davis_analyzer/pytest.ini` 或 `pyproject.toml` 补 markers(若无 pytest.ini,创建 `davis_analyzer/pytest.ini`:`[pytest]\nmarkers =\n    integration: 需要真实浏览器/网络` 的最小内容——先查父级是否已有 pytest 配置,有则在其 markers 追加)。

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/recap/card_renderer.py davis_analyzer/tests/test_recap_cards.py
git commit -m "feat(recap): 数据卡渲染(比分牌1080x1920+个股下三分之一条)"
```

---

### Task 10: vision_qc.py 视觉质检闸 + 接入 audio 命令

**Files:**
- Create: `davis_analyzer/recap/vision_qc.py`
- Test: `davis_analyzer/tests/test_recap_vision_qc.py`
- Modify: `davis_analyzer/recap/cli.py`(cmd_audio 末尾渲染卡片→跑视觉质检→结论写 `原料包/视觉质检.json`)

**Interfaces:**
- Consumes: `scripts/content_publisher/vision.py` 的 `ask_vision(image_path, prompt, timeout_s=60) -> dict`(用 importlib 按路径加载,不改对方代码);Task 9 的 cards PNG。
- Produces: `vision_qc.qc_card(png: Path) -> dict`(`{"pass": bool, "issues": [str]}`)、`vision_qc.qc_dir(card_dir: Path) -> dict`(汇总,写盘由调用方做)。exit 不因 QC fail 而非零(报告人工处置,与「推送失败不阻断」同口径),但 `cmd_audio` 结尾 print 质检结论。

- [ ] **Step 1: 写失败测试**

```python
# davis_analyzer/tests/test_recap_vision_qc.py
"""recap 视觉质检:ask_vision 包装/结论汇总(mock 模型)。"""
from __future__ import annotations

from pathlib import Path

from davis_analyzer.recap import vision_qc


def test_qc_card_parses_verdict(tmp_path, monkeypatch):
    png = tmp_path / "a.png"
    png.write_bytes(b"x")
    monkeypatch.setattr(vision_qc, "_ask_vision",
                        lambda img, prompt: {"pass": True, "issues": []})
    out = vision_qc.qc_card(png)
    assert out["pass"] is True and out["issues"] == []


def test_qc_card_fail_issues(tmp_path, monkeypatch):
    png = tmp_path / "a.png"
    png.write_bytes(b"x")
    monkeypatch.setattr(vision_qc, "_ask_vision",
                        lambda img, prompt: {"pass": False, "issues": ["文字被裁切"]})
    out = vision_qc.qc_card(png)
    assert out["pass"] is False and "文字被裁切" in out["issues"]


def test_qc_dir_aggregates(tmp_path, monkeypatch):
    for n in ("a.png", "b.png"):
        (tmp_path / n).write_bytes(b"x")
    verdicts = {"a.png": {"pass": True, "issues": []},
                "b.png": {"pass": False, "issues": ["数字模糊"]}}
    monkeypatch.setattr(vision_qc, "_ask_vision",
                        lambda img, prompt: verdicts[Path(img).name])
    rep = vision_qc.qc_dir(tmp_path)
    assert rep["pass"] is False
    assert rep["frames"][0]["pass"] is True and rep["frames"][1]["pass"] is False


def test_qc_card_bad_payload_defaults_fail(tmp_path, monkeypatch):
    png = tmp_path / "a.png"
    png.write_bytes(b"x")
    monkeypatch.setattr(vision_qc, "_ask_vision", lambda img, prompt: {"unexpected": 1})
    out = vision_qc.qc_card(png)
    assert out["pass"] is False and out["issues"]


def test_qc_empty_dir(tmp_path):
    rep = vision_qc.qc_dir(tmp_path)
    assert rep["pass"] is False and rep["frames"] == []
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_vision_qc.py -v`
Expected: FAIL `No module named ... vision_qc`

- [ ] **Step 3: 实现**

```python
# davis_analyzer/recap/vision_qc.py
"""recap 视觉质检闸:数据卡/成片帧过 vision.py(结构化 JSON,主模型只消费结论)。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from loguru import logger

from davis_analyzer.recap.constants import REPO_ROOT

_QC_PROMPT = (
    "这是A股复盘短视频里的一张数据卡截图。请只检查以下问题并返回JSON"
    '(不要多余文字):{"pass": true/false, "issues": ["问题描述", ...]}。'
    "检查项:①文字是否清晰可读(无模糊/锯齿);②是否有文字溢出卡片边界或被裁切;"
    "③排版是否有元素重叠遮挡;④数字是否完整显示(无截断);⑤配色对比度是否足以看清。"
    "没有问题则 pass=true、issues 为空数组。"
)


def _load_vision():
    """按路径加载 scripts/content_publisher/vision.py(只读复用,不 import 进包)。"""
    mod_path = REPO_ROOT / "scripts" / "content_publisher" / "vision.py"
    spec = importlib.util.spec_from_file_location("recap_vision_shim", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ask_vision(image_path: Path, prompt: str) -> dict:
    return _load_vision().ask_vision(image_path, prompt)


def qc_card(png: Path) -> dict:
    try:
        verdict = _ask_vision(png, _QC_PROMPT)
    except Exception as e:  # noqa: BLE001 —— 模型不可用按 fail 处置,报告人工
        logger.warning(f"视觉质检调用失败 {png.name}: {e!r}")
        return {"pass": False, "issues": [f"质检调用异常: {e!r}"]}
    if not isinstance(verdict, dict) or "pass" not in verdict:
        return {"pass": False, "issues": [f"质检返回结构异常: {verdict!r}"]}
    return {"pass": bool(verdict["pass"]),
            "issues": [str(i) for i in verdict.get("issues", [])]}


def qc_dir(card_dir: Path) -> dict:
    frames = [{"file": p.name, **qc_card(p)} for p in sorted(card_dir.glob("*.png"))]
    return {"pass": bool(frames) and all(f["pass"] for f in frames), "frames": frames}
```

修改 `cli.py` 的 `cmd_audio`:

```python
def cmd_audio(args) -> None:
    from davis_analyzer.recap.audio_pack import make_pack
    from davis_analyzer.recap import card_renderer, vision_qc
    out = make_pack(args.date)
    cards = card_renderer.render_cards(args.date)
    rep = vision_qc.qc_dir(out / "cards")
    import json as _json
    (out / "视觉质检.json").write_text(_json.dumps(rep, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(f"audio: 原料包 → {out}")
    print(f"视觉质检: {'通过' if rep['pass'] else '发现问题,见 原料包/视觉质检.json'}")
    if not rep["pass"]:
        for f in rep["frames"]:
            if not f["pass"]:
                print(f"  ✗ {f['file']}: {'; '.join(f['issues'][:3])}")
```

- [ ] **Step 4: 运行测试通过 + 全套回归**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_vision_qc.py -v && .venv/bin/python -m pytest davis_analyzer/tests/ -k recap -v`
Expected: 全部 passed(Task 1-10 全回归绿)

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/recap/vision_qc.py davis_analyzer/recap/cli.py davis_analyzer/tests/test_recap_vision_qc.py
git commit -m "feat(recap): 视觉质检闸接入audio命令(数据卡逐张过vision)"
```

---

### Task 11(二期,一期跑顺后执行): post_compose.py ffmpeg 自动合成

> 一期验收标准:连续 5 个交易日录制单照单完成+原料包可用,再启动本任务。

**Files:**
- Create: `davis_analyzer/recap/post_compose.py`
- Test: `davis_analyzer/tests/test_recap_post.py`

**Interfaces:**
- Consumes: 原料包全部产物(audio/durations.json/cards/字幕.srt)+ inbox 素材;`cardgen.video` 的 `ffmpeg()`/`audio_duration`/`concat_clips`;`recorder_sheet.match_clips`。
- Produces: `post_compose.compose(day_dash) -> Path`(`episodes/{day}/final/{day}_recap.mp4`,1080×1920)。流程:①open 段=scoreboard.png Ken Burns+open 音轨;②每只票=素材变速对齐解说时长+底部叠 stock_card+该段音轨;③close 段=scoreboard.png 复用(加「明日看点」字幕已有 SRT 不烧进二期 v1——字幕留给剪映版,二期成片带音轨与叠层);④concat;⑤台账 `composed`。

- [ ] **Step 1: 写失败测试**(ffmpeg 真跑小素材,属 integration)

```python
# davis_analyzer/tests/test_recap_post.py
"""recap 二期合成:段落滤镜命令构造(单元)+ compose 烟测(integration)。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from davis_analyzer.recap import post_compose as pc


def test_setpts_speed_formula():
    # 解说 12s / 素材 18s → 加速 1.5x,PTS=PTS/1.5
    assert pc.speed_factor(clip=18.0, need=12.0) == pytest.approx(1.5)
    assert pc.speed_factor(clip=10.0, need=12.0) == pytest.approx(1.0)  # 不减速,留片尾
    assert pc.speed_factor(clip=0.0, need=12.0) == 1.0                  # 防零


def test_overlay_geometry():
    # 1080p 合成层:卡片缩放后 420 高,底部留 120 → y = 1920-420-120
    assert pc.overlay_y(video_h=1920, card_h=420, margin=120) == 1380


def _ff_make(src_args: list[str], out: Path) -> None:
    r = subprocess.run([pc.ffmpeg(), "-y", *src_args, str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-300:]


@pytest.mark.integration
def test_compose_smoke(tmp_path, monkeypatch):
    """ffmpeg lavfi 生成 1s 真素材(黑帧视频/静音mp3/纯色png),compose 全链路出 mp4。"""
    day = "2026-09-18"
    ep_dir = tmp_path / "episodes" / day
    (ep_dir / "episode.json").write_text(json.dumps({
        "trade_date": day, "title": "t", "facts": [],
        "segments": [
            {"seg_id": "open", "kind": "scoreboard", "ts_code": None,
             "lines": [{"speaker": "pb", "text": "开场。"}]},
            {"seg_id": "s1", "kind": "stock", "ts_code": "605577.SH",
             "lines": [{"speaker": "color", "text": "五连板。"}]},
            {"seg_id": "close", "kind": "outlook", "ts_code": None,
             "lines": [{"speaker": "pb", "text": "本内容仅为盘面复盘记录,不构成投资建议。"}]},
        ]}, ensure_ascii=False), "utf-8")
    pack = ep_dir / "原料包"
    (pack / "audio").mkdir(parents=True)
    (pack / "cards").mkdir()
    for name in ("open_00_pb.mp3", "s1_00_color.mp3", "close_00_pb.mp3"):
        _ff_make(["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "1"],
                 pack / "audio" / name)
    _ff_make(["-f", "lavfi", "-i", "color=c=navy:s=2160x3840:d=1", "-frames:v", "1"],
             pack / "cards" / "scoreboard.png")
    _ff_make(["-f", "lavfi", "-i", "color=c=navy:s=2160x840:d=1", "-frames:v", "1"],
             pack / "cards" / "stock_01_605577.png")
    (pack / "durations.json").write_text(json.dumps(
        {"lines": [], "segments": {"open": 1.0, "s1": 1.0, "close": 1.0}}), "utf-8")
    inbox = tmp_path / "inbox" / day
    inbox.mkdir(parents=True)
    _ff_make(["-f", "lavfi", "-i", "color=c=gray:s=1080x1920:d=1",
              "-c:v", "libx264", "-preset", "ultrafast", "-t", "1"],
             inbox / "20260918_605577.SH_01.mp4")
    from davis_analyzer.recap import recorder_sheet as rs
    monkeypatch.setattr(rs, "INBOX_DIR", tmp_path / "inbox")  # match_clips 读自己的 INBOX_DIR
    monkeypatch.setattr(pc, "EPISODES_DIR", tmp_path / "episodes")
    monkeypatch.setattr(pc, "audio_duration", lambda p: 1.0)
    out = pc.compose(day)
    assert out.exists() and out.stat().st_size > 0
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_post.py -v -m "not integration"`
Expected: FAIL `No module named ... post_compose`

- [ ] **Step 3: 实现**

```python
# davis_analyzer/recap/post_compose.py
"""recap 二期:素材+原料包 → 1080x1920 成片(变速对齐/叠层/混音/concat)。"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from davis_analyzer.cardgen.video import audio_duration, concat_clips, ffmpeg
from davis_analyzer.recap.constants import EPISODES_DIR
from davis_analyzer.recap.types import Episode

W, H = 1080, 1920
_PAD_TAIL = 0.6


def speed_factor(clip: float, need: float) -> float:
    """素材加速到解说时长;素材足够长才加速,不够长保持 1x(守恒报告已提示重录)。"""
    if clip <= 0 or need <= 0 or clip <= need + 0.3:
        return 1.0
    return min(clip / need, 4.0)


def overlay_y(video_h: int, card_h: int, margin: int) -> int:
    return video_h - card_h - margin


def _run(cmd: list[str], tag: str) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{tag} 失败: {r.stderr[-400:]}")


def _concat_mp3(paths: list[Path], out: Path) -> Path:
    _run([ffmpeg(), "-y", "-i", "concat:" + "|".join(str(p) for p in paths),
          "-c", "copy", str(out)], "concat_mp3")
    return out


def _stock_clip(clip: Path, card_png: Path, seg_audio: Path, out: Path,
                need: float) -> Path:
    dur = audio_duration(clip)
    sp = speed_factor(dur, need + _PAD_TAIL)
    # card 是 2160 宽(dsf=2 渲染),滤镜内缩到 1080 宽;叠底部留 120px
    vf = (
        f"[0:v]setpts=PTS/{sp:.4f},scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},setsar=1[v0];"
        f"[2:v]scale={W}:-2[card];"
        f"[v0][card]overlay=0:{overlay_y(H, 420, 120)}:shortest=1,format=yuv420p[v]"
    )
    _run([ffmpeg(), "-y", "-i", str(clip), "-i", str(seg_audio),
          "-i", str(out.parent / "card_s.png"), "-filter_complex", vf,
          "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "fast",
          "-crf", "23", "-c:a", "aac", "-b:a", "128k", "-r", "30",
          "-t", f"{need + _PAD_TAIL:.2f}", str(out)], "stock_clip")
    return out


def _board_clip(board_png: Path, seg_audio: Path, out: Path) -> Path:
    """比分牌静态段:图+音轨(Ken Burns 由 zoompan 提供,简化为轻微推近)。"""
    need = audio_duration(seg_audio) + _PAD_TAIL
    frames = int(need * 25)
    vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"zoompan=z='min(zoom+0.0004,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
          f":d={frames}:s={W}x{H}:fps=25,format=yuv420p[v]")
    _run([ffmpeg(), "-y", "-loop", "1", "-t", f"{need:.2f}", "-i", str(board_png),
          "-i", str(seg_audio), "-filter_complex", vf, "-map", "[v]", "-map", "1:a",
          "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-r", "25",
          "-c:a", "aac", "-b:a", "128k", "-shortest", str(out)], "board_clip")
    return out


def compose(day_dash: str) -> Path:
    from davis_analyzer.recap.recorder_sheet import match_clips
    ep_dir = EPISODES_DIR / day_dash
    ep = Episode.from_dict(json.loads((ep_dir / "episode.json").read_text(encoding="utf-8")))
    pack = ep_dir / "原料包"
    durs = json.loads((pack / "durations.json").read_text(encoding="utf-8"))
    seg_need: dict[str, float] = durs["segments"]
    clip_map, missing = match_clips(day_dash, ep)
    if missing:
        raise SystemExit(f"缺素材段落: {missing}(先补录丢 inbox)")
    final_dir = ep_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    def seg_audio_files(seg_id: str) -> list[Path]:
        return sorted((pack / "audio").glob(f"{seg_id}_*.mp3"))

    with tempfile.TemporaryDirectory(prefix="recap_post_") as td:
        tdp = Path(td)
        parts: list[Path] = []
        for seg in ep.segments:
            audios = seg_audio_files(seg.seg_id)
            merged = (_concat_mp3(audios, tdp / f"aud_{seg.seg_id}.mp3") if len(audios) > 1
                      else audios[0])
            if seg.kind == "stock":
                card = next((pack / "cards").glob(
                    f"stock_*_{seg.ts_code.split('.')[0]}.png"), None)
                if card is None:
                    raise SystemExit(f"缺数据卡: {seg.ts_code}")
                parts.append(_stock_clip(clip_map[seg.seg_id], card, merged,
                                         tdp / f"part_{seg.seg_id}.mp4",
                                         seg_need.get(seg.seg_id, audio_duration(merged))))
            else:
                parts.append(_board_clip(pack / "cards" / "scoreboard.png", merged,
                                         tdp / f"part_{seg.seg_id}.mp4"))
        final = concat_clips(parts, final_dir / f"{day_dash}_recap.mp4")
    logger.info(f"recap 成片: {final} ({final.stat().st_size / 1048576:.1f}MB)")
    return final
```

同时在 `cli.py` 的 `cmd_post` 尾部加台账推进:

```python
def cmd_post(args) -> None:
    from davis_analyzer.recap import db
    from davis_analyzer.recap.post_compose import compose
    out = compose(args.date)
    conn = _conn()
    try:
        db.ensure_tables(conn)
        db.update_status(conn, args.date, "composed")
    finally:
        conn.close()
    print(f"post: 成片 → {out}(发布永远人工)")
```

- [ ] **Step 4: 运行测试**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_recap_post.py -v`
Expected: 单元 2 passed;integration 烟测 1 passed(假素材出 mp4)

- [ ] **Step 5: 真素材端到端一次 + 提交**

用真实录制素材跑 `python -m davis_analyzer.recap audio --date <day> && python -m davis_analyzer.recap post --date <day>`,人工看成片;成片抽帧跑 `vision_qc.qc_dir`(临时脚本),确认叠层/字幕/清晰度可接受。

```bash
git add davis_analyzer/recap/post_compose.py davis_analyzer/recap/cli.py davis_analyzer/tests/test_recap_post.py
git commit -m "feat(recap): 二期ffmpeg自动合成(变速对齐/叠层/concat)+成片抽帧质检"
```

---

## 验收清单(一期)

- [ ] 工作日 19:40 timer 自动产出 `episodes/{day}/` 四件套+飞书录制单(连续 3 日观察 logs/recap_run.log)
- [ ] 照单录屏→inbox→`audio` 命令产原料包,TTS 双音色可听、SRT 剪映可导入、守恒报告有结论
- [ ] 数据卡 PNG 视觉质检 JSON 产出且人工复核通过
- [ ] 剪映按拼接说明拼出一条 60-90s 成片(一期终态)
- [ ] `pytest davis_analyzer/tests/ -k recap` 全绿;`test_doc_consistency.py` 不受影响(未改既有权重)

## Self-Review 记录

- **Spec 覆盖**:选片(§三→Task 3)、剧本/映射表/双解说(§四→Task 5)、facts/合规/免责(§五→Task 4/5)、录制单/文件名协议(§六→Task 6)、原料包/TTS/SRT/守恒(§七→Task 8)、数据卡+质检闸一期(§七/§八→Task 9/10)、调度 19:40+AGENTS.md(§九→Task 7)、测试(§十一→各任务)、异常(§十二→DailyDataMissing 非零退出/推送失败不阻断/冰点降级 Task 7)、二期合成(§八→Task 11)。非目标(§十三)未越界:不荐股、不自动发布、不用分钟线。
- **类型一致**:`Candidate.__dict__` 序列化贯穿 Task 3/7/9(`cands[0].facts[0]["as_of"]`);`durations.json` 结构 Task 8 定义、Task 11 消费一致;`match_clips` 返回 `(dict[str, Path], list[str])` Task 6/8/11 三处一致。
- **已知实现注意点**:①`cli.py` 顶部 `data/sheet/scriptwriter` 三个锚点 import 是测试 monkeypatch 依赖,子命令内不得局部重 import;②数字闸的「无单位变体」在 recap validator `_facts_of` 内实现(不动 cardgen/numbers.py 共享代码);③指数 facts 的 value 与 display 同口径取整(3875.6→3876),否则解说文本永远对不上 facts;④SRT 段合计口径=「句间隙计入、段尾不计」,Task 8 测试断言 4.2s 锁定;⑤`recorder_sheet.INBOX_DIR` 是 match_clips 的真实读取点,测试必须 patch `rs.INBOX_DIR`(不是 audio_pack/post_compose 的同名导入);⑥Task 7 测试包装 generate_episode 时先取原函数再 patch(lambda 内再调模块属性会无限递归)。
