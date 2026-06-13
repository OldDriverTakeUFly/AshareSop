"""Tests for report_generator: generate_stock_report, save_all_reports, summary index."""

from __future__ import annotations

import pytest

from davis_analyzer.report_generator import (
    _compute_dupont_conclusion,
    _get_investment_conclusion,
    _get_valuation_judgment,
    generate_stock_report,
    generate_summary_index,
    save_all_reports,
)
from davis_analyzer.types import (
    DavisDoubleScore,
    DistressSignal,
    FinancialData,
    PipelineResult,
    ProsperityScore,
    StockInfo,
    ValuationData,
)


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def sample_stock_info():
    return StockInfo(
        ts_code="000001.SZ",
        name="平安银行",
        industry="银行",
        list_status="L",
        is_cyclical=False,
    )


@pytest.fixture
def sample_valuation_data():
    return (65.0, 0.15, 0.25)


@pytest.fixture
def sample_prosperity():
    return ProsperityScore(
        ts_code="000001.SZ",
        revenue_score=78.0,
        profit_score=72.0,
        slope_score=68.0,
        duration_score=75.0,
        composite_score=73.5,
        delta_g=5.2,
    )


@pytest.fixture
def sample_distress():
    return DistressSignal(
        ts_code="000001.SZ",
        layer1_score=80.0,
        layer2_score=70.0,
        layer3_score=60.0,
        total_score=69.0,
        signals_detail={
            "eps_decline": True,
            "pe_pb_percentile": "PE/PB处于历史低位",
            "risk_items": ["营收增速下滑"],
        },
    )


@pytest.fixture
def sample_davis_score():
    return DavisDoubleScore(
        ts_code="000001.SZ",
        name="平安银行",
        valuation_score=65.0,
        prosperity_score=73.5,
        distress_score=69.0,
        final_score=69.2,
        rank=1,
    )


@pytest.fixture
def sample_financial_data():
    return FinancialData(
        ts_code="000001.SZ",
        report_period="20240331",
        revenue=10000.0,
        net_profit=3000.0,
        eps=0.50,
        roe=15.0,
        operating_cf=2000.0,
        total_debt=5000.0,
        total_assets=15000.0,
        yoy_revenue_growth=12.0,
        yoy_profit_growth=18.0,
    )


@pytest.fixture
def sample_valuation_history():
    return ValuationData(
        ts_code="000001.SZ",
        trade_date="20240401",
        pe_ttm=8.5,
        pb=0.9,
        ps=2.1,
        total_mv=3200.0,
    )


@pytest.fixture
def sample_pipeline_result(
    sample_stock_info,
    sample_valuation_data,
    sample_prosperity,
    sample_distress,
    sample_davis_score,
    sample_financial_data,
):
    return PipelineResult(
        scores=[sample_davis_score],
        stock_infos={"000001.SZ": sample_stock_info},
        valuation_data={"000001.SZ": sample_valuation_data},
        prosperity_scores={"000001.SZ": sample_prosperity},
        distress_signals={"000001.SZ": sample_distress},
        financial_data={"000001.SZ": [sample_financial_data]},
    )


@pytest.fixture
def multi_stock_pipeline_result():
    infos = {
        "000001.SZ": StockInfo(
            "000001.SZ", "平安银行", "银行", "L", False
        ),
        "600036.SH": StockInfo(
            "600036.SH", "招商银行", "银行", "L", False
        ),
        "000858.SZ": StockInfo(
            "000858.SZ", "五粮液", "食品饮料", "L", False
        ),
    }
    val_data = {
        "000001.SZ": (60.0, 0.15, 0.25),
        "600036.SH": (55.0, 0.30, 0.40),
        "000858.SZ": (70.0, 0.10, 0.20),
    }
    prosperity = {
        "000001.SZ": ProsperityScore(
            "000001.SZ", 78.0, 72.0, 68.0, 75.0, 73.5, 5.2
        ),
        "600036.SH": ProsperityScore(
            "600036.SH", 65.0, 60.0, 55.0, 50.0, 58.75, 2.1
        ),
        "000858.SZ": ProsperityScore(
            "000858.SZ", 85.0, 80.0, 75.0, 100.0, 84.25, 8.3
        ),
    }
    distress = {
        "000001.SZ": DistressSignal(
            "000001.SZ", 80.0, 70.0, 60.0, 69.0, {"eps_decline": True}
        ),
        "600036.SH": DistressSignal(
            "600036.SH", 50.0, 45.0, 40.0, 44.5, {}
        ),
        "000858.SZ": DistressSignal(
            "000858.SZ", 90.0, 85.0, 80.0, 84.5, {"pe_pb_percentile": "深度低估"}
        ),
    }
    fin_data = {
        "000001.SZ": [
            FinancialData(
                "000001.SZ", "20240331", 10000.0, 3000.0, 0.50, 15.0,
                2000.0, 5000.0, 15000.0, 12.0, 18.0,
            )
        ],
        "600036.SH": [
            FinancialData(
                "600036.SH", "20240331", 8000.0, 2500.0, 0.45, 14.0,
                1800.0, 4000.0, 12000.0, 8.0, 10.0,
            )
        ],
        "000858.SZ": [
            FinancialData(
                "000858.SZ", "20240331", 15000.0, 6000.0, 1.20, 25.0,
                4000.0, 3000.0, 20000.0, 20.0, 25.0,
            )
        ],
    }
    scores = [
        DavisDoubleScore("000858.SZ", "五粮液", 70.0, 84.25, 84.5, 80.0, 1),
        DavisDoubleScore("000001.SZ", "平安银行", 60.0, 73.5, 69.0, 67.4, 2),
        DavisDoubleScore("600036.SH", "招商银行", 55.0, 58.75, 44.5, 53.0, 3),
    ]
    return PipelineResult(
        scores=scores,
        stock_infos=infos,
        valuation_data=val_data,
        prosperity_scores=prosperity,
        distress_signals=distress,
        financial_data=fin_data,
    )


