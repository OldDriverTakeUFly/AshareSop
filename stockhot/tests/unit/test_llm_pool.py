"""llm_pool 单测:池解析/候选展开/错误分类/冷却台账/向后兼容合成(纯离线,零网络)。"""
import os
from datetime import datetime, timedelta

import pytest

from stockhot import llm_pool


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """每个用例:清 env 池变量 + 重置进程内冷却/死池台账。"""
    for k in list(os.environ):
        if k.startswith("LLM_POOL_"):
            monkeypatch.delenv(k)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    llm_pool._pool_cooldown.clear()
    llm_pool._dead_pools.clear()
    llm_pool._last_error.clear()


class TestSynthesis:
    def test_synth_from_legacy_llm_env(self, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "https://x/api/coding/paas/v4")
        monkeypatch.setenv("LLM_API_KEY", "k1")
        monkeypatch.setenv("LLM_MODEL", "glm-4-flash")
        cands = llm_pool.candidates()
        # 合成池:配置模型在前,glm 系已知 fallback 补后(去重)
        assert [c.model for c in cands] == ["glm-4-flash", "glm-5.3-flash"]
        assert cands[0].pool == "1" and cands[0].api_key == "k1"

    def test_synth_empty_when_no_env(self):
        assert llm_pool.candidates() == []


class TestPoolParse:
    def _two_pools(self, monkeypatch):
        monkeypatch.setenv("LLM_POOL_1", "https://coding/|keyA|glm-5.3-flash,glm-4-flash")
        monkeypatch.setenv("LLM_POOL_2", "https://std/|keyB|glm-4-flash")

    def test_order_and_fields(self, monkeypatch):
        self._two_pools(monkeypatch)
        cands = llm_pool.candidates()
        assert [(c.pool, c.base_url, c.api_key, c.model) for c in cands] == [
            ("1", "https://coding", "keyA", "glm-5.3-flash"),
            ("1", "https://coding", "keyA", "glm-4-flash"),
            ("2", "https://std", "keyB", "glm-4-flash"),
        ]

    def test_gap_ends_pool_list(self, monkeypatch):
        self._two_pools(monkeypatch)
        monkeypatch.setenv("LLM_POOL_4", "https://std2/|keyC|glm-4-flash")  # 断号:3 缺
        assert all(c.pool in ("1", "2") for c in llm_pool.candidates())


class TestFilters:
    def test_cooldown_pool_skipped(self, monkeypatch):
        self._two = monkeypatch.setenv
        monkeypatch.setenv("LLM_POOL_1", "https://coding/|keyA|glm-5.3-flash")
        monkeypatch.setenv("LLM_POOL_2", "https://std/|keyB|glm-4-flash")
        c1 = llm_pool.candidates()[0]
        llm_pool.note(c1, "quota", "您已达到每周/每月使用上限，您的限额将在 2099-01-01 00:00:00 重置。")
        models = [c.model for c in llm_pool.candidates()]
        assert models == ["glm-4-flash"] and all(c.pool != "1" for c in llm_pool.candidates())

    def test_auth_dead_pool_skipped(self, monkeypatch):
        monkeypatch.setenv("LLM_POOL_1", "https://coding/|keyA|glm-5.3-flash")
        monkeypatch.setenv("LLM_POOL_2", "https://std/|keyB|glm-4-flash")
        llm_pool.note(llm_pool.candidates()[0], "auth")
        assert [c.pool for c in llm_pool.candidates()] == ["2"]

    def test_vision_filter_and_suffix_override(self, monkeypatch):
        monkeypatch.setenv("LLM_POOL_1",
                           "https://coding/|keyA|glm-5.3-flash,glm-4-flash:novision,glm-4v-plus:vision")
        text = [c.model for c in llm_pool.candidates(vision=False)]
        vis = [c.model for c in llm_pool.candidates(vision=True)]
        assert text == ["glm-5.3-flash", "glm-4-flash", "glm-4v-plus"]
        assert vis == ["glm-5.3-flash", "glm-4v-plus"]  # 4-flash 能力表外+显式 novision 双杀


class TestClassify:
    @pytest.mark.parametrize("status,msg,want", [
        (429, "code 1310 限额将在 2026-10-30 15:59:11 重置", "quota"),
        (429, "code 1302 已达到速率限制", "rate"),
        (400, "model `glm-9x` does not exist", "model"),
        (401, "Invalid API key", "auth"),
        (None, "ConnectTimeout", "network"),
        (200, "", "ok"),
    ])
    def test_matrix(self, status, msg, want):
        assert llm_pool.classify(status, msg) == want


class TestNote:
    def test_quota_parses_reset_time(self, monkeypatch):
        monkeypatch.setenv("LLM_POOL_1", "https://coding/|keyA|glm-5.3-flash")
        cand = llm_pool.candidates()[0]
        llm_pool.note(cand, "quota", "您的限额将在 2099-01-01 08:00:00 重置。")
        assert llm_pool._pool_cooldown["1"] == datetime(2099, 1, 1, 8, 0, 0)

    def test_quota_without_timestamp_falls_back_30min(self, monkeypatch):
        monkeypatch.setenv("LLM_POOL_1", "https://coding/|keyA|glm-5.3-flash")
        cand = llm_pool.candidates()[0]
        before = datetime.now()
        llm_pool.note(cand, "quota", "1310 上限")
        cd = llm_pool._pool_cooldown["1"]
        assert before + timedelta(minutes=29) <= cd <= before + timedelta(minutes=31)

    def test_rate_and_model_do_not_cooldown(self, monkeypatch):
        monkeypatch.setenv("LLM_POOL_1", "https://coding/|keyA|glm-5.3-flash")
        cand = llm_pool.candidates()[0]
        llm_pool.note(cand, "rate")
        llm_pool.note(cand, "model")
        assert not llm_pool._pool_cooldown and not llm_pool._dead_pools


class TestSummarize:
    def test_summary_lists_causes(self, monkeypatch):
        monkeypatch.setenv("LLM_POOL_1", "https://coding/|keyA|glm-5.3-flash")
        monkeypatch.setenv("LLM_POOL_2", "https://std/|keyB|glm-4-flash")
        cands = llm_pool.candidates()
        llm_pool.note(cands[0], "quota", "限额将在 2099-01-01 08:00:00 重置")
        llm_pool.note(cands[1], "auth", "bad key")
        s = llm_pool.summarize()
        assert "池1" in s and "2099-01-01 08:00:00" in s and "池2" in s and "bad key" in s
