"""CLI entry point for Davis Double Valuation Analyzer."""

from __future__ import annotations

import argparse
import sys

from loguru import logger


def main() -> None:
    """Parse arguments and dispatch to subcommands."""
    parser = argparse.ArgumentParser(
        prog="davis_analyzer",
        description="A股戴维斯双击估值分析器 — 基于戴维斯双击理论筛选低估值标的",
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="运行估值筛选分析")
    run_parser.add_argument(
        "--dry-run", action="store_true", help="使用缓存数据，不调用API"
    )
    run_parser.add_argument(
        "--top", type=int, default=30, help="输出前N个标的 (默认30)"
    )
    run_parser.add_argument(
        "--output", type=str, default=None, help="报告输出目录"
    )

    args = parser.parse_args()

    if args.command == "run":
        _run_command(args)
    else:
        parser.print_help()


def _run_command(args: argparse.Namespace) -> None:
    from davis_analyzer.pipeline import run_screening_pipeline

    if args.dry_run:
        print("\nDry-run mode: pipeline dry_run relies on cache availability.")
        print("Use without --dry-run for a full screening run.\n")
        return

    logger.info("Starting Davis Double screening pipeline...")
    try:
        results = run_screening_pipeline(dry_run=False, top_n=args.top)
    except Exception as exc:
        logger.error("Pipeline failed: {}", exc)
        print(f"\nPipeline error: {exc}")
        print("Check Tushare API token and network connectivity.")
        sys.exit(1)

    if not results:
        logger.warning("No results returned. Try without --dry-run or check cache.")
        print("\nNo stocks matched the screening criteria.")
        sys.exit(0)

    print(f"\nTop {len(results)} stocks identified by Davis Double screening:\n")
    print(
        f"{'Rank':>4}  {'Code':<10}  {'Name':<10}  {'Final':>6}  "
        f"{'Val':>6}  {'Pros':>6}  {'Dist':>6}"
    )
    print("-" * 65)
    for ds in results:
        print(
            f"{ds.rank:>4}  {ds.ts_code:<10}  {ds.name:<10}  "
            f"{ds.final_score:>6.1f}  {ds.valuation_score:>6.1f}  "
            f"{ds.prosperity_score:>6.1f}  {ds.distress_score:>6.1f}"
        )

    # save_all_reports() requires intermediate data (stock_info, valuation,
    # prosperity, distress) per stock — not available from the current
    # pipeline return type.  Full reports need a pipeline refactor.
    if args.output:
        logger.info(
            "Full report generation requires intermediate pipeline data "
            "(not yet available). Skipping save."
        )

    print("\nRe-run without --dry-run for live data.")


if __name__ == "__main__":
    main()
