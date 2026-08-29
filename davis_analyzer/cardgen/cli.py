# davis_analyzer/cardgen/cli.py
"""cardgen CLI: init / ingest / validate / build / status / enqueue。"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

from davis_analyzer.cardgen import ledger
from davis_analyzer.cardgen.builder import load_release, render
from davis_analyzer.cardgen.facts import load_facts, save_facts
from davis_analyzer.cardgen.ingest import fetch_daily_basic
from davis_analyzer.cardgen.validator import run_validation

REPO_ROOT = Path(__file__).resolve().parents[2]


def _projects_root() -> Path:
    """工程根目录;测试经 CARDGEN_PROJECT_ROOT 重定向,避免污染 docs/。"""
    return Path(os.environ.get("CARDGEN_PROJECT_ROOT", REPO_ROOT / "docs" / "小红书卡片"))


def _project(topic: str) -> Path:
    return _projects_root() / topic


def _ledger_conn() -> sqlite3.Connection:
    """台账连接;测试经 CARDGEN_LEDGER_DB 重定向,避免污染真实 content_cards.db。"""
    env = os.environ.get("CARDGEN_LEDGER_DB")
    return ledger.connect(Path(env) if env else None)


# ── init:建工程骨架并登记台账 ──
def cmd_init(args: argparse.Namespace) -> None:
    d = _project(args.topic)
    d.mkdir(parents=True, exist_ok=True)
    for name, template in (("facts.json", {"facts": []}),
                           ("cards.spec.json", {"cards": []})):
        p = d / name
        if not p.exists():
            p.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "output").mkdir(exist_ok=True)
    conn = _ledger_conn()
    v = ledger.register_card(conn, args.topic, str(d / "cards.spec.json"))
    conn.close()
    print(f"工程就绪: {d}(version {v})")


# ── ingest:daily_basic 事实入库 ──
def cmd_ingest(args: argparse.Namespace) -> None:
    fact = fetch_daily_basic(args.code, args.metric)
    fact.id = args.id or f"{args.metric}_{args.code.split('.')[0]}"
    p = _project(args.topic) / "facts.json"
    facts = [f for f in load_facts(p) if f.id != fact.id] + [fact]
    save_facts(p, facts)
    print(f"事实入库: {fact.id} = {fact.display}({fact.source_ref})")


# ── validate:四闸校验并写台账 ──
def cmd_validate(args: argparse.Namespace) -> None:
    report = run_validation(_project(args.topic), topic=args.topic)
    conn = _ledger_conn()
    try:
        row = ledger.get_card(conn, args.topic)
        ledger.log_validate(conn, args.topic, int(row["current_version"]) if row else 1,
                            report.passed, report.failures)
        if report.passed:
            # 状态单调:rendered/queued 是更高生命周期态,重复 validate 不降级
            if row and row["status"] in ("rendered", "queued"):
                print(f"状态保持 {row['status']}(validate 通过但不降级生命周期)")
            else:
                ledger.set_status(conn, args.topic, "validated")
    finally:
        conn.close()
    for f in report.failures:
        print(f"✗ [{f.gate}] {f.card} {f.field}: {f.detail}")
    print(f"validate {'通过' if report.passed else f'未通过({len(report.failures)}项)'} | "
          f"as_of={report.as_of or '-'} expires_at={report.expires_at or '-'}")
    if not report.passed:
        sys.exit(1)


# ── build:渲染并发布 RELEASE ──
def cmd_build(args: argparse.Namespace) -> None:
    conn = _ledger_conn()
    try:
        release = render(_project(args.topic), args.topic, conn,
                         bump=args.bump, reason=args.reason)
        print(f"渲染完成 v{release['version']}: {len(release['images'])} 张 PNG | "
              f"过期日 {release['expires_at']} | RELEASE.json 已生成")
    finally:
        conn.close()


# ── status:台账总览 ──
def cmd_status(args: argparse.Namespace) -> None:
    conn = _ledger_conn()
    try:
        for r in ledger.status_rows(conn, topic=args.topic):
            print(f"{r['topic']:<16} v{r['current_version']} [{r['status']:<9}] "
                  f"as_of={r['as_of'] or '-'} expires={r['expires_at'] or '-'}")
    finally:
        conn.close()


# ── enqueue:打印发稿入池命令(不代执行) ──
def cmd_enqueue(args: argparse.Namespace) -> None:
    proj = _project(args.topic)
    if not (proj / "output" / "RELEASE.json").exists():
        sys.exit(f"未找到 RELEASE.json: 先 build --topic {args.topic}")
    release = load_release(proj)
    if release["expires_at"] < date.today().isoformat():
        sys.exit(f"已过期(expires_at={release['expires_at']}):数据陈旧,须更新事实后重新 build")
    # 契约:优先读物化 spec($fact 已替换为 display),避免 title/tags 含 $fact 引用时打印 dict
    mat = proj / "output" / "spec.materialized.json"
    spec = json.loads((mat if mat.exists() else proj / "cards.spec.json").read_text(encoding="utf-8"))
    title = re.sub(r"<[^>]+>", " ", str(spec["cards"][0].get("title", args.topic))).strip()
    tags = next((str(c["tags"]) for c in spec["cards"] if c.get("tags")), "")
    # 契约:RELEASE.images 为相对 project_dir 的路径,打印前用 proj/img 拼成绝对路径
    images = ",".join(str(proj / img) if not img.startswith("/") else img
                      for img in release["images"])
    try:
        source = str(proj.relative_to(REPO_ROOT))
    except ValueError:
        source = str(proj)  # 工程在仓库外(如测试重定向)时退回绝对路径
    print("发稿入池命令(人工/agent 执行):")
    print(f".venv/bin/python scripts/content_publisher/queue.py enqueue "
          f"--title '{title}' --images {images} --tags '{tags}' "
          f"--source '{source}'")
    conn = _ledger_conn()
    try:
        ledger.set_status(conn, args.topic, "queued")
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="cardgen", description="小红书金融卡片内容生成")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="建工程骨架并登记台账")
    i.add_argument("--topic", required=True)
    i.set_defaults(func=cmd_init)
    g = sub.add_parser("ingest", help="从 daily_basic 拉事实写入 facts.json")
    g.add_argument("--topic", required=True)
    g.add_argument("--code", required=True, help="ts_code 如 688802.SH")
    g.add_argument("--metric", required=True, help="ps/pe_ttm/pb/total_mv")
    g.add_argument("--id", default="", help="事实 id,缺省 {metric}_{代码前段}")
    g.set_defaults(func=cmd_ingest)
    v = sub.add_parser("validate", help="四闸校验")
    v.add_argument("--topic", required=True)
    v.set_defaults(func=cmd_validate)
    b = sub.add_parser("build", help="渲染 PNG 并发布 RELEASE.json")
    b.add_argument("--topic", required=True)
    b.add_argument("--bump", action="store_true", help="spec/facts 已变更时显式升版")
    b.add_argument("--reason", default="", help="升版原因(配合 --bump)")
    b.set_defaults(func=cmd_build)
    s = sub.add_parser("status", help="台账总览")
    s.add_argument("--topic")
    s.set_defaults(func=cmd_status)
    e = sub.add_parser("enqueue", help="打印发稿入池命令并置 queued")
    e.add_argument("--topic", required=True)
    e.set_defaults(func=cmd_enqueue)
    args = ap.parse_args(argv)
    args.func(args)
