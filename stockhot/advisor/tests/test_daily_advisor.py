"""TDD tests for daily advisor orchestration script."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from stockhot.invest_sop.scripts.run_daily_advisor import (
    main,
    run_advisor,
    run_report,
)


# ── run_advisor ────


class TestRunAdvisor:
    @patch("stockhot.invest_sop.scripts.run_daily_advisor.subprocess.run")
    def test_calls_subprocess_with_correct_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        result = run_advisor("2026-06-22")

        assert result is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "-m" in cmd
        assert "stockhot.advisor" in cmd
        assert "daily" in cmd
        assert "--date" in cmd
        assert "2026-06-22" in cmd

    @patch("stockhot.invest_sop.scripts.run_daily_advisor.subprocess.run")
    def test_returns_false_on_nonzero_exit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)

        result = run_advisor("2026-06-22")

        assert result is False

    @patch("stockhot.invest_sop.scripts.run_daily_advisor.subprocess.run")
    def test_uses_venv_python(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        run_advisor("2026-06-22")

        cmd = mock_run.call_args[0][0]
        assert ".venv" in cmd[0]
        assert "python" in cmd[0]


# ── run_report ────


class TestRunReport:
    @patch("stockhot.invest_sop.scripts.run_daily_advisor.subprocess.run")
    def test_calls_subprocess_with_report_script(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        result = run_report("2026-06-22")

        assert result is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "generate_premarket_report.py" in cmd[1]
        assert "--date" in cmd
        assert "2026-06-22" in cmd

    @patch("stockhot.invest_sop.scripts.run_daily_advisor.subprocess.run")
    def test_returns_false_on_nonzero_exit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)

        result = run_report("2026-06-22")

        assert result is False


# ── main ────


class TestMain:
    @patch("stockhot.invest_sop.scripts.run_daily_advisor.run_report")
    @patch("stockhot.invest_sop.scripts.run_daily_advisor.run_advisor")
    def test_with_date_calls_both(self, mock_advisor, mock_report, capsys):
        mock_advisor.return_value = True
        mock_report.return_value = True

        exit_code = main(["--date", "2026-06-22"])

        assert exit_code == 0
        mock_advisor.assert_called_once_with("2026-06-22")
        mock_report.assert_called_once_with("2026-06-22")
        captured = capsys.readouterr()
        assert "successfully" in captured.out

    @patch("stockhot.invest_sop.scripts.run_daily_advisor.run_report")
    @patch("stockhot.invest_sop.scripts.run_daily_advisor.run_advisor")
    def test_without_date_uses_today(self, mock_advisor, mock_report):
        mock_advisor.return_value = True
        mock_report.return_value = True

        main([])

        today = date.today().isoformat()
        mock_advisor.assert_called_once_with(today)
        mock_report.assert_called_once_with(today)

    @patch("stockhot.invest_sop.scripts.run_daily_advisor.run_report")
    @patch("stockhot.invest_sop.scripts.run_daily_advisor.run_advisor")
    def test_advisor_failure_doesnt_block_report(
        self, mock_advisor, mock_report, capsys
    ):
        mock_advisor.return_value = False
        mock_report.return_value = True

        exit_code = main(["--date", "2026-06-22"])

        assert exit_code == 0
        mock_advisor.assert_called_once_with("2026-06-22")
        mock_report.assert_called_once_with("2026-06-22")
        captured = capsys.readouterr()
        assert "partial failures" in captured.out

    @patch("stockhot.invest_sop.scripts.run_daily_advisor.run_report")
    @patch("stockhot.invest_sop.scripts.run_daily_advisor.run_advisor")
    def test_both_fail_still_returns_zero(self, mock_advisor, mock_report, capsys):
        mock_advisor.return_value = False
        mock_report.return_value = False

        exit_code = main(["--date", "2026-06-22"])

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "partial failures" in captured.out

    @patch("stockhot.invest_sop.scripts.run_daily_advisor.run_report")
    @patch("stockhot.invest_sop.scripts.run_daily_advisor.run_advisor")
    def test_report_failure_logs_partial(self, mock_advisor, mock_report, capsys):
        mock_advisor.return_value = True
        mock_report.return_value = False

        exit_code = main(["--date", "2026-06-22"])

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "partial failures" in captured.out
