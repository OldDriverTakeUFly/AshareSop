"""Publisher module for StockHot-CN."""

from datetime import datetime

from stockhot.storage.database import get_images_by_date, get_analysis_result
import json


def run_publish(date: str | None = None, dry_run: bool = False) -> dict:
    """Run publishing for specified date."""
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    print(f"[Publisher] 发布日期: {target_date}")

    if dry_run:
        print("[Publisher] 试运行模式, 不实际发布")
        return {"date": target_date, "status": "dry_run", "dry_run": True}

    images = get_images_by_date(target_date)
    analysis = get_analysis_result(target_date, "report")

    if not images:
        print("[Publisher] 无图片可发布")
        return {"date": target_date, "status": "no_images"}

    image_paths = [img["file_path"] for img in images]
    caption = _generate_caption(analysis) if analysis else _default_caption(target_date)

    result = publish_to_xiaohongshu(image_paths, caption)

    _save_publish_record(target_date, "xiaohongshu", result)

    print("[Publisher] 发布完成")
    return {"date": target_date, "status": "success", "dry_run": False}


def publish_to_xiaohongshu(images: list[str], caption: str) -> dict:
    """发布到小红书"""
    print("[Publisher] 准备发布到小红书")
    print(f"[Publisher] 图片数量: {len(images)}")
    print(f"[Publisher] 标题: {caption[:50]}...")

    return {
        "platform": "xiaohongshu",
        "images_count": len(images),
        "caption_length": len(caption),
        "status": "mock_success",
    }


def _generate_caption(analysis: dict | None) -> str:
    """Generate caption from AI analysis."""
    if not analysis:
        return _default_caption(datetime.now().strftime("%Y-%m-%d"))

    result = analysis.get("result_json", {})
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            result = {}

    text = result.get("text", "")
    if text:
        return text[:500]
    return _default_caption(datetime.now().strftime("%Y-%m-%d"))


def _default_caption(date: str) -> str:
    """Generate default caption."""
    return f"""📈 A股每日热点分析 {date}

今日市场有哪些机会？来看看详细数据~

#A股 #每日复盘 #投资 #热点追踪"""


def _save_publish_record(date: str, platform: str, result: dict) -> None:
    """Save publish record to database."""
    from stockhot.storage.database import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO publish_records (trade_date, platform, status, response_json) VALUES (?, ?, ?, ?)",
            (date, platform, result.get("status", "unknown"), json.dumps(result)),
        )
        conn.commit()
    finally:
        conn.close()
