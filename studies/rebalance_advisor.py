"""调仓建议生成器 — 比对选股 top20 与实盘持仓，生成建议清单（不执行）.

⚠️ 本脚本只读 + 生成建议报告，绝不写 invest_holdings。
   所有调仓建议需人工确认后通过 Web API / CLI 手动执行。

两阶段 diff（参考 backtest.rebalance 算法）：
  - 调出建议：实盘持仓不在 top20 的 → 标注"建议关注/止损"
  - 新进建议：top20 不在实盘持仓的 → 标注"候选建仓" + 参考价位

带 market_regime 门控：熊市标注"不建议新开仓"。

Usage:
    .venv/bin/python studies/rebalance_advisor.py [--date YYYY-MM-DD]

Output: docs/回测记录/调仓建议_<date>.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "studies" / "output"
DOCS_DIR = PROJECT_ROOT / "docs" / "回测记录"


def _ts_code_to_code(ts_code: str) -> str:
    """603629.SH → 603629."""
    return ts_code.split(".")[0]


def _get_market_regime(as_of: date) -> str:
    """获取市场状态（HMM，零 API）。失败返回 'unknown'."""
    try:
        from davis_analyzer.market_regime import get_market_regime

        regime = get_market_regime(as_of.isoformat())
        return regime or "unknown"
    except Exception:
        return "unknown"


def generate_rebalance_report(as_of: str) -> str:
    """生成调仓建议 markdown 报告（不执行任何调仓）."""
    from stockhot.storage.database import get_connection

    # ── 读 top20 ──
    json_path = OUTPUT_DIR / f"top20_screen_{as_of}.json"
    if not json_path.exists():
        raise FileNotFoundError(f"选股结果不存在: {json_path}")
    with open(json_path, encoding="utf-8") as f:
        screen_data = json.load(f)
    top20 = screen_data.get("top20", [])
    top20_codes = {_ts_code_to_code(r["ts_code"]) for r in top20}

    # ── 读实盘持仓 ──
    conn = get_connection()
    try:
        holdings = [
            dict(row) for row in conn.execute(
                "SELECT code, name, current_price, stop_loss_hard, target_price "
                "FROM invest_holdings WHERE status = 'active'"
            )
        ]
    finally:
        conn.close()
    held_codes = {h["code"] for h in holdings}

    # ── 两阶段 diff ──
    to_review = [h for h in holdings if h["code"] not in top20_codes]  # 调出候选
    to_buy = [r for r in top20 if _ts_code_to_code(r["ts_code"]) not in held_codes]  # 新进候选

    # ── market_regime 门控 ──
    regime = _get_market_regime(datetime.strptime(as_of, "%Y-%m-%d").date())
    bear_market = regime == "bear"

    # ── 生成报告 ──
    lines = [
        f"# 调仓建议 | {as_of}",
        "",
        f"> **生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> **选股日期**：{as_of}",
        f"> **市场状态**：{regime}",
        "",
        "## ⚠️ 重要声明",
        "",
        "**本报告仅供参考，不构成投资建议，绝不自动执行调仓。**",
        "所有调仓操作需你人工确认后通过 Web API / CLI 手动执行。",
        "",
        "---",
        "",
    ]

    if bear_market:
        lines += [
            "## 🔴 熊市门控",
            "",
            f"当前市场状态为 **{regime}**，**不建议新开仓**。新进建议仅供观察，",
            "等待市场状态切换到 neutral/bull 后再考虑建仓。",
            "",
            "---",
            "",
        ]

    # ── 调出建议（持仓不在 top20）──
    lines += [
        "## 一、调出建议（实盘持仓不在选股 top20）",
        "",
    ]
    if not to_review:
        lines.append("（所有实盘持仓均在 top20，无需调出）\n")
    else:
        lines.append("| 代码 | 名称 | 现价 | 硬止损 | 说明 |")
        lines.append("|------|------|------|--------|------|")
        for h in to_review:
            cp = h.get("current_price") or "—"
            sl = h.get("stop_loss_hard") or "—"
            # 止损触发判断
            note = "跌出选股 top20，建议评估持有逻辑"
            if isinstance(cp, (int, float)) and isinstance(sl, (int, float)) and sl > 0:
                if cp <= sl:
                    note = "⚠️ **现价已破硬止损，建议止损**"
            lines.append(f"| {h['code']} | {h.get('name', '-')} | {cp} | {sl} | {note} |")
        lines.append("")

    # ── 新进建议（top20 不在持仓）──
    lines += [
        "## 二、新进建议（选股 top20 未持有）",
        "",
    ]
    if not to_buy:
        lines.append("（top20 候选均已持有，无新进建议）\n")
    else:
        lines.append("| 代码 | 名称 | 综合分 | 现价 | 目标价 | 技术止损 | 域 |")
        lines.append("|------|------|--------|------|--------|----------|------|")
        for r in to_buy:
            code = _ts_code_to_code(r["ts_code"])
            score = r.get("composite", "—")
            cp = r.get("current_price") or "—"
            tp = r.get("target_price") or "—"
            sl = r.get("stop_loss_technical") or "—"
            domain = r.get("domain", "—")
            lines.append(f"| {code} | {r.get('name', '-')} | {score} | {cp} | {tp} | {sl} | {domain} |")
        lines.append("")
        lines.append("> 📊 目标价来自估值历史中位反推（PE/PB），技术止损为 trailing_stop（MA20/近20日低×0.98）。")
        lines.append("> '—' 表示数据不足无法计算。周期股/super_cycle 用 PB 反推。")
        lines.append("")

    lines += [
        "---",
        "",
        "## 三、操作指引",
        "",
        "1. **调出**：如确认持有逻辑破坏，通过 `holdings_cli.py remove --id <id>` 或 Web API 关闭持仓",
        "2. **新进**：确认后通过 `holdings_cli.py add` 或 Web API 建仓，建议参考技术止损设置止损价",
        "3. **本报告不执行任何操作**，仅提供建议",
        "",
        f"> 原始数据：`studies/output/top20_screen_{as_of}.json`",
    ]

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口."""
    parser = argparse.ArgumentParser(description="生成调仓建议报告（不执行）")
    parser.add_argument("--date", default=None, help="选股日期 YYYY-MM-DD（默认：今天）")
    args = parser.parse_args(argv)

    as_of = args.date or date.today().strftime("%Y-%m-%d")
    print(f"=== rebalance_advisor @ {datetime.now().isoformat()} | AS_OF={as_of} ===")

    try:
        report = generate_rebalance_report(as_of)
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = DOCS_DIR / f"调仓建议_{as_of}.md"
        out_path.write_text(report, encoding="utf-8")
        print(f"调仓建议已生成: {out_path}")
        return 0
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1
    except Exception as e:
        import traceback

        print(f"[ERROR] 生成失败: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
