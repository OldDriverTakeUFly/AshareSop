"""Schema validation tests for StockHot-CN API models."""

from stockhot.api.schemas import (
    AvailableDates,
    Broker,
    BrokenStock,
    ConsecutiveBoard,
    DragonTigerResponse,
    FundFlowResponse,
    FundFlowTrend,
    HealthStatus,
    HotMoney,
    Institutional,
    LhbDetail,
    LimitDownStock,
    LimitUpAnalysis,
    LimitUpResponse,
    LimitUpStock,
    MarketFundFlow,
    RiskAlertData,
    RiskAlertResponse,
    SealStrength,
    SectorCorrelation,
    SectorFundFlow,
    StStock,
)


class TestLimitUpSchemas:
    def test_limit_up_stock(self):
        stock = LimitUpStock(
            code="000001",
            name="平安银行",
            change_pct=10.0,
            seal_amount=5_000_0000,
            max_board=3.0,
            consecutive_boards=2.0,
            sector="银行",
            broken_count=0.0,
            first_seal_time="09:30:00",
            last_seal_time="14:55:00",
            turnover_rate=8.5,
        )
        assert stock.code == "000001"
        assert stock.change_pct == 10.0

    def test_broken_stock(self):
        stock = BrokenStock(
            code="000002",
            name="万科A",
            change_pct=5.2,
            broken_count=3.0,
            sector="房地产",
        )
        assert stock.broken_count == 3.0

    def test_limit_down_stock(self):
        stock = LimitDownStock(
            code="000003",
            name="测试",
            change_pct=-10.0,
            sector="其他",
        )
        assert stock.change_pct == -10.0

    def test_consecutive_board(self):
        board = ConsecutiveBoard(
            board_count=5,
            stocks=[{"code": "000001", "name": "平安银行"}],
        )
        assert board.board_count == 5

    def test_sector_correlation(self):
        corr = SectorCorrelation(name="半导体", count=8, stocks=["股票A", "股票B"])
        assert corr.count == 8

    def test_seal_strength(self):
        seal = SealStrength(
            code="000001", name="测试", seal_amount=1e8, broken_count=0.0, score=1e8
        )
        assert seal.score == 1e8

    def test_limit_up_analysis(self):
        analysis = LimitUpAnalysis(
            consecutive_boards=[],
            sector_correlation=[],
            seal_strength_ranking=[],
            summary="涨停 10 只",
        )
        assert analysis.summary == "涨停 10 只"

    def test_limit_up_response_full(self):
        resp = LimitUpResponse(
            date="2026-05-14",
            status="success",
            limit_up_pool=[],
            broken_pool=[],
            limit_down_pool=[],
            analysis=LimitUpAnalysis(
                consecutive_boards=[],
                sector_correlation=[],
                seal_strength_ranking=[],
                summary="测试",
            ),
        )
        assert resp.analysis is not None
        assert resp.analysis.summary == "测试"

    def test_limit_up_response_no_analysis(self):
        resp = LimitUpResponse(
            date="2026-05-14",
            status="success",
            limit_up_pool=[],
            broken_pool=[],
            limit_down_pool=[],
        )
        assert resp.analysis is None


class TestDragonTigerSchemas:
    def test_lhb_detail(self):
        detail = LhbDetail(
            code="000001",
            name="平安银行",
            reason="涨幅偏离值达7%",
            close_price=15.5,
            change_pct=10.0,
            net_buy_amount=1.2e8,
            buy_amount=3.0e8,
            sell_amount=1.8e8,
            list_date="20260514",
        )
        assert detail.reason == "涨幅偏离值达7%"

    def test_institutional(self):
        inst = Institutional(
            inst_code="机构1",
            inst_name="机构专用",
            buy_amount=5e8,
            sell_amount=2e8,
            net_amount=3e8,
        )
        assert inst.inst_name == "机构专用"

    def test_broker(self):
        broker = Broker(
            broker_name="东方财富证券拉萨团结路",
            buy_amount=1e8,
            sell_amount=0.5e8,
            net_amount=0.5e8,
        )
        assert broker.broker_name.startswith("东方财富")

    def test_hot_money(self):
        hm = HotMoney(
            broker="某某营业部",
            buy_targets=["000001"],
            sell_targets=["000002"],
            net_direction="net_buy",
        )
        assert hm.net_direction == "net_buy"

    def test_dragon_tiger_response(self):
        resp = DragonTigerResponse(
            date="2026-05-14",
            status="success",
            detail=[],
            institutional=[],
            brokers=[],
            hot_money=[],
            summary="龙虎榜上榜股票数: 5",
        )
        assert resp.status == "success"


