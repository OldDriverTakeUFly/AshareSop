"""Scheduler module for StockHot-CN."""

from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from stockhot.data_collector import run_collection
from stockhot.ai_analyzer import run_analysis
from stockhot.image_generator import run_generation
from stockhot.publisher import run_publish
from stockhot.hotspot_discovery import run_hotspot_discovery
from stockhot.research_report import run_research_report
from stockhot.limit_up import run_limit_up_analysis
from stockhot.dragon_tiger import run_dragon_tiger_analysis
from stockhot.fund_flow import run_fund_flow_analysis
from stockhot.risk_alert import run_risk_alert_analysis
from stockhot.storage.database import init_database, cleanup_old_data
from stockhot.core.config import (
    SCHEDULER_WORKFLOW_HOUR,
    SCHEDULER_WORKFLOW_MINUTE,
    SCHEDULER_CLEANUP_HOUR,
    SCHEDULER_CLEANUP_MINUTE,
    DATA_RETENTION_DAYS,
)


def run_daily_workflow(date: str | None = None) -> dict:
    """Run the complete daily workflow."""
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*50}")
    print(f"开始每日工作流程 - {target_date}")
    print(f"{'='*50}\n")

    results = {}

    print("[Step 1/10] 数据采集...")
    results["collection"] = run_collection(target_date)

    print("\n[Step 2/10] AI分析...")
    results["analysis"] = run_analysis(target_date)

    print("\n[Step 3/10] 涨停板分析...")
    results["limit_up"] = run_limit_up_analysis(target_date)

    print("\n[Step 4/10] 龙虎榜分析...")
    results["dragon_tiger"] = run_dragon_tiger_analysis(target_date)

    print("\n[Step 5/10] 资金流向分析...")
    results["fund_flow"] = run_fund_flow_analysis(target_date)

    print("\n[Step 6/10] 风险提示分析...")
    results["risk_alert"] = run_risk_alert_analysis(target_date)

    print("\n[Step 7/10] 热点发现...")
    hotspot_result = run_hotspot_discovery(target_date)
    results["hotspot_discovery"] = hotspot_result

    print("\n[Step 8/10] 主题报告...")
    lead_theme = hotspot_result.get("lead_theme") if hotspot_result else None
    results["theme_report"] = run_research_report(date=target_date, theme=lead_theme)

    print("\n[Step 9/10] 图片生成...")
    results["generation"] = run_generation(target_date)

    print("\n[Step 10/10] 发布...")
    results["publish"] = run_publish(target_date)

    print("\n" + "=" * 50)
    print("每日工作流程完成!")
    print("=" * 50 + "\n")

    return results


def run_scheduler() -> None:
    """Run the scheduler for daily automation."""
    scheduler = BlockingScheduler()

    scheduler.add_job(
        run_daily_workflow,
        CronTrigger(hour=SCHEDULER_WORKFLOW_HOUR, minute=SCHEDULER_WORKFLOW_MINUTE),
        id="daily_workflow",
        name="每日A股热点分析",
        replace_existing=True,
    )

    scheduler.add_job(
        lambda: cleanup_old_data(DATA_RETENTION_DAYS),
        CronTrigger(hour=SCHEDULER_CLEANUP_HOUR, minute=SCHEDULER_CLEANUP_MINUTE),
        id="cleanup",
        name="清理过期数据",
        replace_existing=True,
    )

    print("Scheduler started. Press Ctrl+C to exit.")
    print(
        f"Daily workflow runs at {SCHEDULER_WORKFLOW_HOUR}:{SCHEDULER_WORKFLOW_MINUTE:02d} every weekday."
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler stopped.")


def trigger_manual(date: str | None = None) -> None:
    """Manually trigger the daily workflow."""
    init_database()
    run_daily_workflow(date)
