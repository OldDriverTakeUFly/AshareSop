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
        EpisodeSegment("open", "scoreboard", None, [
            DialogueLine("pb", f"今日战报,欢迎收看A股全场回放:{idx}。"),
            DialogueLine("pb", f"全场上涨{up}家、下跌{down}家,涨停{lu}家"
                              f"——今晚的集锦室有点空,但比分牌还是要念的。")]),
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
    from dotenv import load_dotenv
    from davis_analyzer.recap.constants import REPO_ROOT
    load_dotenv(REPO_ROOT / ".env")   # get_provider 读 LLM_API_KEY(与 advisor cli 口径一致,brief 补丁)
    args = build_parser().parse_args()
    args.func(args)