# ── helper function tests ─────────────────────────────────────────────


class TestGetInvestmentConclusion:
    def test_strong_recommend(self):
        assert "强烈推荐" in _get_investment_conclusion(85.0)

    def test_recommend(self):
        assert "推荐关注" in _get_investment_conclusion(70.0)

    def test_watchable(self):
        assert "可关注" in _get_investment_conclusion(55.0)

    def test_cautious(self):
        assert "谨慎观察" in _get_investment_conclusion(40.0)


class TestGetValuationJudgment:
    def test_severely_undervalued(self):
        assert "严重低估" in _get_valuation_judgment(0.10)

    def test_slightly_undervalued(self):
        assert "偏低估" in _get_valuation_judgment(0.30)

    def test_reasonable(self):
        assert "合理" in _get_valuation_judgment(0.50)

    def test_overvalued(self):
        assert "偏高" in _get_valuation_judgment(0.80)


class TestComputeDupontConclusion:
    def test_with_valid_financial_data(self, sample_financial_data):
        result = _compute_dupont_conclusion(sample_financial_data)
        assert "数据不足" not in result
        assert any(
            kw in result
            for kw in ["净利率驱动", "周转率驱动", "杠杆驱动"]
        )

    def test_with_none(self):
        assert _compute_dupont_conclusion(None) == "数据不足"

    def test_zero_revenue(self):
        fin = FinancialData(
            "000001.SZ", "20240331", 0.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 0.0,
        )
        result = _compute_dupont_conclusion(fin)
        assert "数据周期不足" in result

    def test_zero_total_assets(self):
        fin = FinancialData(
            "000001.SZ", "20240331", 100.0, 30.0, 0.5, 15.0,
            20.0, 0.0, 0.0,
        )
        result = _compute_dupont_conclusion(fin)
        assert "数据周期不足" in result

    def test_zero_equity(self):
        fin = FinancialData(
            "000001.SZ", "20240331", 100.0, 30.0, 0.5, 15.0,
            20.0, 100.0, 100.0,
        )
        result = _compute_dupont_conclusion(fin)
        assert "数据周期不足" in result

    def test_net_margin_dominant(self):
        fin = FinancialData(
            "000001.SZ", "20240331",
            revenue=100.0, net_profit=200.0,
            eps=1.0, roe=50.0,
            operating_cf=50.0, total_debt=100.0, total_assets=1000.0,
        )
        result = _compute_dupont_conclusion(fin)
        assert "净利率驱动" in result


# ── generate_stock_report tests ───────────────────────────────────────


