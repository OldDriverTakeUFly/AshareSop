from fastapi.testclient import TestClient


def test_get_distress_heatmap(client: TestClient, mock_task):
    response = client.get(f"/api/distress/{mock_task}")
    assert response.status_code == 200
    body = response.json()
    assert len(body["stocks"]) == 3
    stock = body["stocks"][0]
    assert stock["ts_code"] == "000001.SZ"
    assert stock["name"] == "平安银行"
    assert "eps_decline" in stock["layer1_signals"]
    assert "balance_sheet" in stock["layer2_signals"]
    assert "revenue_inflection" in stock["layer3_signals"]
    assert stock["layer_scores"]["layer1"] == 70.0
    assert stock["total_score"] == 59.0


def test_distress_not_found(client: TestClient, clean_task_manager):
    response = client.get("/api/distress/nonexistent")
    assert response.status_code == 404
