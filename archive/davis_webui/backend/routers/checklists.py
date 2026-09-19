from fastapi import APIRouter, HTTPException

from davis_webui.backend.schemas import (
    ChecklistData,
    ChecklistFillRequest,
    ChecklistGenerateRequest,
    ChecklistSection,
    RescoreRequest,
    RescoreResultItem,
)
from davis_webui.backend.tasks import task_manager

router = APIRouter()

_filled_checklists: dict[str, ChecklistFillRequest] = {}

_CHECKLIST_SECTIONS = [
    ChecklistSection(
        title="行业政策",
        items=[
            "当前行业政策环境（利好/中性/利空）",
            "政策变化趋势",
            "相关政策文件/日期",
        ],
    ),
    ChecklistSection(
        title="机构观点",
        items=[
            "最近3个月机构评级",
            "目标价共识",
            "主要机构观点摘要",
        ],
    ),
    ChecklistSection(
        title="公司公告",
        items=["近期重大公告", "公告日期及内容"],
    ),
    ChecklistSection(
        title="竞争格局",
        items=[
            "行业竞争地位",
            "主要竞争对手及市场份额",
            "竞争优势/护城河",
        ],
    ),
    ChecklistSection(
        title="管理层治理",
        items=[
            "管理层评价",
            "股权激励/管理层持股",
            "公司治理风险点",
        ],
    ),
]


@router.post("/generate")
async def generate_checklists(req: ChecklistGenerateRequest):
    info = task_manager.get_task(req.task_id)
    if info is None or info.result is None:
        raise HTTPException(
            status_code=404, detail="Task or results not found"
        )
    result = info.result

    checklists = []
    for score in result.scores[: req.top_n]:
        ts_code = score.ts_code
        prosperity = result.prosperity_scores.get(ts_code)
        distress = result.distress_signals.get(ts_code)

        prosperity_display = (
            f"{prosperity.composite_score:.1f}" if prosperity else "暂无"
        )
        distress_display = (
            f"{distress.total_score:.1f}" if distress else "暂无"
        )

        checklists.append(
            ChecklistData(
                ts_code=ts_code,
                name=score.name,
                rank=score.rank,
                scores={
                    "final": score.final_score,
                    "valuation": score.valuation_score,
                    "trend": score.trend_score,
                    "prosperity": score.prosperity_score,
                    "distress": score.distress_score,
                },
                prosperity_display=prosperity_display,
                distress_display=distress_display,
                sections=list(_CHECKLIST_SECTIONS),
            )
        )

    return {"checklists": checklists}


@router.post("/{ts_code}/fill")
async def fill_checklist(ts_code: str, req: ChecklistFillRequest):
    req.prosperity_adjustment = max(
        -20.0, min(20.0, req.prosperity_adjustment)
    )
    req.distress_adjustment = max(
        -20.0, min(20.0, req.distress_adjustment)
    )

    _filled_checklists[ts_code] = req

    return {"success": True}


@router.post("/rescore")
async def rescore(req: RescoreRequest):
    info = task_manager.get_task(req.task_id)
    if info is None or info.result is None:
        raise HTTPException(
            status_code=404, detail="Task or results not found"
        )
    result = info.result

    from davis_analyzer.rescorer import rescore as do_rescore

    results = []
    for score in result.scores:
        ts_code = score.ts_code
        filled = _filled_checklists.get(ts_code)
        if filled is None:
            continue

        checklist_data = {
            "ts_code": ts_code,
            "name": score.name,
            "rank": score.rank,
            "prosperity_adjustment": filled.prosperity_adjustment,
            "distress_adjustment": filled.distress_adjustment,
            "raw_research": {},
        }

        rescored = do_rescore(
            original_prosperity=score.prosperity_score,
            original_distress=score.distress_score,
            checklist_data=checklist_data,
        )

        results.append(
            RescoreResultItem(
                ts_code=rescored.ts_code,
                name=rescored.name,
                original_prosperity=rescored.original_prosperity,
                adjusted_prosperity=rescored.adjusted_prosperity,
                original_distress=rescored.original_distress,
                adjusted_distress=rescored.adjusted_distress,
                prosperity_adjustment=rescored.prosperity_adjustment,
                distress_adjustment=rescored.distress_adjustment,
            )
        )

    return {"results": results}