class TestGenerateStockReport:
    def test_report_has_all_7_chapters(
        self,
        sample_stock_info,
        sample_valuation_data,
        sample_prosperity,
        sample_distress,
        sample_davis_score,
        sample_financial_data,
    ):
        md = generate_stock_report(
            stock_info=sample_stock_info,
            valuation_data=sample_valuation_data,
            prosperity=sample_prosperity,
            distress=sample_distress,
            davis_score=sample_davis_score,
            financial_data=sample_financial_data,
        )
        for chapter in range(1, 8):
            assert f"## {chapter}." in md, f"Missing chapter {chapter}"

    def test_report_contains_stock_name(
        self,
        sample_stock_info,
        sample_valuation_data,
        sample_prosperity,
        sample_distress,
        sample_davis_score,
    ):
        md = generate_stock_report(
            stock_info=sample_stock_info,
            valuation_data=sample_valuation_data,
            prosperity=sample_prosperity,
            distress=sample_distress,
            davis_score=sample_davis_score,
        )
        assert "平安银行" in md
        assert "000001.SZ" in md

    def test_dupont_not_data_insufficient_when_financial_data_provided(
        self,
        sample_stock_info,
        sample_valuation_data,
        sample_prosperity,
        sample_distress,
        sample_davis_score,
        sample_financial_data,
    ):
        md = generate_stock_report(
            stock_info=sample_stock_info,
            valuation_data=sample_valuation_data,
            prosperity=sample_prosperity,
            distress=sample_distress,
            davis_score=sample_davis_score,
            financial_data=sample_financial_data,
        )
        assert "数据不足" not in md
        assert "杜邦分析结论" in md

    def test_dupont_shows_data_insufficient_without_financial_data(
        self,
        sample_stock_info,
        sample_valuation_data,
        sample_prosperity,
        sample_distress,
        sample_davis_score,
    ):
        md = generate_stock_report(
            stock_info=sample_stock_info,
            valuation_data=sample_valuation_data,
            prosperity=sample_prosperity,
            distress=sample_distress,
            davis_score=sample_davis_score,
            financial_data=None,
        )
        assert "数据不足" in md

    def test_report_with_valuation_history(
        self,
        sample_stock_info,
        sample_valuation_data,
        sample_prosperity,
        sample_distress,
        sample_davis_score,
        sample_financial_data,
        sample_valuation_history,
    ):
        md = generate_stock_report(
            stock_info=sample_stock_info,
            valuation_data=sample_valuation_data,
            prosperity=sample_prosperity,
            distress=sample_distress,
            davis_score=sample_davis_score,
            financial_data=sample_financial_data,
            valuation_history_latest=sample_valuation_history,
        )
        assert "3200" in md
        assert "8.5" in md

    def test_report_without_valuation_history_uses_defaults(
        self,
        sample_stock_info,
        sample_valuation_data,
        sample_prosperity,
        sample_distress,
        sample_davis_score,
        sample_financial_data,
    ):
        md = generate_stock_report(
            stock_info=sample_stock_info,
            valuation_data=sample_valuation_data,
            prosperity=sample_prosperity,
            distress=sample_distress,
            davis_score=sample_davis_score,
            financial_data=sample_financial_data,
        )
        assert "## 1. 公司概况" in md
        assert "## 2. 估值分析" in md

    def test_report_includes_scores(
        self,
        sample_stock_info,
        sample_valuation_data,
        sample_prosperity,
        sample_distress,
        sample_davis_score,
        sample_financial_data,
    ):
        md = generate_stock_report(
            stock_info=sample_stock_info,
            valuation_data=sample_valuation_data,
            prosperity=sample_prosperity,
            distress=sample_distress,
            davis_score=sample_davis_score,
            financial_data=sample_financial_data,
        )
        assert "73.5" in md
        assert "69.0" in md
        assert "69.2" in md

    def test_report_includes_signals_summary(
        self,
        sample_stock_info,
        sample_valuation_data,
        sample_prosperity,
        sample_distress,
        sample_davis_score,
        sample_financial_data,
    ):
        md = generate_stock_report(
            stock_info=sample_stock_info,
            valuation_data=sample_valuation_data,
            prosperity=sample_prosperity,
            distress=sample_distress,
            davis_score=sample_davis_score,
            financial_data=sample_financial_data,
        )
        assert "信号摘要" in md

    def test_report_includes_investment_conclusion(
        self,
        sample_stock_info,
        sample_valuation_data,
        sample_prosperity,
        sample_distress,
        sample_davis_score,
        sample_financial_data,
    ):
        md = generate_stock_report(
            stock_info=sample_stock_info,
            valuation_data=sample_valuation_data,
            prosperity=sample_prosperity,
            distress=sample_distress,
            davis_score=sample_davis_score,
            financial_data=sample_financial_data,
        )
        assert "## 7. 投资结论" in md
        assert "推荐关注" in md


# ── generate_summary_index tests ──────────────────────────────────────


