"""国产光刻胶标的景气度分析（正确API版）。

使用 fetch_batch_financial → batch_prosperity → compute_relative_delta_g → classify_stock_stage
"""
import os
from dotenv import load_dotenv
load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

import sys
import loguru
loguru.logger.remove()
loguru.logger.add(sys.stderr, level="ERROR")

import pandas as pd
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_batch_financial
from davis_analyzer.prosperity import batch_prosperity
from davis_analyzer.prosperity_sector import classify_stock_stage, compute_relative_delta_g, generate_risk_warnings
from davis_analyzer.prosperity_inflection import analyze_inflection
from davis_analyzer.types import StockInfo

# 国产光刻胶 A 股标的
PHOTORESIST_STOCKS = {
    "300346.SZ": ("南大光电", "KrF光刻胶+ArF光刻胶+前驱体", "高端"),
    "300236.SZ": ("上海新阳", "ArF光刻胶+电镀液+清洗液", "高端"),
    "603306.SH": ("华懋科技", "光刻胶单体+汽车安全气囊材料", "高端"),
    "300655.SZ": ("晶瑞电材", "i线光刻胶+ArF研发+高纯试剂", "中高端"),
    "300576.SZ": ("容大感光", "PCB光刻胶+感光油墨", "中端"),
    "300537.SZ": ("广信材料", "PCB光刻胶+UV涂料", "中端"),
    "300398.SZ": ("飞凯材料", "光刻胶+半导体材料+紫外固化", "中端"),
    "300429.SZ": ("强力新材", "光刻胶光引发剂+树脂", "上游"),
    "688199.SH": ("久日新材", "光刻胶树脂+光引发剂", "上游"),
    "603650.SH": ("彤程新材", "橡胶助剂+光刻胶(北京科华)", "延伸"),
    "002643.SZ": ("万润股份", "显示材料+光刻胶中间体+沸石", "延伸"),
    "603110.SH": ("东方材料", "软包油墨+光刻胶(拟收购)", "延伸"),
    "688019.SH": ("安集科技", "CMP抛光液+光刻胶去除剂", "配套"),
}


