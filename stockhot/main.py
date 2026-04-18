"""Main entry point for StockHot-CN CLI."""

import sys
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    parser = argparse.ArgumentParser(
        prog="stockhot",
        description="A股每日热点分析工具 - 自动采集市场数据并生成小红书内容",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    parser.add_argument(
        "--mode",
        choices=["collect", "analyze", "generate", "publish", "all"],
        default="all",
        help="运行模式: collect(数据采集) analyze(AI分析) generate(图片生成) publish(发布) all(全部)",
    )
    parser.add_argument("--date", help="指定日期 (YYYY-MM-DD), 默认今日")
    parser.add_argument("--dry-run", action="store_true", help="试运行, 不实际发布")

    args = parser.parse_args()

    print("StockHot-CN 启动中...")
    print(f"运行模式: {args.mode}")
    print(f"日期: {args.date or '今日'}")

    if args.mode in ("collect", "all"):
        from stockhot.data_collector import run_collection
        run_collection(date=args.date)

    if args.mode in ("analyze", "all"):
        from stockhot.ai_analyzer import run_analysis
        run_analysis(date=args.date)

    if args.mode in ("generate", "all"):
        from stockhot.image_generator import run_generation
        run_generation(date=args.date)

    if args.mode in ("publish", "all"):
        from stockhot.publisher import run_publish
        run_publish(date=args.date, dry_run=args.dry_run)

    print("完成!")


if __name__ == "__main__":
    main()