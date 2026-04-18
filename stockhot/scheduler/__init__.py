"""Scheduler module for StockHot-CN."""

from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from stockhot.data_collector import run_collection
from stockhot.ai_analyzer import run_analysis
from stockhot.image_generator import run_generation
from stockhot.publisher import run_publish
from stockhot.storage.database import init_database, cleanup_old_data


def run_daily_workflow(date: str | None = None) -> dict:
    """Run the complete daily workflow."""
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*50}")
    print(f"开始每日工作流程 - {target_date}")
    print(f"{'='*50}\n")

    results = {}

    print("[Step 1/4] 数据采集...")
    results["collection"] = run_collection(target_date)

    print("\n[Step 2/4] AI分析...")
    results["analysis"] = run_analysis(target_date)

    print("\n[Step 3/4] 图片生成...")
    results["generation"] = run_generation(target_date)

    print("\n[Step 4/4] 发布...")
    results["publish"] = run_publish(target_date)

    print("\n" + "="*50)
    print("每日工作流程完成!")
    print("="*50 + "\n")

    return results


def run_scheduler() -> None:
    """Run the scheduler for daily automation."""
    scheduler = BlockingScheduler()

    scheduler.add_job(
        run_daily_workflow,
        CronTrigger(hour=16, minute=30),
        id="daily_workflow",
        name="每日A股热点分析",
        replace_existing=True,
    )

    scheduler.add_job(
        lambda: cleanup_old_data(90),
        CronTrigger(hour=3, minute=0),
        id="cleanup",
        name="清理过期数据",
        replace_existing=True,
    )

    print("Scheduler started. Press Ctrl+C to exit.")
    print("Daily workflow runs at 16:30 (4:30 PM) every weekday.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler stopped.")


def trigger_manual(date: str | None = None) -> None:
    """Manually trigger the daily workflow."""
    init_database()
    run_daily_workflow(date)