def main():
    client = TushareClient()

    print("=" * 90)
    print("国产光刻胶标的景气度分析（G+ΔG框架）")
    print("=" * 90)

    ts_codes = list(PHOTORESIST_STOCKS.keys())

    # 批量拉取财务数据
    print(f"\n拉取 {len(ts_codes)} 只标的的财务数据...")
    financial_data_map = fetch_batch_financial(client, ts_codes, periods=8)

    print(f"成功获取 {len(financial_data_map)} 只:")
    for code in ts_codes:
        name = PHOTORESIST_STOCKS[code][0]
        if code in financial_data_map:
            n = len(financial_data_map[code])
            print(f"  ✓ {name:8s} {code}  {n}期")
        else:
            print(f"  ✗ {name:8s} {code}  拉取失败")

    if not financial_data_map:
        print("无数据，退出")
        return

    # 批量计算景气度
    prosperity_scores = batch_prosperity(financial_data_map)

    # 构造 stock_infos（统一行业="光刻胶"作为内部基准）
    stock_infos = {}
    for code in prosperity_scores.keys():
        name, biz, tier = PHOTORESIST_STOCKS.get(code, ("?", "?", "?"))
        stock_infos[code] = StockInfo(
            ts_code=code, name=name, industry="光刻胶",
            list_status="L", is_cyclical=True
        )

    # 计算相对 ΔG
    compute_relative_delta_g(prosperity_scores, stock_infos)

    # 逐只分析
    results = []
    for ts_code, score in prosperity_scores.items():
        name, biz, tier = PHOTORESIST_STOCKS.get(ts_code, ("?", "?", "?"))

        stage = classify_stock_stage(score)
        risks = []
        try:
            risks = generate_risk_warnings(score, financial_data_map[ts_code])
        except:
            pass

        try:
            inflection = analyze_inflection(score, stage)
            inflection_narrative = inflection.narrative if inflection else ""
        except:
            inflection_narrative = ""

        is_ignition = (score.delta_g is not None and score.delta_g > 0 and score.composite_score > 50)

        latest_fin = financial_data_map[ts_code][0] if financial_data_map[ts_code] else None

        results.append({
            "ts_code": ts_code,
            "name": name,
            "tier": tier,
            "biz": biz,
            "score": score,
            "stage": stage,
            "is_ignition": is_ignition,
            "risks": risks,
            "inflection_narrative": inflection_narrative,
            "latest_fin": latest_fin,
        })

    results.sort(key=lambda x: x["score"].composite_score, reverse=True)

    # === 汇总表 ===
    print(f"\n{'='*90}")
    print("景气度排名汇总表")
    print(f"{'='*90}\n")
    print(f"{'排名':>3} {'名称':8s} {'代码':12s} {'梯队':6s} {'综合分':>6} {'ΔG':>8} {'相对ΔG':>8} {'周期':10s} {'点火':4s}")
    print("─" * 90)

    for i, r in enumerate(results, 1):
        s = r["score"]
        ig = "★" if r["is_ignition"] else ""
        delta_g_str = f"{s.delta_g:+.2f}" if s.delta_g is not None else "N/A"
        rel_str = f"{s.relative_delta_g:+.2f}" if s.relative_delta_g is not None else "N/A"
        print(f"{i:3d}  {r['name']:8s} {r['ts_code']:12s} {r['tier']:6s} "
              f"{s.composite_score:6.1f} {delta_g_str:>8} {rel_str:>8} "
              f"{str(r['stage']):10s} {ig:4s}")

    # === 详细分析 ===
    print(f"\n{'='*90}")
    print("逐只标的详细分析")
    print(f"{'='*90}")

    for i, r in enumerate(results, 1):
        s = r["score"]
        print(f"\n{'─'*90}")
        delta_g_str = f"{s.delta_g:+.2f}" if s.delta_g is not None else "N/A"
        print(f"  {i}. 【{r['name']}】{r['ts_code']}  [{r['tier']}]  综合:{s.composite_score:.1f}  ΔG:{delta_g_str}  周期:{r['stage']}  {'★二次点火' if r['is_ignition'] else ''}")
        print(f"{'─'*90}")

        # 财务明细
        fin = r["latest_fin"]
        if fin:
            rev_yoy = f"{fin.yoy_revenue_growth:+.1f}%" if fin.yoy_revenue_growth else "N/A"
            profit_yoy = f"{fin.yoy_profit_growth:+.1f}%" if fin.yoy_profit_growth else "N/A"
            rev_str = f"{fin.revenue/1e8:.2f}亿" if fin.revenue else "N/A"
            np_str = f"{fin.net_profit/1e8:.2f}亿" if fin.net_profit else "N/A"
            roe_str = f"{fin.roe:.2f}%" if fin.roe else "N/A"
            gm_str = f"{fin.grossprofit_margin:.1f}%" if fin.grossprofit_margin else "N/A"
            print(f"  业务: {r['biz']}")
            print(f"  最新季报({fin.report_period}): 营收={rev_str}({rev_yoy})  归母={np_str}({profit_yoy})  ROE={roe_str}  毛利率={gm_str}")

        # 景气分项
        print(f"  景气分项: 营收={s.revenue_score:.1f}  净利={s.profit_score:.1f}  斜率={s.slope_score:.1f}  持续={s.duration_score:.1f}")

        # 风险
        if r["risks"]:
            print(f"  ⚠ 风险: {', '.join(r['risks'])}")

        # 山峰理论
        if s.delta_g is not None:
            if s.delta_g > 0 and s.composite_score > 50:
                mt = "左山坡（加速期 G↑+ΔG↑ → Davis Double 机会区）"
            elif s.delta_g < 0 and s.composite_score > 50:
                mt = "右山坡（减速期 G高ΔG↓ → 杀估值风险区）"
            elif s.delta_g > 0 and s.composite_score <= 50:
                mt = "山后回升（底部拐点 ΔG转正 G仍低）"
            else:
                mt = "山后下行（G↓+ΔG↓ → 规避区）"
        else:
            mt = "ΔG 数据不足"
        print(f"  山峰定位: {mt}")

        # 30% 阈值检验
        if fin and fin.yoy_profit_growth is not None:
            if fin.yoy_profit_growth < 30 and fin.yoy_profit_growth > 0:
                print(f"  ⚠ 30%阈值: 净利增速 {fin.yoy_profit_growth:.1f}% < 30%，超额收益概率下降")

    # === 二次点火汇总 ===
    ignition = [r for r in results if r["is_ignition"]]
    print(f"\n{'='*90}")
    print(f"★ 二次点火筛选（G>0 且 ΔG>0 且 综合>50）: {len(ignition)} 只")
    print(f"{'='*90}")
    for r in ignition:
        s = r["score"]
        print(f"  ★ {r['name']:8s} {r['ts_code']}  综合={s.composite_score:.1f}  ΔG={s.delta_g:+.2f}  相对ΔG={s.relative_delta_g:+.2f}")


if __name__ == "__main__":
    main()
