#!/usr/bin/env python3
# content_publisher/publisher.py — M3 自动发帖执行器(playwright + 持久登录态 + 频控护栏)
# 设计:登录态经持久化浏览器 profile(storage/browser_profile_xhs)托管,人工扫码一次;
#      发布走创作者平台网页;选择器带多级回退,小红书改版时优先修 _TITLE/_CONTENT/_SUBMIT。
# 不做:无登录态瞎试、绕风控;登录失效 → 显式失败等人工,绝不重试登录。
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent.parent
PROFILE_DIR = Path(os.environ.get("PUBLISHER_PROFILE_DIR",
                                  REPO_ROOT / "storage" / "browser_profile_xhs"))
CREATOR = "https://creator.xiaohongshu.com"

# 频控护栏:单日上限 / 最小发布间隔
GUARD_DAILY_LIMIT = 2
GUARD_MIN_INTERVAL_MIN = 30

# ── 选择器(多级回退,改版维护点)──
_TITLE_SELECTORS = ["input[placeholder*='标题']", ".d-input input", "input.title-input"]
_CONTENT_SELECTORS = ["#post-textarea", ".ql-editor", "[contenteditable='true']"]
_SUBMIT_SELECTORS = ["button.publishBtn", "button:has-text('发布')", ".publish-btn"]


@dataclass
class PublishTask:
    """一条待发布内容(来自 publish_queue 行)。"""

    qid: int
    title: str
    body: str
    tags: str
    images: list[str]


def select_publishable(due_rows: list[dict], published_today: int,
                       last_publish_at: str | None, now: str) -> tuple[list[dict], list[str]]:
    """纯函数护栏:从 due 行中挑出可发布子集。

    due_rows: [{id,title,scheduled_at,release_expires,...}](来自 due 查询)
    published_today: 今日已发布条数;last_publish_at: 最近一次发布 ISO 时间
    返回 (待发布行, 跳过原因列表)——跳过不报错,只留痕。
    """
    todo: list[dict] = []
    skipped: list[str] = []
    today = now[:10]
    if published_today >= GUARD_DAILY_LIMIT:
        return [], [f"单日上限 {GUARD_DAILY_LIMIT} 已满(今日已发 {published_today})"]
    if last_publish_at:
        elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(last_publish_at))
        if elapsed < timedelta(minutes=GUARD_MIN_INTERVAL_MIN):
            return [], [f"距上次发布不足 {GUARD_MIN_INTERVAL_MIN} 分钟(上次 {last_publish_at})"]
    for r in due_rows:
        if r.get("release_expires") and r["release_expires"] < today:
            skipped.append(f"#{r['id']} 数据已过期(有效至 {r['release_expires']}),跳过待 bump 重发")
            continue
        todo.append(r)
        if len(todo) + published_today >= GUARD_DAILY_LIMIT:
            skipped.append("已达单日上限,其余顺延")
            break
    return todo, skipped


def _first(page, selectors: list[str], timeout: int = 15000):
    """按序尝试选择器,返回第一个可见者;全失败抛 RuntimeError(附全部选择器便于修)。"""
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout)
            return loc
        except Exception:  # noqa: BLE001 逐个回退
            continue
    raise RuntimeError(f"选择器全部失效(小红书改版?): {selectors}")


def _is_logged_in(context) -> bool:
    return any(c["name"] == "web_session" and c.get("value") for c in context.cookies())


def login(timeout_s: int = 300) -> None:
    """首次登录:开有头浏览器等人工扫码,登录态落 PROFILE_DIR。"""
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{CREATOR}/")
        print("请在打开的浏览器中完成登录(扫码),最长等待 5 分钟…")
        deadline = datetime.now() + timedelta(seconds=timeout_s)
        while datetime.now() < deadline:
            if _is_logged_in(ctx):
                print("登录成功,登录态已持久化(下次免扫码)")
                ctx.close()
                return
            page.wait_for_timeout(3000)
        ctx.close()
        sys.exit("超时未登录:重新运行 login,profile 会保留已填账号")


def publish_one(task: PublishTask, headless: bool = False) -> dict:
    """发布一条:上传图片 → 标题/正文/tags → 点发布 → 等成功页。返回 {note_url}。"""
    if not PROFILE_DIR.exists():  # 先查登录态,再碰 playwright(无浏览器依赖也能干净报错)
        sys.exit("无登录态:先运行 queue.py login 完成扫码")
    from playwright.sync_api import sync_playwright
    imgs = [str(Path(p) if Path(p).is_absolute() else REPO_ROOT / p) for p in task.images]
    for p in imgs:
        if not Path(p).exists():
            sys.exit(f"图片不存在: {p}")
    body_full = (task.body or "").rstrip()
    if task.tags:
        body_full = (body_full + "\n\n" + task.tags).strip()

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), headless=headless)
        try:
            if not _is_logged_in(ctx):
                sys.exit("登录态已失效:运行 queue.py login 重新扫码(不会自动重试)")
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(f"{CREATOR}/publish/paste?source=official", wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            # 图片(多选上传)
            page.locator("input[type='file']").first.set_input_files(imgs)
            page.wait_for_timeout(3000)
            # 标题(上限 20 字,防御性截断)
            _first(page, _TITLE_SELECTORS).fill(task.title[:20])
            # 正文+tags
            _first(page, _CONTENT_SELECTORS).fill(body_full)
            page.wait_for_timeout(800)
            # 提交
            _first(page, _SUBMIT_SELECTORS).click()
            # 成功判定:跳转 success 页(带 note id)或出现成功提示
            try:
                page.wait_for_url("**/publish/success**", timeout=20000)
            except Exception:  # noqa: BLE001
                # 兜底:可能停在原页+失败 toast,人工确认场景下让调用方看截图
                shot = REPO_ROOT / "storage" / "publish_fail.png"
                page.screenshot(path=str(shot))
                raise RuntimeError(f"未跳转成功页,疑似发布失败,截图: {shot}") from None
            note_url = page.url
            print(f"发布成功: {note_url}")
            return {"note_url": note_url}
        finally:
            ctx.close()