class TestGenerateSummaryIndex:
    def test_summary_has_table_header(self, multi_stock_pipeline_result):
        md = generate_summary_index(
            multi_stock_pipeline_result.scores,
            multi_stock_pipeline_result.stock_infos,
            multi_stock_pipeline_result.valuation_data,
            multi_stock_pipeline_result.prosperity_scores,
        )
        assert "| 排名 |" in md
        assert "| 代码 |" in md
        assert "| 名称 |" in md

    def test_summary_lists_all_stocks(self, multi_stock_pipeline_result):
        md = generate_summary_index(
            multi_stock_pipeline_result.scores,
            multi_stock_pipeline_result.stock_infos,
            multi_stock_pipeline_result.valuation_data,
            multi_stock_pipeline_result.prosperity_scores,
        )
        assert "五粮液" in md
        assert "平安银行" in md
        assert "招商银行" in md

    def test_summary_contains_scores(self, multi_stock_pipeline_result):
        md = generate_summary_index(
            multi_stock_pipeline_result.scores,
            multi_stock_pipeline_result.stock_infos,
            multi_stock_pipeline_result.valuation_data,
            multi_stock_pipeline_result.prosperity_scores,
        )
        assert "80.0" in md
        assert "84.5" in md

    def test_summary_empty_list(self):
        md = generate_summary_index([], {}, {}, {})
        assert "TOP 0" in md


# ── save_all_reports tests ────────────────────────────────────────────


class TestSaveAllReports:
    def test_generates_individual_reports_and_summary(
        self,
        sample_pipeline_result,
        tmp_path,
    ):
        saved = save_all_reports(sample_pipeline_result, str(tmp_path))
        assert len(saved) == 2

    def test_individual_report_chinese_filename(
        self,
        sample_pipeline_result,
        tmp_path,
    ):
        saved = save_all_reports(sample_pipeline_result, str(tmp_path))
        report_files = [f for f in saved if "深度研报" in f]
        assert len(report_files) == 1
        assert "1_000001.SZ_平安银行_深度研报.md" in report_files[0]

    def test_summary_chinese_filename(
        self,
        sample_pipeline_result,
        tmp_path,
    ):
        saved = save_all_reports(sample_pipeline_result, str(tmp_path))
        summary_files = [f for f in saved if "汇总" in f]
        assert len(summary_files) == 1
        assert "戴维斯双击估值筛选汇总" in summary_files[0]

    def test_reports_are_valid_markdown(
        self,
        sample_pipeline_result,
        tmp_path,
    ):
        saved = save_all_reports(sample_pipeline_result, str(tmp_path))
        report_path = [p for p in saved if "深度研报" in p][0]
        content = open(report_path, encoding="utf-8").read()
        assert content.startswith("# 戴维斯双击分析报告")

    def test_report_dupont_not_insufficient(
        self,
        sample_pipeline_result,
        tmp_path,
    ):
        saved = save_all_reports(sample_pipeline_result, str(tmp_path))
        report_path = [p for p in saved if "深度研报" in p][0]
        content = open(report_path, encoding="utf-8").read()
        assert "数据不足" not in content
        assert any(
            kw in content
            for kw in ["净利率驱动", "周转率驱动", "杠杆驱动"]
        )

    def test_multiple_stocks(
        self,
        multi_stock_pipeline_result,
        tmp_path,
    ):
        saved = save_all_reports(multi_stock_pipeline_result, str(tmp_path))
        report_files = [f for f in saved if "深度研报" in f]
        assert len(report_files) == 3
        summary_files = [f for f in saved if "汇总" in f]
        assert len(summary_files) == 1
        assert len(saved) == 4

    def test_empty_pipeline_result(self, tmp_path):
        empty_result = PipelineResult(
            scores=[],
            stock_infos={},
            valuation_data={},
            prosperity_scores={},
            distress_signals={},
            financial_data={},
        )
        saved = save_all_reports(empty_result, str(tmp_path))
        assert saved == []

    def test_output_dir_created(self, sample_pipeline_result, tmp_path):
        new_dir = tmp_path / "subdir" / "reports"
        save_all_reports(sample_pipeline_result, str(new_dir))
        assert new_dir.exists()

    def test_skips_stock_missing_intermediate_data(self, tmp_path):
        scores = [
            DavisDoubleScore("999999.SZ", "幽灵股", 60.0, 70.0, 65.0, 65.0, 1),
        ]
        result = PipelineResult(
            scores=scores,
            stock_infos={},
            valuation_data={},
            prosperity_scores={},
            distress_signals={},
            financial_data={},
        )
        saved = save_all_reports(result, str(tmp_path))
        assert saved == []

    def test_fifth_chapter_present(
        self,
        sample_pipeline_result,
        tmp_path,
    ):
        saved = save_all_reports(sample_pipeline_result, str(tmp_path))
        report_path = [p for p in saved if "深度研报" in p][0]
        content = open(report_path, encoding="utf-8").read()
        assert "## 5. 戴维斯双击评分" in content
        assert "第1名" in content
