# davis_analyzer/metrics/collector.py
"""半自动采集:复用 publisher 浏览器 profile 打开笔记管理页 → vision 读数 → 落库。

纪律:只读自己账号的创作者平台页面;读数异常(非负整数缺失)跳过该行不写脏数据。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from davis_analyzer.metrics.db import connect, record_account_metrics, record_note_metrics, upsert_note

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = REPO_ROOT / "storage" / "browser_profile_xhs"
NOTE_MANAGER_URL = "https://creator.xiaohongshu.com/new/note-manager"
VISION_SCRIPT = REPO_ROOT / "scripts" / "content_publisher" / "vision.py"

_PROMPT = (
    "这是小红书创作者平台「笔记管理」页截图。请提取:1) 顶部账号栏的 关注数/粉丝数/获赞与收藏;"
    "2) 每条笔记行的:标题、发布时间、以及后面的数字列(依次为 观看数/点赞/收藏/评论/分享,列名以页面表头为准)。"
    "只返回JSON,不要多余文字:"
    '{"account":{"followers":int|null,"following":int|null,"total_likes":int|null},'
    '"notes":[{"title":str,"published_at":str,"views":int|null,"likes":int|null,'
    '"collects":int|null,"comments":int|null,"shares":int|null}]}')


def _read_page() -> tuple[Path, str]:
    """打开笔记管理页(持久 profile),返回(截图路径, 页面纯文本)。"""
    from playwright.sync_api import sync_playwright

    shot = REPO_ROOT / "storage" / "metrics_capture.png"
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"])
        try:
            ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(NOTE_MANAGER_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(8000)
            page.screenshot(path=str(shot), full_page=True)
            return shot, page.inner_text("body")
        finally:
            ctx.close()


def _vision_read(shot: Path) -> dict:
    """调 scripts/content_publisher/vision.py(glm-5.3-flash)读结构化 JSON。"""
    proc = subprocess.run(
        [sys.executable, str(VISION_SCRIPT), str(shot), "--prompt", _PROMPT],
        capture_output=True, text=True, timeout=180, cwd=REPO_ROOT)
    out = proc.stdout.strip()
    i, j = out.find("{"), out.rfind("}")
    if i == -1 or j == -1:
        raise RuntimeError(f"vision 无 JSON 输出: {out[:200]} {proc.stderr[:200]}")
    return json.loads(out[i:j + 1])


def _to_int(v: object) -> int | None:
    """'1.2万'→12000;'34'→34;None/脏值→None(跳过信号)。"""
    if v is None:
        return None
    if isinstance(v, int):
        return v if v >= 0 else None
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "—", "环比-"):
        return None
    try:
        if s.endswith("万"):
            return int(float(s[:-1]) * 10000)
        return int(float(s)) if float(s) >= 0 else None
    except ValueError:
        return None


def collect(account_id: str) -> dict:
    """一次采集:截图 → vision → 写快照。返回统计;读数行级失败只跳过不中断。"""
    shot, body = _read_page()
    data = _vision_read(shot)
    now = datetime.now().isoformat(timespec="seconds")
    stats = {"notes": 0, "skipped": 0, "account": False}

    acc = data.get("account") or {}
    followers = _to_int(acc.get("followers"))
    if followers is not None:
        with connect() as c:
            record_account_metrics(c, account_id, now, followers=followers,
                                   following=_to_int(acc.get("following")),
                                   total_likes=_to_int(acc.get("total_likes")), source="vision")
        stats["account"] = True

    # grp 猜测:标题含关键词映射到内容组(粗粒度,manual 可修)
    def _grp(title: str) -> str:
        for kw, g in [("估值分位", "工具方法"), ("周期", "工具方法"), ("财报数据", "工具方法"),
                      ("产业链怎么看", "工具方法"), ("美联储", "工具方法"), ("加息", "工具方法"),
                      ("缩表", "工具方法"), ("沃什", "工具方法"), ("黄金", "工具方法"),
                      ("有色", "工具方法"), ("CPI", "工具方法"), ("点阵图", "工具方法")]:
            if kw in title:
                return g
        return "产业链调研"

    with connect() as c:
        for n in data.get("notes", []):
            title = str(n.get("title") or "").strip()
            if not title:
                continue
            views = _to_int(n.get("views"))
            if views is None:  # 核心读数缺失 → 整行跳过(防脏数据)
                stats["skipped"] += 1
                logger.warning(f"跳过读数异常行: {title[:30]} raw={n}")
                continue
            note_id = upsert_note(c, account_id, title,
                                  grp=_grp(title), published_at=str(n.get("published_at") or ""))
            record_note_metrics(c, note_id, now, views=views,
                                likes=_to_int(n.get("likes")), collects=_to_int(n.get("collects")),
                                comments=_to_int(n.get("comments")), shares=_to_int(n.get("shares")),
                                source="vision")
            stats["notes"] += 1
    logger.info(f"采集完成: {stats}(页面含笔记数≥{body.count('2026-')})")
    return stats
