"""Integration tests for pipeline, CLI subcommands, and cross-module imports."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from davis_analyzer.cli import main as cli_main
from davis_analyzer.types import (
    DavisDoubleScore,
    DistressSignal,
    FinancialData,
    PipelineResult,
    ProsperityScore,
    RescoredResult,
    StockInfo,
)

# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_stock_info():
    return StockInfo(
        ts_code="000001.SZ",
        name="平安银行",
        industry="银行",
        list_status="L",
        is_cyclical=False,
    )


@pytest.fixture
def mock_pipeline_result(mock_stock_info):
    scores = [
        DavisDoubleScore(
            ts_code="000001.SZ",
            name="平安银行",
            valuation_score=65.0,
            prosperity_score=73.5,
            distress_score=69.0,
            final_score=69.2,
            rank=1,
            trend_score=72.0,
        ),
        DavisDoubleScore(
            ts_code="600036.SH",
            name="招商银行",
            valuation_score=55.0,
            prosperity_score=58.75,
            distress_score=44.5,
            final_score=53.0,
            rank=2,
            trend_score=50.0,
        ),
    ]
    stock_infos = {
        "000001.SZ": mock_stock_info,
        "600036.SH": StockInfo(
            ts_code="600036.SH",
            name="招商银行",
            industry="银行",
            list_status="L",
            is_cyclical=False,
        ),
    }
    valuation_data = {
        "000001.SZ": (65.0, 0.15, 0.25),
        "600036.SH": (55.0, 0.30, 0.40),
    }
    prosperity_scores = {
        "000001.SZ": ProsperityScore("000001.SZ", 78.0, 72.0, 68.0, 75.0, 73.5, 5.2),
        "600036.SH": ProsperityScore("600036.SH", 65.0, 60.0, 55.0, 50.0, 58.75, 2.1),
    }
    distress_signals = {
        "000001.SZ": DistressSignal("000001.SZ", 80.0, 70.0, 60.0, 69.0, {"eps_decline": True}),
        "600036.SH": DistressSignal("600036.SH", 50.0, 45.0, 40.0, 44.5, {}),
    }
    financial_data = {
        "000001.SZ": [
            FinancialData(
                "000001.SZ",
                "20240331",
                10000.0,
                3000.0,
                0.50,
                15.0,
                2000.0,
                5000.0,
                15000.0,
                12.0,
                18.0,
            )
        ],
        "600036.SH": [
            FinancialData(
                "600036.SH",
                "20240331",
                8000.0,
                2500.0,
                0.45,
                14.0,
                1800.0,
                4000.0,
                12000.0,
                8.0,
                10.0,
            )
        ],
    }
    trend_scores = {
        "000001.SZ": 72.0,
        "600036.SH": 50.0,
    }
    return PipelineResult(
        scores=scores,
        stock_infos=stock_infos,
        valuation_data=valuation_data,
        prosperity_scores=prosperity_scores,
        distress_signals=distress_signals,
        financial_data=financial_data,
        trend_scores=trend_scores,
    )


# ── CLI argument parsing tests ────────────────────────────────────────


class TestCLISubcommands:
    def test_cli_no_args_prints_help(self, capsys):
        with patch("sys.argv", ["davis_analyzer"]):
            cli_main()
        captured = capsys.readouterr()
        assert "davis_analyzer" in captured.out

    def test_cli_help_lists_all_subcommands(self, capsys):
        with patch("sys.argv", ["davis_analyzer", "--help"]):
            with pytest.raises(SystemExit):
                cli_main()
        captured = capsys.readouterr()
        assert "run" in captured.out
        assert "deep-research" in captured.out
        assert "rescore" in captured.out

    def test_cli_run_subcommand_exists(self, capsys):
        with patch("sys.argv", ["davis_analyzer", "run", "--help"]):
            with pytest.raises(SystemExit):
                cli_main()
        captured = capsys.readouterr()
        assert "--top" in captured.out
        assert "--output" in captured.out

    def test_cli_deep_research_subcommand_exists(self, capsys):
        with patch("sys.argv", ["davis_analyzer", "deep-research", "--help"]):
            with pytest.raises(SystemExit):
                cli_main()
        captured = capsys.readouterr()
        assert "--top" in captured.out
        assert "--checklist-dir" in captured.out

    def test_cli_rescore_subcommand_exists(self, capsys):
        with patch("sys.argv", ["davis_analyzer", "rescore", "--help"]):
            with pytest.raises(SystemExit):
                cli_main()
        captured = capsys.readouterr()
        assert "--checklist-dir" in captured.out

    def test_cli_run_dry_run_flag_removed(self, capsys):
        """The ``--dry-run`` flag was a no-op (printed a message, did nothing)
        and has been removed. argparse must now reject it."""
        with patch("sys.argv", ["davis_analyzer", "run", "--dry-run"]):
            with pytest.raises(SystemExit):
                cli_main()
        captured = capsys.readouterr()
        assert "unrecognized arguments: --dry-run" in captured.err


# ── PipelineResult structure tests ────────────────────────────────────


class TestPipelineResultStructure:
    def test_pipeline_result_has_all_expected_fields(self, mock_pipeline_result):
        assert hasattr(mock_pipeline_result, "scores")
        assert hasattr(mock_pipeline_result, "stock_infos")
        assert hasattr(mock_pipeline_result, "valuation_data")
        assert hasattr(mock_pipeline_result, "prosperity_scores")
        assert hasattr(mock_pipeline_result, "distress_signals")
        assert hasattr(mock_pipeline_result, "financial_data")
        assert hasattr(mock_pipeline_result, "trend_scores")

    def test_pipeline_result_scores_are_davis_double(self, mock_pipeline_result):
        for score in mock_pipeline_result.scores:
            assert isinstance(score, DavisDoubleScore)

    def test_pipeline_result_trend_scores_is_dict(self, mock_pipeline_result):
        assert isinstance(mock_pipeline_result.trend_scores, dict)

    def test_pipeline_result_trend_scores_contains_floats(self, mock_pipeline_result):
        for ts_code, score in mock_pipeline_result.trend_scores.items():
            assert isinstance(ts_code, str)
            assert isinstance(score, float)

    def test_pipeline_result_empty_creation(self):
        result = PipelineResult(
            scores=[],
            stock_infos={},
            valuation_data={},
            prosperity_scores={},
            distress_signals={},
            financial_data={},
        )
        assert result.scores == []
        assert result.trend_scores == {}

    def test_pipeline_result_trend_scores_defaults_to_empty(self):
        result = PipelineResult(
            scores=[],
            stock_infos={},
            valuation_data={},
            prosperity_scores={},
            distress_signals={},
            financial_data={},
        )
        assert result.trend_scores == {}


# ── DavisDoubleScore trend_score tests ────────────────────────────────


class TestDavisDoubleScoreTrendScore:
    def test_davis_score_has_trend_score(self):
        score = DavisDoubleScore(
            ts_code="000001.SZ",
            name="测试",
            valuation_score=60.0,
            prosperity_score=70.0,
            distress_score=65.0,
            final_score=65.0,
            rank=1,
        )
        assert hasattr(score, "trend_score")

    def test_davis_score_trend_score_defaults_zero(self):
        score = DavisDoubleScore(
            ts_code="000001.SZ",
            name="测试",
            valuation_score=60.0,
            prosperity_score=70.0,
            distress_score=65.0,
            final_score=65.0,
            rank=1,
        )
        assert score.trend_score == 0.0

    def test_davis_score_trend_score_can_be_set(self):
        score = DavisDoubleScore(
            ts_code="000001.SZ",
            name="测试",
            valuation_score=60.0,
            prosperity_score=70.0,
            distress_score=65.0,
            final_score=65.0,
            rank=1,
            trend_score=85.5,
        )
        assert score.trend_score == 85.5


# ── Cross-module import chain tests ───────────────────────────────────


class TestImportChain:
    def test_import_pipeline(self):
        from davis_analyzer.pipeline import run_screening_pipeline

        assert callable(run_screening_pipeline)

    def test_import_checklist_generator(self):
        from davis_analyzer.checklist_generator import (
            generate_batch_checklists,
            generate_checklist,
        )

        assert callable(generate_batch_checklists)
        assert callable(generate_checklist)

    def test_import_rescorer(self):
        from davis_analyzer.rescorer import batch_rescore, parse_checklist, rescore

        assert callable(batch_rescore)
        assert callable(parse_checklist)
        assert callable(rescore)

    def test_import_report_generator(self):
        from davis_analyzer.report_generator import (
            generate_stock_report,
            save_all_reports,
        )

        assert callable(generate_stock_report)
        assert callable(save_all_reports)

    def test_import_trend_module(self):
        from davis_analyzer import trend

        assert hasattr(trend, "calculate_trend_score")

    def test_import_scoring(self):
        from davis_analyzer.scoring import (
            calculate_davis_double_score,
            rank_stocks,
        )

        assert callable(calculate_davis_double_score)
        assert callable(rank_stocks)

    def test_rescored_result_type_exists(self):
        assert hasattr(RescoredResult, "__dataclass_fields__")
        fields = RescoredResult.__dataclass_fields__
        assert "adjusted_prosperity" in fields
        assert "adjusted_distress" in fields


# ── Checklist + Rescorer integration tests ────────────────────────────


class TestChecklistRescorerIntegration:
    def test_generate_then_parse_roundtrip(
        self,
        mock_pipeline_result,
        tmp_path,
    ):
        from davis_analyzer.checklist_generator import generate_batch_checklists
        from davis_analyzer.rescorer import batch_rescore

        saved = generate_batch_checklists(mock_pipeline_result, str(tmp_path), top_n=2)
        assert len(saved) == 2

        rescored = batch_rescore(mock_pipeline_result, str(tmp_path))
        assert len(rescored) == 2
        assert "000001.SZ" in rescored
        assert "600036.SH" in rescored

    def test_rescore_with_no_checklists_returns_empty(
        self,
        mock_pipeline_result,
        tmp_path,
    ):
        from davis_analyzer.rescorer import batch_rescore

        result = batch_rescore(mock_pipeline_result, str(tmp_path))
        assert result == {}

    def test_checklist_filename_format(
        self,
        mock_pipeline_result,
        tmp_path,
    ):
        from davis_analyzer.checklist_generator import generate_batch_checklists

        saved = generate_batch_checklists(mock_pipeline_result, str(tmp_path), top_n=1)
        assert len(saved) == 1
        assert "调研checklist" in saved[0]
        assert "000001.SZ" in saved[0]
