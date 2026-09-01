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

from loguru import logger

from davis_analyzer.cardgen import ledger
from davis_analyzer.cardgen.facts import facts_digest, load_facts
from davis_analyzer.cardgen.materialize import materialize_spec, spec_digest
from davis_analyzer.cardgen.validator import run_validation

REPO_ROOT = Path(__file__).resolve().parents[2]
CARDGEN_VERSION = "0.1"


def _png_prefix(topic: str) -> str:
    """嵌套 topic(连板天梯/2026-09-01)作文件名前缀时压平斜杠。"""
    return topic.replace("/", "_")
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


def _inject_fill_css(html_path: Path) -> None:
    """后处理注入纵向均布 CSS:内容不足 1440px 高的卡片自动拉开间距,消除底部大留白。

    space-between 只分配剩余空间,内容已满的卡片零间隙不受影响(2026-08-30 多卡 vision 复检验证)。
    """
    html = html_path.read_text(encoding="utf-8")
    if "cardgen-fill" not in html:
        html = html.replace("</style>", "</style><style class='cardgen-fill'>"
                            ".card{justify-content:space-between;}.card>.foot{margin-top:0 !important;}</style>", 1)
        html_path.write_text(html, encoding="utf-8")


def _inject_images(spec: dict, html_path: Path, project_dir: Path) -> None:
    """spec 卡片含 image 字段时,在 card_factory 产出的 HTML 里注入图片块(后处理,不改 card_factory)。

    image 契约:{src: 工程内相对路径, license: 授权说明, credit: 署名}——三者必填(版权溯源同 facts 纪律)。
    图片块含 flex 顶推(margin-top:auto)自然落位于卡片内容流末尾,credit 作为图注强制显示。
    """
    html = html_path.read_text(encoding="utf-8")
    n = len(spec.get("cards", []))
    injections = 0
    for i, card in enumerate(spec.get("cards", [])):
        img = card.get("image")
        if not img:
            continue
        for key in ("src", "license", "credit"):
            if not img.get(key):
                raise SystemExit(f"卡片 {card.get('name', i)} image 字段缺 {key}(版权溯源纪律)")
        src = (project_dir / img["src"]).resolve()
        if not src.exists():
            raise SystemExit(f"卡片 {card.get('name', i)} 引用图片不存在: {src}")
        if img.get("mode") == "corner":
            # top/width 可选覆盖(2026-08-30:封面密集卡需要更小的缩略图和更低起点避让标题)
            top, width = img.get("top", 44), img.get("width", 270)
            block = (
                f'<div style="position:absolute;top:{top}px;right:44px;width:{width}px;">'
                f'<img src="file://{src}" style="max-width:{width}px;max-height:200px;object-fit:contain;'
                'border-radius:20px;display:block;margin:0 auto;box-shadow:0 8px 32px rgba(0,0,0,.18);"/>'
                '<div style="font-size:18px;opacity:.55;text-align:center;margin-top:6px;line-height:1.3;">'
                f'图:{img["credit"]}({img["license"]})</div></div>')
        else:
            block = (
                f'<div style="position:absolute;left:56px;right:56px;bottom:{img.get("bottom",150)}px;">'
                f'<img src="file://{src}" style="max-width:60%;max-height:300px;object-fit:contain;'
                'border-radius:24px;display:block;margin:0 auto;box-shadow:0 8px 32px rgba(0,0,0,.18);"/>'
                '<div style="font-size:22px;opacity:.55;text-align:center;margin-top:10px;line-height:1.4;">'
                f'图:{img["credit"]}({img["license"]})</div></div>')
        # 定位第 i 个卡片 div(按出现顺序),用括号深度找其闭合标签
        positions: list[int] = []
        idx = 0
        while True:
            p = html.find("class=\"card", idx)
            if p == -1:
                break
            positions.append(p)
            idx = p + 1
        if len(positions) < n:
            raise SystemExit(f"注入失败:HTML 卡片数 {len(positions)} < spec 卡片数 {n}")
        pos = positions[i]
        depth, j = 0, pos
        while True:
            nxt_open = html.find("<div", j + 1)
            nxt_close = html.find("</div>", j + 1)
            if nxt_close == -1:
                raise SystemExit("注入失败:card div 未闭合")
            if nxt_open != -1 and nxt_open < nxt_close:
                depth += 1
                j = nxt_open
            else:
                if depth == 0:
                    html = html[:nxt_close] + block + html[nxt_close:]
                    injections += 1
                    break
                depth -= 1
                j = nxt_close
    if injections:
        html_path.write_text(html, encoding="utf-8")
        logger.info(f"图片注入完成: {injections} 张")


def render(project_dir: Path, topic: str, conn: sqlite3.Connection,
           bump: bool = False, reason: str = "") -> dict:
    """渲染并发布:validate 闸门 → 变更检测 bump → 物化落盘 → 渲染 → 张数检查 → RELEASE。"""
    # ── 闸门:不过不准 build ──
    report = run_validation(project_dir, topic=topic)
    if not report.passed:
        for f in report.failures:
            logger.warning(f"validate 未过 [{f.gate}] {f.card} {f.field}: {f.detail}")
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
    _inject_images(spec, out / "cards.html", project_dir)
    _inject_fill_css(out / "cards.html")
    prefix = _png_prefix(topic)
    _run(["node", str(REPO_ROOT / "scripts" / "card_factory" / "snap.cjs"),
          str(out / "cards.html"), "--outdir", str(out), "--prefix", prefix])

    # ── 张数检查 + RELEASE ──
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    n_png, n_names = _png_count(project_dir), len(manifest["names"])
    if n_png != n_names:
        raise SystemExit(f"渲染产物不完整: PNG {n_png} 张 != manifest {n_names} 张")

    release = {"topic": topic, "version": int(version), "as_of": report.as_of,
               "expires_at": report.expires_at, "group": spec.get("group", ""),
               "images": [str(p.relative_to(project_dir)) for p in sorted(out.glob(f"{prefix}_*.png"))],
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
