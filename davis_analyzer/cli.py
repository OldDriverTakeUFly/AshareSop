"""CLI entry point for Davis Double Valuation Analyzer."""

from __future__ import annotations

import argparse
import sys

from loguru import logger

_DEFAULT_CHECKLIST_DIR = "davis_analyzer/studies/checklists/"


def main() -> None:
    """Parse arguments and dispatch to subcommands."""
    parser = argparse.ArgumentParser(
        prog="davis_analyzer",
        description="A股戴维斯双击估值分析器 — 基于戴维斯双击理论筛选低估值标的",
    )
    subparsers = parser.add_subparsers(dest="command")

    _add_run_parser(subparsers)
    _add_deep_research_parser(subparsers)
    _add_rescore_parser(subparsers)

    args = parser.parse_args()

    if args.command == "run":
        _run_command(args)
    elif args.command == "deep-research":
        _deep_research_command(args)
    elif args.command == "rescore":
        _rescore_command(args)
    else:
        parser.print_help()


def _add_run_parser(subparsers) -> None:
    run_parser = subparsers.add_parser("run", help="运行估值筛选分析")
    run_parser.add_argument("--top", type=int, default=30, help="输出前N个标的 (默认30)")
    run_parser.add_argument("--output", type=str, default=None, help="报告输出目录")


def _add_deep_research_parser(subparsers) -> None:
    dr_parser = subparsers.add_parser("deep-research", help="生成深度调研checklist")
    dr_parser.add_argument(
        "--top",
        type=int,
        default=3,
        help="为前N个标的生成checklist (默认3)",
    )
    dr_parser.add_argument(
        "--checklist-dir",
        type=str,
        default=_DEFAULT_CHECKLIST_DIR,
        help="checklist输出目录",
    )


def _add_rescore_parser(subparsers) -> None:
    rs_parser = subparsers.add_parser("rescore", help="根据填写的checklist重新评分")
    rs_parser.add_argument(
        "--checklist-dir",
        type=str,
        default=_DEFAULT_CHECKLIST_DIR,
        help="包含已填写checklist的目录",
    )


def _run_pipeline(args: argparse.Namespace):
    """Shared pipeline invocation for run / deep-research / rescore."""
    from davis_analyzer.pipeline import run_screening_pipeline

    logger.info("Starting Davis Double screening pipeline...")
    try:
        return run_screening_pipeline(dry_run=False, top_n=getattr(args, "top", 30))
    except Exception as exc:
        logger.error("Pipeline failed: {}", exc)
        print(f"\nPipeline error: {exc}")
        print("Check Tushare API token and network connectivity.")
        sys.exit(1)


def _run_command(args: argparse.Namespace) -> None:
    results = _run_pipeline(args)
    if results is None:
        return

    if not results.scores:
        logger.warning("No results returned. Try without --dry-run or check cache.")
        print("\nNo stocks matched the screening criteria.")
        sys.exit(0)

    print(f"\nTop {len(results.scores)} stocks identified by Davis Double screening:\n")
    print(
        f"{'Rank':>4}  {'Code':<10}  {'Name':<10}  {'Final':>6}  "
        f"{'Val':>6}  {'Trend':>6}  {'Pros':>6}  {'Dist':>6}"
    )
    print("-" * 72)
    for ds in results.scores:
        print(
            f"{ds.rank:>4}  {ds.ts_code:<10}  {ds.name:<10}  "
            f"{ds.final_score:>6.1f}  {ds.valuation_score:>6.1f}  "
            f"{ds.trend_score:>6.1f}  {ds.prosperity_score:>6.1f}  "
            f"{ds.distress_score:>6.1f}"
        )

    if args.output:
        from davis_analyzer.report_generator import save_all_reports

        logger.info("Generating reports to directory: {}", args.output)
        try:
            saved = save_all_reports(results, args.output)
            print(f"\n已生成 {len(saved)} 个研报文件到 {args.output}/")
        except Exception as exc:
            logger.error("Report generation failed: {}", exc)
            print(f"\n报告生成失败: {exc}")


def _deep_research_command(args: argparse.Namespace) -> None:
    from davis_analyzer.checklist_generator import generate_batch_checklists

    run_args = argparse.Namespace(dry_run=False, top=args.top)
    results = _run_pipeline(run_args)
    if results is None or not results.scores:
        print("\nNo pipeline results — cannot generate checklists.")
        return

    logger.info("Generating checklists for top {} stocks to {}", args.top, args.checklist_dir)
    try:
        saved = generate_batch_checklists(results, args.checklist_dir, top_n=args.top)
        print(f"\n已生成 {len(saved)} 个调研checklist到 {args.checklist_dir}/")
    except Exception as exc:
        logger.error("Checklist generation failed: {}", exc)
        print(f"\nChecklist生成失败: {exc}")


def _rescore_command(args: argparse.Namespace) -> None:
    from davis_analyzer.rescorer import batch_rescore

    run_args = argparse.Namespace(dry_run=False, top=30)
    results = _run_pipeline(run_args)
    if results is None or not results.scores:
        print("\nNo pipeline results — cannot rescore.")
        return

    logger.info("Rescoring from checklists in {}", args.checklist_dir)
    try:
        rescored = batch_rescore(results, args.checklist_dir)
    except Exception as exc:
        logger.error("Rescore failed: {}", exc)
        print(f"\n重新评分失败: {exc}")
        return

    if not rescored:
        print(f"\n在 {args.checklist_dir}/ 中未找到已填写的checklist。")
        return

    print(f"\n已重新评分 {len(rescored)} 只标的:\n")
    print(
        f"{'Code':<10}  {'Name':<10}  "
        f"{'原景气':>6}  {'新景气':>6}  "
        f"{'原困境':>6}  {'新困境':>6}"
    )
    print("-" * 65)
    for rr in rescored.values():
        print(
            f"{rr.ts_code:<10}  {rr.name:<10}  "
            f"{rr.original_prosperity:>6.1f}  {rr.adjusted_prosperity:>6.1f}  "
            f"{rr.original_distress:>6.1f}  {rr.adjusted_distress:>6.1f}"
        )


if __name__ == "__main__":
    main()