class TestFundFlowSchemas:
    def test_market_fund_flow(self):
        mf = MarketFundFlow(
            date="2026-05-14",
            main_net=50.5,
            main_pct=0.012,
            huge_net=30.0,
            large_net=20.5,
            medium_net=-15.0,
            small_net=-35.5,
        )
        assert mf.main_net == 50.5

    def test_sector_fund_flow(self):
        sf = SectorFundFlow(
            name="半导体",
            change_pct=2.5,
            main_net=10.0,
            main_pct=0.05,
            huge_net=5.0,
            large_net=5.0,
            medium_net=-2.0,
            small_net=-8.0,
        )
        assert sf.name == "半导体"

    def test_fund_flow_trend(self):
        trend = FundFlowTrend(
            direction="持续流入",
            momentum="加速",
            large_vs_retail_divergence=True,
            lookback_rows=5,
            avg_main_net=12.34,
        )
        assert trend.large_vs_retail_divergence is True

    def test_fund_flow_response_with_trend(self):
        resp = FundFlowResponse(
            date="2026-05-14",
            status="success",
            market_flow=[],
            sector_flow=[],
            trend=FundFlowTrend(
                direction="震荡",
                momentum="稳定",
                large_vs_retail_divergence=False,
                lookback_rows=3,
                avg_main_net=-5.0,
            ),
            summary="暂无市场资金流向数据。",
        )
        assert resp.trend is not None
        assert resp.trend.direction == "震荡"

    def test_fund_flow_response_no_trend(self):
        resp = FundFlowResponse(
            date="2026-05-14",
            status="success",
            market_flow=[],
            sector_flow=[],
            summary="暂无数据。",
        )
        assert resp.trend is None


class TestRiskAlertSchemas:
    def test_st_stock(self):
        stock = StStock(代码="000001", 名称="ST某某", 最新价=5.5, 涨跌幅=-5.0)
        assert stock.代码 == "000001"

    def test_risk_alert_data(self):
        data = RiskAlertData(
            st_stocks=[StStock(代码="000001", 名称="ST某某", 最新价=5.5, 涨跌幅=-5.0)],
            suspended_stocks=[{"代码": "000002", "名称": "停牌股"}],
            abnormal_volatility=[],
            capital_flight=[],
            high_position_risks=[],
            summary="风险提示: 共检出 1 项风险信号。",
        )
        assert len(data.st_stocks) == 1

    def test_risk_alert_response(self):
        resp = RiskAlertResponse(
            date="2026-05-14",
            status="success",
            data=RiskAlertData(
                st_stocks=[],
                suspended_stocks=[],
                abnormal_volatility=[],
                capital_flight=[],
                high_position_risks=[],
                summary="风险提示: 当前未检出显著风险信号。",
            ),
        )
        assert resp.data.summary.startswith("风险提示")


class TestUtilitySchemas:
    def test_available_dates(self):
        ad = AvailableDates(dates=["2026-05-14", "2026-05-13"])
        assert len(ad.dates) == 2

    def test_health_status(self):
        hs = HealthStatus(
            status="ok",
            db_path="/data/stockhot.db",
            latest_dates={"limit_up": "2026-05-14"},
        )
        assert hs.latest_dates["limit_up"] == "2026-05-14"


class TestSchemaFromDict:
    """Verify schemas accept real-world dict data via model_validate."""

    def test_limit_up_from_real_data(self):
        raw = {
            "date": "2026-05-14",
            "status": "success",
            "limit_up_pool": [
                {
                    "code": "000001",
                    "name": "平安银行",
                    "change_pct": 10.0,
                    "seal_amount": 50000000.0,
                    "max_board": 3.0,
                    "consecutive_boards": 2.0,
                    "sector": "银行",
                    "broken_count": 0.0,
                    "first_seal_time": "09:30:00",
                    "last_seal_time": "14:55:00",
                    "turnover_rate": 8.5,
                }
            ],
            "broken_pool": [],
            "limit_down_pool": [],
        }
        resp = LimitUpResponse.model_validate(raw)
        assert len(resp.limit_up_pool) == 1
        assert resp.limit_up_pool[0].name == "平安银行"

    def test_fund_flow_from_real_data(self):
        raw = {
            "date": "2026-05-14",
            "status": "success",
            "market_flow": [
                {
                    "date": "2026-05-14",
                    "main_net": 50.5,
                    "main_pct": 0.012,
                    "huge_net": 30.0,
                    "large_net": 20.5,
                    "medium_net": -15.0,
                    "small_net": -35.5,
                }
            ],
            "sector_flow": [],
            "trend": {
                "direction": "持续流入",
                "momentum": "加速",
                "large_vs_retail_divergence": True,
                "lookback_rows": 5,
                "avg_main_net": 12.34,
            },
            "summary": "最近一日主力净流入50.50亿。",
        }
        resp = FundFlowResponse.model_validate(raw)
        assert resp.trend is not None
        assert resp.trend.avg_main_net == 12.34
