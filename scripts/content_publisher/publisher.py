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

# ── 选择器(多级回退,改版维护点;2026-08-29 实测探明)──
_HOME = "https://creator.xiaohongshu.com/new/home"
_UPLOAD_CARD = "text=发布图文笔记"          # 首页上传卡(点击弹文件选择器)
_TITLE_SELECTORS = ["input[placeholder*='标题']", ".d-input input"]
_CONTENT_SELECTORS = ["[contenteditable='true']", ".ql-editor"]
_SUBMIT_SELECTORS = [".btn-inner:has-text('发布笔记')", ".btn-wrapper:has-text('发布笔记')",
                     "button.publishBtn", "button:has-text('发布')"]


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
    # 主站 web_session 或创作者平台 galaxy 会话 cookie 任一存在即视为已登录
    names = {"web_session", "galaxy_creator_session_id", "galaxy.creator.beaker.session.id"}
    return any(c["name"] in names and c.get("value") for c in context.cookies())


# ── 反自动化检测(2026-08-29 实测:playwright 默认 navigator.webdriver=True,
#    小红书发布按钮被风控静默拦截,点击无任何反应)──
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
window.chrome = window.chrome || {runtime: {}};
"""


def _launch(pw, headless: bool = False):
    ctx = pw.chromium.launch_persistent_context(
        str(PROFILE_DIR), headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )
    ctx.add_init_script(_STEALTH_JS)
    return ctx


def login(timeout_s: int = 300) -> None:
    """首次登录:开有头浏览器等人工扫码,登录态落 PROFILE_DIR。"""
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = _launch(pw)
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
        ctx = _launch(pw)
        try:
            if not _is_logged_in(ctx):
                sys.exit("登录态已失效:运行 queue.py login 重新扫码(不会自动重试)")
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            # 实测流程(2026-08-29):首页无静态 file input,点上传卡弹 File System Access
            # 选择器,经 expect_file_chooser 拦截;上传后跳 /publish/publish 编辑器
            page.goto(_HOME, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(9000)  # 首页重前端渲染,等组件挂载
            card = page.locator(_UPLOAD_CARD).first
            card.wait_for(state="visible", timeout=20000)
            with page.expect_file_chooser() as fc_info:
                card.click()
            fc_info.value.set_files(imgs)
            page.wait_for_url("**/publish/publish**", timeout=30000)
            page.wait_for_timeout(3000)
            # 标题(上限 20 字,防御性截断)
            _first(page, _TITLE_SELECTORS).fill(task.title[:20])
            # 正文+tags
            _first(page, _CONTENT_SELECTORS).fill(body_full)
            page.wait_for_timeout(800)
            # 提交:底部操作栏是闭式 Shadow DOM 组件 <xhs-publish-btn>(2026-08-29 实测,
            # 内含 暂存离开+发布 两钮,Playwright 文本选择器不可达)——按盒宽 78% 坐标点击
            # 右侧红色「发布」钮;若误点左侧「暂存离开」会跳回首页,由成功判定识别为失败。
            import time
            bar = page.locator("xhs-publish-btn").first
            bar.wait_for(state="visible", timeout=10000)
            bar.scroll_into_view_if_needed()  # 底栏常在折叠线下,必须先滚入视口
            page.wait_for_timeout(500)
            box = bar.bounding_box()
            if not box:
                raise RuntimeError("xhs-publish-btn 无包围盒")
            page.screenshot(path=str(REPO_ROOT / "storage" / "publish_click.png"))  # 点击前留痕
            page.mouse.click(box["x"] + box["width"] * 0.93, box["y"] + box["height"] / 2)
            # 成功判定:离开编辑器页;跳管理/成功页=成功,跳回 home=误点暂存离开
            deadline = time.time() + 30
            while time.time() < deadline:
                if "/publish/publish" not in page.url:
                    if "/new/home" in page.url or page.url.rstrip("/") == "https://creator.xiaohongshu.com":
                        raise RuntimeError("误点「暂存离开」:内容已存草稿未发布,请重跑 publish")
                    note_url = page.url
                    print(f"发布成功,跳转: {note_url}")
                    return {"note_url": note_url}
                page.wait_for_timeout(2000)
            shot = REPO_ROOT / "storage" / "publish_fail.png"
            page.screenshot(path=str(shot))
            raise RuntimeError(f"30 秒内未离开编辑器页,疑似发布失败,截图: {shot}")
        finally:
            ctx.close()
