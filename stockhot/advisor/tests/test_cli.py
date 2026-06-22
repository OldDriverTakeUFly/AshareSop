"""TDD tests for advisor CLI — ask/daily/watchlist subcommands."""

from __future__ import annotations

import json

import pytest

from stockhot.advisor import cli
from stockhot.advisor.recommendation_engine import Recommendation


@pytest.fixture
def mock_rec() -> Recommendation:
    return Recommendation(
        code="000001",
        recommendation_type="build",
        action="buy",
        confidence="HIGH",
        entry_zone=(10.0, 10.5),
        stop_loss=9.5,
        target=12.0,
        reasoning="Strong momentum",
        prompt_version="v1",
    )


@pytest.fixture
def mock_rec_none() -> Recommendation:
    return Recommendation(
        code="999999",
        recommendation_type="none",
        action="NO_ACTION",
        confidence="LOW",
        reasoning="No actionable signal",
    )


# ── ask ────


class TestAsk:
    def test_ask_outputs_json(self, monkeypatch, capsys, mock_rec):
        monkeypatch.setattr(cli, "run_for_stock", lambda *a, **kw: mock_rec)
        monkeypatch.setattr(cli, "_get_holding_for_code", lambda code: None)

        exit_code = cli.main(["ask", "000001"])
        captured = capsys.readouterr()

        assert exit_code == 0
        data = json.loads(captured.out.strip())
        assert data["code"] == "000001"
        assert data["action"] == "buy"
        assert data["confidence"] == "HIGH"
        assert data["entry_zone"] == [10.0, 10.5]

    def test_ask_with_force(self, monkeypatch, capsys, mock_rec):
        captured: dict = {}

        def mock_run(*args, **kwargs):
            captured.update(kwargs)
            return mock_rec

        monkeypatch.setattr(cli, "run_for_stock", mock_run)
        monkeypatch.setattr(cli, "_get_holding_for_code", lambda code: None)

        exit_code = cli.main(["ask", "000001", "--force"])
        assert exit_code == 0
        assert captured["force"] is True

    def test_ask_with_date(self, monkeypatch, capsys, mock_rec):
        captured: dict = {}

        def mock_run(*args, **kwargs):
            captured["args"] = args
            captured.update(kwargs)
            return mock_rec

        monkeypatch.setattr(cli, "run_for_stock", mock_run)
        monkeypatch.setattr(cli, "_get_holding_for_code", lambda code: None)

        exit_code = cli.main(["ask", "000001", "--date", "2026-06-15"])
        assert exit_code == 0
        assert captured["args"][1] == "2026-06-15"

    def test_ask_passes_holding(self, monkeypatch, capsys, mock_rec):
        holding = {"code": "000001", "name": "平安银行"}
        captured: dict = {}

        def mock_run(*args, **kwargs):
            captured.update(kwargs)
            return mock_rec

        monkeypatch.setattr(cli, "run_for_stock", mock_run)
        monkeypatch.setattr(cli, "_get_holding_for_code", lambda code: holding)

        cli.main(["ask", "000001"])
        assert captured["holding"] == holding

    def test_ask_error_returns_1(self, monkeypatch, capsys):
        def mock_run(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(cli, "run_for_stock", mock_run)
        monkeypatch.setattr(cli, "_get_holding_for_code", lambda code: None)

        exit_code = cli.main(["ask", "000001"])
        assert exit_code == 1
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert "error" in data
        assert "boom" in data["error"]

    def test_ask_entry_zone_tuple_to_list(self, monkeypatch, capsys, mock_rec):
        monkeypatch.setattr(cli, "run_for_stock", lambda *a, **kw: mock_rec)
        monkeypatch.setattr(cli, "_get_holding_for_code", lambda code: None)

        cli.main(["ask", "000001"])
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert isinstance(data["entry_zone"], list)


# ── daily ────


class TestDaily:
    def test_daily_batch(self, monkeypatch, capsys, mock_rec):
        holdings = [{"code": "000001"}, {"code": "000002"}]
        watchlist = [{"code": "600519"}]

        monkeypatch.setattr(cli, "_get_active_holdings", lambda: holdings)
        monkeypatch.setattr(cli, "_get_watchlist", lambda: watchlist)
        monkeypatch.setattr(cli, "run_for_stock", lambda *a, **kw: mock_rec)
        monkeypatch.setattr(cli, "_try_telegram_push", lambda recs: None)

        exit_code = cli.main(["daily", "--no-telegram"])
        captured = capsys.readouterr()

        assert exit_code == 0
        assert "000001" in captured.out
        assert "000002" in captured.out
        assert "600519" in captured.out
        assert "完成" in captured.out

    def test_daily_max_cutoff(self, monkeypatch, capsys, mock_rec):
        holdings = [{"code": f"30000{i}"} for i in range(25)]

        processed_codes: list[str] = []

        def mock_run(*args, **kwargs):
            processed_codes.append(args[0])
            return mock_rec

        monkeypatch.setattr(cli, "_get_active_holdings", lambda: holdings)
        monkeypatch.setattr(cli, "_get_watchlist", lambda: [])
        monkeypatch.setattr(cli, "run_for_stock", mock_run)
        monkeypatch.setattr(cli, "_try_telegram_push", lambda recs: None)

        exit_code = cli.main(["daily", "--no-telegram"])
        captured = capsys.readouterr()

        assert exit_code == 0
        assert len(processed_codes) == cli.MAX_STOCKS_PER_DAILY_RUN
        assert "上限" in captured.err or "上限" in captured.out

    def test_daily_unique_codes(self, monkeypatch, capsys, mock_rec):
        holdings = [{"code": "000001"}]
        watchlist = [{"code": "000001"}, {"code": "600519"}]

        processed_codes: list[str] = []

        def mock_run(*args, **kwargs):
            processed_codes.append(args[0])
            return mock_rec

        monkeypatch.setattr(cli, "_get_active_holdings", lambda: holdings)
        monkeypatch.setattr(cli, "_get_watchlist", lambda: watchlist)
        monkeypatch.setattr(cli, "run_for_stock", mock_run)
        monkeypatch.setattr(cli, "_try_telegram_push", lambda recs: None)

        cli.main(["daily", "--no-telegram"])

        assert processed_codes.count("000001") == 1
        assert "600519" in processed_codes

    def test_daily_holding_passed_to_run(self, monkeypatch, capsys, mock_rec):
        holding = {"code": "000001", "name": "test"}
        holdings = [holding]

        captured: dict = {}

        def mock_run(*args, **kwargs):
            captured.update(kwargs)
            return mock_rec

        monkeypatch.setattr(cli, "_get_active_holdings", lambda: holdings)
        monkeypatch.setattr(cli, "_get_watchlist", lambda: [])
        monkeypatch.setattr(cli, "run_for_stock", mock_run)
        monkeypatch.setattr(cli, "_try_telegram_push", lambda recs: None)

        cli.main(["daily", "--no-telegram"])
        assert captured.get("holding", {}).get("code") == "000001"

    def test_daily_watchlist_passes_none_holding(self, monkeypatch, capsys, mock_rec):
        holdings = []
        watchlist = [{"code": "600519"}]

        captured_holding = []

        def mock_run(*args, **kwargs):
            captured_holding.append(kwargs.get("holding"))
            return mock_rec

        monkeypatch.setattr(cli, "_get_active_holdings", lambda: holdings)
        monkeypatch.setattr(cli, "_get_watchlist", lambda: watchlist)
        monkeypatch.setattr(cli, "run_for_stock", mock_run)
        monkeypatch.setattr(cli, "_try_telegram_push", lambda recs: None)

        cli.main(["daily", "--no-telegram"])
        assert captured_holding[0] is None

    def test_daily_single_stock_error_doesnt_stop_batch(self, monkeypatch, capsys, mock_rec):
        holdings = [{"code": "000001"}, {"code": "000002"}, {"code": "000003"}]
        call_count = [0]

        def mock_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("fail on second")
            return mock_rec

        monkeypatch.setattr(cli, "_get_active_holdings", lambda: holdings)
        monkeypatch.setattr(cli, "_get_watchlist", lambda: [])
        monkeypatch.setattr(cli, "run_for_stock", mock_run)
        monkeypatch.setattr(cli, "_try_telegram_push", lambda recs: None)

        exit_code = cli.main(["daily", "--no-telegram"])
        assert exit_code == 0
        assert call_count[0] == 3

    def test_daily_summary_counts(self, monkeypatch, capsys, mock_rec, mock_rec_none):
        holdings = [{"code": "000001"}, {"code": "000002"}]

        def mock_run(*args, **kwargs):
            code = args[0]
            return mock_rec if code == "000001" else mock_rec_none

        monkeypatch.setattr(cli, "_get_active_holdings", lambda: holdings)
        monkeypatch.setattr(cli, "_get_watchlist", lambda: [])
        monkeypatch.setattr(cli, "run_for_stock", mock_run)
        monkeypatch.setattr(cli, "_try_telegram_push", lambda recs: None)

        cli.main(["daily", "--no-telegram"])
        captured = capsys.readouterr()
        assert "2 只股票" in captured.out
        assert "1 条建议生成" in captured.out
        assert "1 条跳过" in captured.out

    def test_daily_with_date(self, monkeypatch, capsys, mock_rec):
        captured: dict = {}

        def mock_run(*args, **kwargs):
            captured["args"] = args
            return mock_rec

        monkeypatch.setattr(cli, "_get_active_holdings", lambda: [{"code": "000001"}])
        monkeypatch.setattr(cli, "_get_watchlist", lambda: [])
        monkeypatch.setattr(cli, "run_for_stock", mock_run)
        monkeypatch.setattr(cli, "_try_telegram_push", lambda recs: None)

        cli.main(["daily", "--date", "2026-06-15", "--no-telegram"])
        assert captured["args"][1] == "2026-06-15"

    def test_daily_with_force(self, monkeypatch, capsys, mock_rec):
        captured: dict = {}

        def mock_run(*args, **kwargs):
            captured.update(kwargs)
            return mock_rec

        monkeypatch.setattr(cli, "_get_active_holdings", lambda: [{"code": "000001"}])
        monkeypatch.setattr(cli, "_get_watchlist", lambda: [])
        monkeypatch.setattr(cli, "run_for_stock", mock_run)
        monkeypatch.setattr(cli, "_try_telegram_push", lambda recs: None)

        cli.main(["daily", "--force", "--no-telegram"])
        assert captured["force"] is True


# ── Telegram push ────


class TestTelegramPush:
    def test_no_telegram_flag_skips_push(self, monkeypatch, capsys, mock_rec):
        push_called: list = []
        monkeypatch.setattr(cli, "_try_telegram_push", lambda recs: push_called.append(recs))
        monkeypatch.setattr(cli, "_get_active_holdings", lambda: [{"code": "000001"}])
        monkeypatch.setattr(cli, "_get_watchlist", lambda: [])
        monkeypatch.setattr(cli, "run_for_stock", lambda *a, **kw: mock_rec)

        cli.main(["daily", "--no-telegram"])
        assert len(push_called) == 0

    def test_telegram_push_called_without_flag(self, monkeypatch, capsys, mock_rec):
        push_called: list = []
        monkeypatch.setattr(cli, "_try_telegram_push", lambda recs: push_called.append(recs))
        monkeypatch.setattr(cli, "_get_active_holdings", lambda: [{"code": "000001"}])
        monkeypatch.setattr(cli, "_get_watchlist", lambda: [])
        monkeypatch.setattr(cli, "run_for_stock", lambda *a, **kw: mock_rec)

        cli.main(["daily"])
        assert len(push_called) == 1

    def test_try_telegram_push_env_error_skips(self, monkeypatch):
        from stockhot.notification import telegram_bot

        def raise_env_error():
            raise EnvironmentError("not configured")

        monkeypatch.setattr(telegram_bot, "get_telegram_config", raise_env_error)

        recs = [
            Recommendation(
                code="000001",
                recommendation_type="build",
                action="buy",
                confidence="HIGH",
            )
        ]
        cli._try_telegram_push(recs)

    def test_try_telegram_push_sends_batch(self, monkeypatch):
        from stockhot.notification import telegram_bot

        monkeypatch.setattr(
            telegram_bot,
            "get_telegram_config",
            lambda: ("token", "chat", [123]),
        )

        sent_recs: list[dict] = []

        class MockNotifier:
            def __init__(self, *args, **kwargs):
                pass

            async def send_recommendations_batch(self, recs, max_messages=5):
                sent_recs.extend(recs)
                return []

        monkeypatch.setattr(telegram_bot, "TelegramNotifier", MockNotifier)

        recs = [
            Recommendation(
                code="000001",
                recommendation_type="build",
                action="buy",
                confidence="HIGH",
                reasoning="strong",
            )
        ]
        cli._try_telegram_push(recs)

        assert len(sent_recs) == 1
        assert sent_recs[0]["code"] == "000001"
        assert sent_recs[0]["reason"] == "strong"

    def test_try_telegram_push_skips_none_recs(self, monkeypatch):
        from stockhot.notification import telegram_bot

        monkeypatch.setattr(
            telegram_bot,
            "get_telegram_config",
            lambda: ("token", "chat", [123]),
        )

        sent_recs: list[dict] = []

        class MockNotifier:
            def __init__(self, *args, **kwargs):
                pass

            async def send_recommendations_batch(self, recs, max_messages=5):
                sent_recs.extend(recs)
                return []

        monkeypatch.setattr(telegram_bot, "TelegramNotifier", MockNotifier)

        recs = [
            Recommendation(
                code="000001",
                recommendation_type="none",
                action="NO_ACTION",
                confidence="LOW",
            ),
            Recommendation(
                code="000002",
                recommendation_type="build",
                action="buy",
                confidence="HIGH",
            ),
        ]
        cli._try_telegram_push(recs)

        assert len(sent_recs) == 1
        assert sent_recs[0]["code"] == "000002"


# ── watchlist dispatch ────


class TestWatchlistDispatch:
    def test_watchlist_dispatch_calls_watchlist_cli(self, monkeypatch, capsys):
        called: list = []

        def mock_main():
            called.append(True)
            print("watchlist dispatched")

        monkeypatch.setattr(cli.watchlist_cli, "main", mock_main)

        exit_code = cli.main(["watchlist", "list"])
        assert exit_code == 0
        assert len(called) == 1
        captured = capsys.readouterr()
        assert "watchlist dispatched" in captured.out

    def test_watchlist_dispatch_passes_args(self, monkeypatch, capsys):
        import sys

        captured_argv: list = []

        def mock_main():
            captured_argv.extend(sys.argv)

        monkeypatch.setattr(cli.watchlist_cli, "main", mock_main)

        cli.main(["watchlist", "add", "600519", "--name", "茅台"])
        assert captured_argv[0] == "watchlist_cli"
        assert "add" in captured_argv
        assert "600519" in captured_argv
        assert "茅台" in captured_argv

    def test_watchlist_no_args_dispatches_empty(self, monkeypatch, capsys):
        called: list = []
        monkeypatch.setattr(cli.watchlist_cli, "main", lambda: called.append(True))

        exit_code = cli.main(["watchlist"])
        assert exit_code == 0
        assert len(called) == 1


# ── no command ────


class TestNoCommand:
    def test_no_command_shows_help(self, capsys):
        exit_code = cli.main([])
        captured = capsys.readouterr()
        assert exit_code == 0
        assert "usage" in captured.out.lower()
