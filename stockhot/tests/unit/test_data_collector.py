import stockhot.data_collector as dc


class _FakeClient:
    def __init__(self):
        self.calls = []

    def get_gainers(self, limit):
        self.calls.append(("gainers", limit))
        return [{"name": "A", "change_pct": 1.2}]

    def get_losers(self, limit):
        self.calls.append(("losers", limit))
        return [{"name": "B", "change_pct": -2.3}]

    def get_sectors(self, limit):
        self.calls.append(("sectors", limit))
        return [{"name": "电子", "change_pct": 3.4}]

    def get_fund_flow(self, limit):
        self.calls.append(("fund_flows", limit))
        return [{"name": "通信设备", "net_inflow": 12.3}]


def test_run_collection_aggregates_and_persists(monkeypatch):
    client = _FakeClient()
    saved = {}

    monkeypatch.setattr(dc, "_get_client", lambda: client)
    monkeypatch.setattr(dc, "save_daily_data", lambda data: saved.setdefault("data", data))

    result = dc.run_collection("2026-04-17")

    assert result == {
        "date": "2026-04-17",
        "status": "success",
        "counts": {"gainers": 1, "losers": 1, "sectors": 1, "fund_flows": 1},
    }
    assert saved["data"] == {
        "date": "2026-04-17",
        "gainers": [{"name": "A", "change_pct": 1.2}],
        "losers": [{"name": "B", "change_pct": -2.3}],
        "sectors": [{"name": "电子", "change_pct": 3.4}],
        "fund_flows": [{"name": "通信设备", "net_inflow": 12.3}],
    }
    assert client.calls == [
        ("gainers", dc.TOP_N_STOCKS),
        ("losers", dc.TOP_N_STOCKS),
        ("sectors", dc.TOP_N_SECTORS),
        ("fund_flows", dc.TOP_N_FUNDS),
    ]


def test_run_collection_uses_today_when_date_omitted(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(dc, "_get_client", lambda: client)
    monkeypatch.setattr(dc, "save_daily_data", lambda data: None)

    class _FakeNow:
        @staticmethod
        def strftime(fmt: str) -> str:
            return "2026-04-19"

    class _FakeDateTime:
        @staticmethod
        def now():
            return _FakeNow()

    monkeypatch.setattr(dc, "datetime", _FakeDateTime)

    result = dc.run_collection()

    assert result["date"] == "2026-04-19"


def test_public_getters_delegate_to_current_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(dc, "_get_client", lambda: client)

    assert dc.get_gainers(3) == [{"name": "A", "change_pct": 1.2}]
    assert dc.get_losers(4) == [{"name": "B", "change_pct": -2.3}]
    assert dc.get_sector_performance() == [{"name": "电子", "change_pct": 3.4}]
    assert dc.get_fund_flow() == [{"name": "通信设备", "net_inflow": 12.3}]
