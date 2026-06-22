"""Deep-research checklist generator for Davis Double Play analysis.

Produces markdown checklists for manual qualitative research.
The rescorer (T13) reads the two adjustment fields at the bottom of each
checklist to recompute final scores:
  - 景气度调整幅度（-20到+20）
  - 困境反转调整幅度（-20到+20）
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from davis_analyzer.types import (
        DavisDoubleScore,
        DistressSignal,
        PipelineResult,
        ProsperityScore,
        StockInfo,
    )


# ── constants ────────────────────────────────────────────────────────

_PLACEHOLDER = "暂无"


# ── public API ───────────────────────────────────────────────────────


def generate_checklist(
    stock_info: StockInfo,
    davis_score: DavisDoubleScore,
    prosperity_score: ProsperityScore | None,
    distress_score: DistressSignal | None,
    output_dir: str,
) -> str:
    """Generate a single-stock deep-research checklist markdown file.

    Parameters
    ----------
    stock_info : StockInfo
        Basic stock metadata.
    davis_score : DavisDoubleScore
        Composite scoring result (provides final_score, rank, etc.).
    prosperity_score : ProsperityScore | None
        Prosperity sub-score detail (``None`` → shows "暂无").
    distress_score : DistressSignal | None
        Distress signal detail (``None`` → shows "暂无").
    output_dir : str
        Directory to write the checklist file into.

    Returns
    -------
    str  Absolute path of the written file.
    """
    cyclical_label = "是" if stock_info.is_cyclical else "否"

    prosperity_display = (
        f"{prosperity_score.composite_score:.1f}" if prosperity_score is not None else _PLACEHOLDER
    )
    distress_display = (
        f"{distress_score.total_score:.1f}" if distress_score is not None else _PLACEHOLDER
    )

    content = _CHECKLIST_TEMPLATE.format(
        rank=davis_score.rank,
        ts_code=stock_info.ts_code,
        name=stock_info.name,
        industry=stock_info.industry or "未知",
        cyclical=cyclical_label,
        final_score=f"{davis_score.final_score:.1f}",
        valuation_score=f"{davis_score.valuation_score:.1f}",
        trend_score=f"{davis_score.trend_score:.1f}",
        prosperity_score=f"{davis_score.prosperity_score:.1f}",
        distress_score=f"{davis_score.distress_score:.1f}",
        prosperity_display=prosperity_display,
        distress_display=distress_display,
    )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    filename = f"{davis_score.rank}_{stock_info.ts_code}_{stock_info.name}_调研checklist.md"
    filepath = out / filename
    filepath.write_text(content, encoding="utf-8")
    return str(filepath)


def generate_batch_checklists(
    pipeline_result: PipelineResult,
    output_dir: str,
    top_n: int = 30,
) -> list[str]:
    """Generate checklists for the top *top_n* stocks in a PipelineResult.

    Parameters
    ----------
    pipeline_result : PipelineResult
        Full pipeline output containing scores and lookup dicts.
    output_dir : str
        Directory to write checklist files into.
    top_n : int
        Maximum number of stocks to generate checklists for.

    Returns
    -------
    list[str]  Absolute file paths of all written checklists.
    """
    saved: list[str] = []

    for score in pipeline_result.scores[:top_n]:
        ts_code = score.ts_code

        stock_info = pipeline_result.stock_infos.get(ts_code)
        if stock_info is None:
            continue

        prosperity = pipeline_result.prosperity_scores.get(ts_code)
        distress = pipeline_result.distress_signals.get(ts_code)

        filepath = generate_checklist(
            stock_info=stock_info,
            davis_score=score,
            prosperity_score=prosperity,
            distress_score=distress,
            output_dir=output_dir,
        )
        saved.append(filepath)

    return saved


# ── template ─────────────────────────────────────────────────────────

_CHECKLIST_TEMPLATE = """\
# {rank}_{ts_code}_{name} 深度调研Checklist

## 基础信息（自动填充）
| 项目 | 值 |
|------|-----|
| 股票代码 | {ts_code} |
| 股票名称 | {name} |
| 所属行业 | {industry} |
| 周期股 | {cyclical} |
| 综合评分 | {final_score} |
| 估值分位 | {valuation_score} |
| 趋势评分 | {trend_score} |
| 景气度评分 | {prosperity_score} |
| 困境反转评分 | {distress_score} |
| 排名 | {rank} |

## 调研项（请人工填写）
### 1. 行业政策
- [ ] 当前行业政策环境（利好/中性/利空）：___
- [ ] 政策变化趋势：___
- [ ] 相关政策文件/日期：___

### 2. 机构观点
- [ ] 最近3个月机构评级：___
- [ ] 目标价共识：___
- [ ] 主要机构观点摘要：___

### 3. 公司公告
- [ ] 近期重大公告：___
- [ ] 公告日期及内容：___

### 4. 竞争格局
- [ ] 行业竞争地位：___
- [ ] 主要竞争对手及市场份额：___
- [ ] 竞争优势/护城河：___

### 5. 管理层治理
- [ ] 管理层评价：___
- [ ] 股权激励/管理层持股：___
- [ ] 公司治理风险点：___

### 6. 定性判断
#### 景气度调整幅度（-20到+20）
> 当前景气度评分：{prosperity_display}
___（请填写调整值）

#### 困境反转调整幅度（-20到+20）
> 当前困境反转评分：{distress_display}
___（请填写调整值）
"""
