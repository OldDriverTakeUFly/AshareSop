# scripts/daily_market_cards.py
"""每日盘面复盘卡管线(2026-09-01):stockhot.db → 连板天梯/龙虎榜 两卡 → validate → render → [入池]。

用法: .venv/bin/python scripts/daily_market_cards.py --type all [--date 2026-09-02] [--no-render] [--enqueue]
纪律:渲染后停在已出图;--enqueue 只入发稿池(content_publisher queue,带固定文案),发布永远人工。
缺数据(节假日/扫描未跑)非零退出并说明,不硬造。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from davis_analyzer.systems.cardgen import daily, ledger            # noqa: E402
from davis_analyzer.systems.cardgen.builder import render           # noqa: E402


def _projects_root() -> Path:
    return Path(os.environ.get("CARDGEN_PROJECT_ROOT", REPO_ROOT / "docs" / "小红书卡片"))

def _ledger_db() -> Path | None:
    env = os.environ.get("CARDGEN_LEDGER_DB")
    return Path(env) if env else None


def enqueue_one(kind: str, day: str, proj: Path, topic: str, release: dict) -> bool:
    """渲染完成后入发稿池(daily.publish_copy 文案+当日盘后观察);发布留人工。"""
    try:
        bundle = daily.fetch_day_bundle(daily.stockhot_db_path(), day)
        copy = daily.publish_copy(kind, day, bundle)
    except Exception as e:  # noqa: BLE001 —— 洞察附加失败不阻塞入池,回退静态文案
        print(f"! {topic} 盘后观察附加失败({e!r}),回退静态文案")
        copy = daily.publish_copy(kind, day)
    images = ",".join(str(proj / img) if not img.startswith("/") else img
                      for img in release["images"])
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "content_publisher" / "queue.py"),
           "enqueue", "--title", copy["title"], "--body", copy["body"],
           "--tags", copy["tags"], "--images", images,
           "--source", str(proj.relative_to(REPO_ROOT))]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"✗ {topic} 入池失败: {e!r}")
        return False
    print(proc.stdout.strip() or proc.stderr.strip())
    if proc.returncode != 0:
        print(f"✗ {topic} 入池失败(exit {proc.returncode})")
        return False
    conn = ledger.connect(_ledger_db())
    try:
        ledger.set_status(conn, topic, "queued")
    finally:
        conn.close()
    return True


def _vision_check(proj: Path, topic: str, release: dict) -> bool:
    """渲染后逐张 vision 目检(2026-09-18 用户授权, 公告日报流程同款)。

    任一张 pass=false → 返回 False(调用方不入池, 卡留 rendered 等人工);
    vision API 故障 → 重试一次, 仍失败则放行并告警(目检故障不静默杀产卡线)。
    依据 AGENTS 视觉任务规范: 结构化 JSON, 主流程只消费结论。"""
    import json as _json
    prompt = ("这是小红书金融数据卡片,请目检并返回JSON:{\"pass\":bool,\"issues\":[str]}。"
              "检查:文字无溢出卡片边界、无互相重叠遮挡;表格/文本块完整未截断(尤其底部);"
              "清晰可读;无明显异常留白;有问题给具体位置。")
    for img in release["images"]:
        p = proj / img if not img.startswith("/") else Path(img)
        ok, detail = False, ""
        for attempt in (1, 2):
            proc = subprocess.run(
                [sys.executable, str(REPO_ROOT / "scripts" / "content_publisher" / "vision.py"),
                 str(p), "--prompt", prompt],
                capture_output=True, text=True, cwd=REPO_ROOT, timeout=180)
            if proc.returncode == 0:
                try:
                    verdict = _json.loads(proc.stdout)
                    ok, detail = bool(verdict.get("pass")), str(verdict.get("issues", []))
                    break
                except Exception:
                    detail = f"vision输出解析失败: {proc.stdout[:120]}"
            else:
                detail = proc.stderr.strip()[-120:]
        if not ok and not detail:
            print(f"! {topic} vision 两次调用失败({Path(img).name}): {detail}——放行入池,待人工复核")
            continue
        if not ok:
            print(f"✗ {topic} vision 未过 [{Path(img).name}]: {detail}")
            return False
        print(f"✓ {topic} vision 通过 [{Path(img).name}]")
    return True


def run_one(kind: str, day: str, do_render: bool, do_enqueue: bool = False,
            do_push: bool = False, do_vision: bool = False) -> bool:
    try:
        proj, topic, report = daily.generate(kind, day, _projects_root(), _ledger_db())
    except daily.DailyDataMissing as e:
        print(f"✗ {kind} {day}: 数据不完整,拒绝生成——{e}")
        return False
    if not report.passed:
        print(f"✗ {kind} {day}: validate 未过({len(report.failures)} 项),未渲染")
        for f in report.failures:
            print(f"    - {f}")
        return False
    print(f"✓ {topic} validate 通过 | as_of={report.as_of} expires={report.expires_at}")
    if not do_render:
        return True
    conn = ledger.connect(_ledger_db())
    try:
        release = render(proj, topic, conn)
        print(f"✓ {topic} 渲染完成 v{release['version']}: {len(release['images'])} 张 PNG | "
              f"过期日 {release['expires_at']}")
    except (SystemExit, RuntimeError) as e:
        print(f"✗ {topic} 渲染失败: {e}")
        return False
    finally:
        conn.close()
    if do_vision and not _vision_check(proj, topic, release):
        print(f"✗ {topic} vision 目检未过, 不入池(卡留 rendered, 等人工)——路径 {proj}")
        return False
    if do_enqueue:
        if not enqueue_one(kind, day, proj, topic, release):
            return False
    if do_push:
        if not push_one(kind, day, proj, topic, release):
            return False
    return True


def push_one(kind: str, day: str, proj: Path, topic: str, release: dict) -> bool:
    """渲染完成后推「红薯财经博主运营」群(封面图+发稿文案);失败不阻断入池结果,只告警。

    幂等锁 logs/.xhs_card_push/{day}_{kind}.ok——当日该品类已推过则跳过。"""
    lock_dir = REPO_ROOT / "logs" / ".xhs_card_push"
    lock = lock_dir / f"{day}_{kind}.ok"
    if lock.exists():
        print(f"✓ {topic} 当日已推过群,跳过({lock.name})")
        return True
    import asyncio
    try:
        if kind == "thermo":
            # thermo 数据源是 market_data.db thermometer 表,不走 stockhot bundle
            copy = daily.publish_copy(kind, day, daily.fetch_thermo_bundle(day))
        else:
            bundle = daily.fetch_day_bundle(daily.stockhot_db_path(), day)
            copy = daily.publish_copy(kind, day, bundle)
    except Exception:  # noqa: BLE001 —— 与 enqueue_one 同口径,回退静态文案
        copy = daily.publish_copy(kind, day)
    try:
        from stockhot.notification.feishu_bot import EnterpriseFeishuNotifier
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / ".env")
        chat = os.environ.get("FEISHU_XHS_CHAT_ID", "")
        if not chat:
            print("! 未配置 FEISHU_XHS_CHAT_ID,跳过群推送")
            return True

        async def _send() -> None:
            n = EnterpriseFeishuNotifier(os.environ["FEISHU_APP_ID"],
                                         os.environ["FEISHU_APP_SECRET"], chat)
            for img in release["images"]:
                p = proj / img
                if p.exists():
                    await n.send_image(str(p))
            # tags 必须是消息最后一行:话题标签后跟任何文字都会失效(XHS 规则),
            # 运营提示(发布人工)只放头部括号行,复制区(正文+tags)保持纯净
            await n.send_text(f"【{day} 复盘卡·已入池待审,发布人工】{copy['title']}\n\n{copy['body']}\n\n{copy['tags']}")
        asyncio.run(_send())
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        print(f"✓ {topic} 已推红薯运营群({len(release['images'])} 图)")
        return True
    except Exception as e:  # noqa: BLE001 —— 推送失败不影响出卡与入池
        print(f"! {topic} 群推送失败({e!r}),卡片与入池不受影响")
        return True


def main() -> None:
    ap = argparse.ArgumentParser(description="每日盘面复盘卡(连板天梯+龙虎榜)")
    ap.add_argument("--type", choices=["ladder", "lhb", "thermo", "screener", "all"], default="all")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--no-render", action="store_true", help="只生成+validate,不渲染(调试用)")
    ap.add_argument("--enqueue", action="store_true",
                    help="渲染成功后入发稿池(固定文案,发布仍留人工)")
    ap.add_argument("--push", action="store_true",
                    help="渲染成功后推红薯运营群(封面图+文案,发布仍留人工)")
    ap.add_argument("--vision", action="store_true",
                    help="渲染后逐张 vision 目检,未过不入池(2026-09-18;目检API故障放行+告警)")
    args = ap.parse_args()
    kinds = ["ladder", "lhb"] if args.type == "all" else [args.type]  # thermo 单独跑(19:35 温度数据就绪后)
    ok = all(run_one(k, args.date, not args.no_render, args.enqueue, args.push, args.vision) for k in kinds)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
