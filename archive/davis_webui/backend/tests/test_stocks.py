from fastapi.testclient import TestClient


def test_get_stock_detail(client: TestClient, mock_task):
    response = client.get(f"/api/stocks/{mock_task}/000001.SZ")
    assert response.status_code == 200
    body = response.json()
    assert body["stock_info"]["ts_code"] == "000001.SZ"
    assert body["stock_info"]["name"] == "平安银行"
    assert body["davis_score"]["final_score"] == 65.3
    assert body["prosperity_detail"]["composite_score"] == 70.0
    assert body["distress_detail"]["total_score"] == 59.0
    assert "revenue" in body["financial_summary"]


def test_get_stock_detail_not_found(client: TestClient, mock_task):
    response = client.get(f"/api/stocks/{mock_task}/INVALID.CODE")
    assert response.status_code == 404


def test_get_stock_detail_task_not_found(client: TestClient, clean_task_manager):
    response = client.get("/api/stocks/nonexistent/000001.SZ")
    assert response.status_code == 404


def test_get_report(client: TestClient, mock_task, monkeypatch):
    import davis_analyzer.report_generator

    monkeypatch.setattr(
        davis_analyzer.report_generator,
        "generate_stock_report",
        lambda **kw: "# Mock Report\n\nContent here.",
    )

    response = client.get(f"/api/reports/{mock_task}/000001.SZ")
    assert response.status_code == 200
    body = response.json()
    assert body["ts_code"] == "000001.SZ"
    assert body["name"] == "平安银行"
    assert "Mock Report" in body["markdown_content"]


def test_get_report_not_found(client: TestClient, mock_task):
    response = client.get(f"/api/reports/{mock_task}/INVALID.CODE")
    assert response.status_code == 404
