import pandas as pd
import stockhot.limit_up as lu


def _zt_df():
    return pd.DataFrame(
        {
            "代码": ["000001", "000002", "600123", "300456"],
            "名称": ["平安银行", "万科A", "兰花科创", "创意信息"],
            "涨跌幅": [10.0, 10.0, 10.0, 20.0],
            "封板资金": [5.0e8, 3.0e8, 1.0e8, 2.0e8],
            "最高板": [3, 2, 1, 4],
            "连板数": [3, 2, 1, 4],
            "所属行业": ["银行", "房地产", "煤炭", "计算机"],
            "炸板次数": [0, 1, 3, 0],
            "首次封板时间": ["09:30:00", "10:00:00", "14:00:00", "09:31:00"],
            "最后封板时间": ["09:30:00", "14:50:00", "14:55:00", "09:31:00"],
            "换手率": [2.5, 5.0, 8.0, 3.0],
        }
    )


def _broken_df():
    return pd.DataFrame(
        {
            "代码": ["000003"],
            "名称": ["某炸板股"],
            "涨跌幅": [5.2],
            "炸板次数": [2],
            "所属行业": ["电子"],
        }
    )


def _limit_down_df():
    return pd.DataFrame(
        {
            "代码": ["000004"],
            "名称": ["某跌停股"],
            "涨跌幅": [-10.0],
            "所属行业": ["房地产"],
        }
    )


def test_fetch_limit_up_pool_with_mocked_data(monkeypatch):
    monkeypatch.setattr(lu.ak, "stock_zt_pool_em", lambda date: _zt_df())
    result = lu.fetch_limit_up_pool("2026-05-12")
    assert len(result) == 4
    assert result[0]["code"] == "000001"
    assert result[0]["name"] == "平安银行"
    assert result[0]["change_pct"] == 10.0
    assert result[0]["seal_amount"] == 5.0e8
    assert result[0]["consecutive_boards"] == 3.0
    assert result[0]["sector"] == "银行"
    assert result[0]["broken_count"] == 0.0
    assert result[0]["turnover_rate"] == 2.5


def test_fetch_limit_up_pool_empty_on_non_trading_day(monkeypatch):
    monkeypatch.setattr(lu.ak, "stock_zt_pool_em", lambda date: pd.DataFrame())
    result = lu.fetch_limit_up_pool("2026-05-10")
    assert result == []


def test_analyze_seal_strength_sorts_by_score():
    pool = [
        {"code": "000001", "name": "A", "seal_amount": 100.0, "broken_count": 0.0},
        {"code": "000002", "name": "B", "seal_amount": 100.0, "broken_count": 2.0},
        {"code": "000003", "name": "C", "seal_amount": 50.0, "broken_count": 0.0},
    ]
    result = lu.analyze_seal_strength(pool)
    assert len(result) == 3
    assert result[0]["code"] == "000001"
    assert result[0]["score"] == 100.0
    assert result[1]["code"] == "000003"
    assert result[1]["score"] == 50.0
    assert result[2]["code"] == "000002"
    assert result[2]["score"] == 33.33


def test_find_consecutive_boards_groups_correctly():
    pool = [
        {"code": "000001", "name": "A", "consecutive_boards": 3},
        {"code": "000002", "name": "B", "consecutive_boards": 2},
        {"code": "000003", "name": "C", "consecutive_boards": 3},
        {"code": "000004", "name": "D", "consecutive_boards": 1},
    ]
    result = lu.find_consecutive_boards(pool)
    assert len(result) == 2
    assert result[0]["board_count"] == 3
    assert len(result[0]["stocks"]) == 2
    assert result[1]["board_count"] == 2
    assert len(result[1]["stocks"]) == 1


def test_analyze_sector_correlation_counts_sectors():
    pool = [
        {"name": "A", "sector": "银行"},
        {"name": "B", "sector": "银行"},
        {"name": "C", "sector": "电子"},
        {"name": "D", "sector": "电子"},
        {"name": "E", "sector": "电子"},
        {"name": "F", "sector": "未知"},
    ]
    result = lu.analyze_sector_correlation(pool)
    assert len(result) == 3
    assert result[0]["name"] == "电子"
    assert result[0]["count"] == 3
    assert result[1]["name"] == "银行"
    assert result[1]["count"] == 2
    assert result[2]["name"] == "未知"
    assert result[2]["count"] == 1


def test_generate_summary_includes_all_metrics():
    pool = [{"code": "000001", "name": "A"}]
    broken = [{"code": "000002", "name": "B"}]
    limit_down = [{"code": "000003", "name": "C"}, {"code": "000004", "name": "D"}]
    consecutive = [{"board_count": 5, "stocks": [{"name": "龙头股"}, {"name": "跟风股"}]}]
    sector_corr = [{"name": "芯片", "count": 8}]

    summary = lu.generate_summary(pool, broken, limit_down, consecutive, sector_corr)
    assert "涨停 1 只" in summary
    assert "炸板 1 只" in summary
    assert "跌停 2 只" in summary
    assert "最高连板: 5板" in summary
    assert "龙头股" in summary
    assert "板块联动: 芯片(8只涨停)" in summary


def test_run_limit_up_analysis_full_pipeline(monkeypatch):
    saved_daily = {}
    saved_analysis = {}

    monkeypatch.setattr(lu.ak, "stock_zt_pool_em", lambda date: _zt_df())
    monkeypatch.setattr(lu.ak, "stock_zt_pool_zbgc_em", lambda date: _broken_df())
    monkeypatch.setattr(lu.ak, "stock_zt_pool_dtgc_em", lambda date: _limit_down_df())
    monkeypatch.setattr(lu, "save_daily_data", lambda data: saved_daily.update(data))
    monkeypatch.setattr(
        lu,
        "save_analysis_result",
        lambda date, atype, result: saved_analysis.update(
            {"date": date, "type": atype, "result": result}
        ),
    )

    result = lu.run_limit_up_analysis("2026-05-12")

    assert result["status"] == "success"
    assert result["date"] == "2026-05-12"
    assert len(result["data"]["limit_up_pool"]) == 4
    assert len(result["data"]["broken_pool"]) == 1
    assert len(result["data"]["limit_down_pool"]) == 1
    assert "consecutive_boards" in result["data"]
    assert "sector_correlation" in result["data"]
    assert "seal_strength_ranking" in result["data"]
    assert "summary" in result["data"]

    assert saved_daily["date"] == "2026-05-12"
    assert saved_analysis["type"] == "limit_up_analysis"
    assert saved_analysis["date"] == "2026-05-12"


def test_run_limit_up_analysis_no_data(monkeypatch):
    monkeypatch.setattr(lu.ak, "stock_zt_pool_em", lambda date: pd.DataFrame())
    monkeypatch.setattr(lu.ak, "stock_zt_pool_zbgc_em", lambda date: pd.DataFrame())
    monkeypatch.setattr(lu.ak, "stock_zt_pool_dtgc_em", lambda date: pd.DataFrame())

    result = lu.run_limit_up_analysis("2026-05-10")
    assert result == {"date": "2026-05-10", "status": "no_data"}
