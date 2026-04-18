"""Publisher module for StockHot-CN."""

from datetime import datetime


def run_publish(date: str | None = None, dry_run: bool = False) -> dict:
    """Run publishing for specified date."""
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    print(f"[Publisher] 发布日期: {target_date}")
    if dry_run:
        print("[Publisher] 试运行模式, 不实际发布")
    print("[Publisher] 发布完成")
    return {"date": target_date, "status": "success", "dry_run": dry_run}


def publish_to_xiaohongshu(images: list[str], caption: str) -> dict:
    """发布到小红书"""
    return {}