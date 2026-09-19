# davis_analyzer/recap/recorder_sheet.py
"""recap 录制任务单:生成 markdown、飞书推送(幂等)、素材文件名协议对位。"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from loguru import logger

from davis_analyzer.systems.recap.constants import CLIP_FILENAME_RE, INBOX_DIR, REPO_ROOT
from davis_analyzer.systems.recap.types import Candidate, Episode

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
            "   App路径(东方财富): 搜索代码 → 分时 → 底部「功能」→ 超级复盘(同花顺: 分时左下角「超级盘口」,需Level-2)",
            f"   回放区间: {c.replay_start[:5]}-{c.replay_end[:5]} | 建议倍速: 1x",
            f"   保存为: {day_compact}_{c.ts_code}_{i:02d}.mp4", "",
        ]
    lines.append("录完把文件丢进 inbox 目录,然后跑 `python -m davis_analyzer.systems.recap audio`。")
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
            # 评审修复:两条路径都不得覆盖已对位段落(杂散文件后到会静默顶掉正确素材)
            seg = next((s for s in stock_segs
                        if s.ts_code == ts_code and s.seg_id == f"s{seq}"
                        and s.seg_id not in matched), None)
            if seg is None:
                seg = next((s for s in stock_segs
                            if s.ts_code == ts_code and s.seg_id not in matched), None)
            if seg is not None:
                matched[seg.seg_id] = p
            else:
                logger.warning(f"recap inbox 文件无可认领段落,忽略: {p.name}")
    missing = [s.seg_id for s in stock_segs if s.seg_id not in matched]
    return matched, missing


def push_sheet(day_dash: str, markdown: str, dry_run: bool = False) -> bool:
    """推录制单到红薯运营群(纯文本);幂等锁 logs/.recap_sheet/{day}.ok;失败不阻断。

    锁只在推送成功或无 chat id 跳过后落盘;推送异常不落锁,下次运行可重试
    (对齐 daily_market_cards.push_one 只在 send 成功后写锁的口径)。"""
    lock = _lock_path(day_dash)
    if lock.exists():
        logger.info(f"recap 录制单 {day_dash} 已推过,跳过")
        return True
    if dry_run:
        return True   # 纯预览:不推送、不落锁
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
    except Exception as e:  # noqa: BLE001 —— 推送失败不阻断流程,但不落锁(可重试)
        logger.warning(f"录制单推送失败({e!r}),未落锁,下次运行可重试")
        return True
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    return True
