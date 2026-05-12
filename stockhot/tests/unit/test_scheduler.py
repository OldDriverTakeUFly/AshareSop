import stockhot.scheduler as scheduler


def test_run_daily_workflow_executes_steps_in_order(monkeypatch):
    calls = []

    monkeypatch.setattr(
        scheduler,
        "run_collection",
        lambda date: calls.append(("collection", date)) or {"stage": "collection"},
    )
    monkeypatch.setattr(
        scheduler,
        "run_analysis",
        lambda date: calls.append(("analysis", date)) or {"stage": "analysis"},
    )
    monkeypatch.setattr(
        scheduler,
        "run_limit_up_analysis",
        lambda date: calls.append(("limit_up", date)) or {"stage": "limit_up"},
    )
    monkeypatch.setattr(
        scheduler,
        "run_dragon_tiger_analysis",
        lambda date: calls.append(("dragon_tiger", date)) or {"stage": "dragon_tiger"},
    )
    monkeypatch.setattr(
        scheduler,
        "run_fund_flow_analysis",
        lambda date: calls.append(("fund_flow", date)) or {"stage": "fund_flow"},
    )
    monkeypatch.setattr(
        scheduler,
        "run_risk_alert_analysis",
        lambda date: calls.append(("risk_alert", date)) or {"stage": "risk_alert"},
    )
    monkeypatch.setattr(
        scheduler,
        "run_hotspot_discovery",
        lambda date: (
            calls.append(("hotspot_discovery", date))
            or {"stage": "hotspot_discovery", "lead_theme": "商业航天"}
        ),
    )
    monkeypatch.setattr(
        scheduler,
        "run_research_report",
        lambda date=None, theme=None: (
            calls.append(("theme_report", date, theme)) or {"stage": "theme_report", "theme": theme}
        ),
    )
    monkeypatch.setattr(
        scheduler,
        "run_generation",
        lambda date: calls.append(("generation", date)) or {"stage": "generation"},
    )
    monkeypatch.setattr(
        scheduler,
        "run_publish",
        lambda date: calls.append(("publish", date)) or {"stage": "publish"},
    )

    result = scheduler.run_daily_workflow("2026-04-24")

    assert calls == [
        ("collection", "2026-04-24"),
        ("analysis", "2026-04-24"),
        ("limit_up", "2026-04-24"),
        ("dragon_tiger", "2026-04-24"),
        ("fund_flow", "2026-04-24"),
        ("risk_alert", "2026-04-24"),
        ("hotspot_discovery", "2026-04-24"),
        ("theme_report", "2026-04-24", "商业航天"),
        ("generation", "2026-04-24"),
        ("publish", "2026-04-24"),
    ]
    assert result == {
        "collection": {"stage": "collection"},
        "analysis": {"stage": "analysis"},
        "limit_up": {"stage": "limit_up"},
        "dragon_tiger": {"stage": "dragon_tiger"},
        "fund_flow": {"stage": "fund_flow"},
        "risk_alert": {"stage": "risk_alert"},
        "hotspot_discovery": {"stage": "hotspot_discovery", "lead_theme": "商业航天"},
        "theme_report": {"stage": "theme_report", "theme": "商业航天"},
        "generation": {"stage": "generation"},
        "publish": {"stage": "publish"},
    }


def test_trigger_manual_initializes_database_before_running(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "init_database", lambda: calls.append("init_database"))
    monkeypatch.setattr(
        scheduler,
        "run_daily_workflow",
        lambda date=None: calls.append(("run_daily_workflow", date)),
    )

    scheduler.trigger_manual("2026-04-24")

    assert calls == ["init_database", ("run_daily_workflow", "2026-04-24")]
