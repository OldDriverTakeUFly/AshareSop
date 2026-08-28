# davis_analyzer/cardgen/builder.py
"""渲染编排:validate 通过 → 物化落盘 → card_factory HTML → node snap PNG → RELEASE.json。"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

from davis_analyzer.cardgen import ledger
from davis_analyzer.cardgen.facts import facts_digest, load_facts
from davis_analyzer.cardgen.materialize import materialize_spec, spec_digest
from davis_analyzer.cardgen.validator import run_validation

REPO_ROOT = Path(__file__).resolve().parents[2]
CARDGEN_VERSION = "0.1"
SUBPROCESS_TIMEOUT_S = 120


def _run(cmd: list[str], timeout: int = SUBPROCESS_TIMEOUT_S) -> None:
    """子进程在仓库根目录执行,失败/超时抛 RuntimeError(输出附在消息里)。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"子进程超时(>{timeout}s): {' '.join(cmd)}") from e
    if proc.returncode != 0:
        raise RuntimeError(f"子进程失败: {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")


def _png_count(project_dir: Path) -> int:
    return len(list((project_dir / "output").glob("*.png")))


def load_release(project_dir: Path) -> dict:
    return json.loads((project_dir / "output" / "RELEASE.json").read_text(encoding="utf-8"))


def render(project_dir: Path, topic: str, conn: sqlite3.Connection,
           bump: bool = False, reason: str = "") -> dict:
    """渲染并发布:validate 闸门 → 变更检测 bump → 物化落盘 → 渲染 → 张数检查 → RELEASE。"""
    # ── 闸门:不过不准 build ──
    report = run_validation(project_dir, topic=topic)
    if not report.passed:
        for f in report.failures:
            print(f"✗ [{f.gate}] {f.card} {f.field}: {f.detail}", file=sys.stderr)
        raise SystemExit(f"validate 未通过({len(report.failures)} 项),禁止 build")

    # ── 过期拒绝(spec §4.3/§6):expires_at 已过 → 数据陈旧,禁止 build(写盘前)──
    today = date.today().isoformat()
    if report.expires_at and report.expires_at < today:
        raise SystemExit(
            f"工程已过期:expires_at={report.expires_at} < 今天 {today},数据陈旧,须更新事实后重新 build")

    facts = load_facts(project_dir / "facts.json")
    spec = json.loads((project_dir / "cards.spec.json").read_text(encoding="utf-8"))
    fd, sd = facts_digest(facts), spec_digest(spec)

    version = ledger.register_card(conn, topic, str(project_dir / "cards.spec.json"))
    # ── 变更检测:与 revisions 最新行比对 digest,已渲染过且变更须显式 --bump --reason ──
    prev = conn.execute(
        "SELECT facts_digest, spec_digest FROM revisions WHERE topic=? ORDER BY version DESC LIMIT 1",
        (topic,)).fetchone()
    if prev is not None:
        changed = fd != prev["facts_digest"] or sd != prev["spec_digest"]
        if changed and not (bump and reason.strip()):
            raise SystemExit("spec/facts 已变更:须 --bump --reason '<修订原因>' 后重新 build")
        if bump:
            version = ledger.bump_version(conn, topic, reason=reason or "手动bump",
                                          facts_digest=fd, spec_digest=sd)

    # ── 物化落盘 ──
    mat, _ = materialize_spec(spec, {f.id: f for f in facts})
    out = project_dir / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "spec.materialized.json").write_text(
        json.dumps(mat, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 渲染:card_factory HTML → node snap PNG ──
    if shutil.which("node") is None:
        raise SystemExit("未找到 node,无法截图(snap.cjs 依赖 playwright)")
    _run([sys.executable,
          str(REPO_ROOT / "scripts" / "card_factory" / "build_cards.py"),
          str(out / "spec.materialized.json"), "--out", str(out)])
    _run(["node", str(REPO_ROOT / "scripts" / "card_factory" / "snap.cjs"),
          str(out / "cards.html"), "--outdir", str(out), "--prefix", topic])

    # ── 张数检查 + RELEASE ──
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    n_png, n_names = _png_count(project_dir), len(manifest["names"])
    if n_png != n_names:
        raise SystemExit(f"渲染产物不完整: PNG {n_png} 张 != manifest {n_names} 张")

    release = {"topic": topic, "version": int(version), "as_of": report.as_of,
               "expires_at": report.expires_at,
               "images": [str(p.relative_to(project_dir)) for p in sorted(out.glob(f"{topic}_*.png"))],
               "facts_digest": fd,
               "validate": {"passed": True, "failures": 0},
               "cardgen_version": CARDGEN_VERSION}
    (out / "RELEASE.json").write_text(json.dumps(release, ensure_ascii=False, indent=2),
                                      encoding="utf-8")

    ledger.record_render(conn, topic, int(version), reason=reason or f"v{version} 渲染",
                         facts_digest=fd, spec_digest=sd)
    conn.execute("UPDATE cards SET as_of=?, expires_at=? WHERE topic=?",
                 (report.as_of, report.expires_at, topic))
    conn.commit()
    return release
