"""TDD tests for prompt template registry and pre-registered templates."""

from __future__ import annotations

import pytest

from stockhot.advisor.prompts.registry import (
    PromptRegistry,
    PromptTemplate,
    default_registry,
)
from stockhot.advisor.prompts import templates  # noqa: F401 — triggers registration

# ── PromptRegistry basic CRUD ────


class TestRegistryBasic:
    """Test register / get / list_names on a fresh registry."""

    def test_register_and_get(self):
        reg = PromptRegistry()
        tpl = PromptTemplate(
            name="test_op",
            version="v1",
            system="sys",
            user_template="hello {code}",
            expected_output_schema={"action": "buy"},
        )
        reg.register(tpl)
        result = reg.get("test_op", "v1")
        assert result is tpl

    def test_get_nonexistent_name_raises_keyerror(self):
        reg = PromptRegistry()
        with pytest.raises(KeyError):
            reg.get("does_not_exist")

    def test_get_nonexistent_version_raises_keyerror(self):
        reg = PromptRegistry()
        tpl = PromptTemplate(
            name="x",
            version="v1",
            system="s",
            user_template="u",
            expected_output_schema={},
        )
        reg.register(tpl)
        with pytest.raises(KeyError):
            reg.get("x", "v2")

    def test_list_names(self):
        reg = PromptRegistry()
        reg.register(PromptTemplate("a", "v1", "s", "u", {}))
        reg.register(PromptTemplate("b", "v1", "s", "u", {}))
        names = sorted(reg.list_names())
        assert names == ["a", "b"]

    def test_list_names_empty(self):
        reg = PromptRegistry()
        assert reg.list_names() == []


# ── Version management ────


class TestVersionManagement:
    """Test latest-version resolution and explicit version retrieval."""

    def test_get_latest_returns_highest_version(self):
        reg = PromptRegistry()
        v1 = PromptTemplate("op", "v1", "s1", "u1", {})
        v2 = PromptTemplate("op", "v2", "s2", "u2", {})
        reg.register(v1)
        reg.register(v2)
        result = reg.get("op")  # no version → latest
        assert result is v2

    def test_get_specific_version(self):
        reg = PromptRegistry()
        v1 = PromptTemplate("op", "v1", "s1", "u1", {})
        v2 = PromptTemplate("op", "v2", "s2", "u2", {})
        reg.register(v1)
        reg.register(v2)
        result = reg.get("op", version="v1")
        assert result is v1

    def test_list_names_dedupes_versions(self):
        reg = PromptRegistry()
        reg.register(PromptTemplate("op", "v1", "s", "u", {}))
        reg.register(PromptTemplate("op", "v2", "s", "u", {}))
        assert reg.list_names() == ["op"]


# ── Pre-registered templates ────


EXPECTED_TEMPLATE_NAMES = [
    "build_position",
    "adjust_position",
    "clear_position",
    "t_trade",
]


class TestPreRegisteredTemplates:
    """Test all 4 pre-registered templates exist with valid fields."""

    @pytest.mark.parametrize("name", EXPECTED_TEMPLATE_NAMES)
    def test_template_registered_in_default(self, name):
        names = default_registry.list_names()
        assert name in names, f"{name} not registered in default_registry"

    @pytest.mark.parametrize("name", EXPECTED_TEMPLATE_NAMES)
    def test_template_has_all_fields(self, name):
        tpl = default_registry.get(name)
        assert tpl.name == name
        assert tpl.version, f"{name} version is empty"
        assert tpl.system, f"{name} system is empty"
        assert tpl.user_template, f"{name} user_template is empty"
        assert tpl.expected_output_schema, f"{name} schema is empty"

    @pytest.mark.parametrize("name", EXPECTED_TEMPLATE_NAMES)
    def test_anti_hallucination_instruction(self, name):
        tpl = default_registry.get(name)
        assert (
            "不能编造" in tpl.system
        ), f"{name} system prompt missing anti-hallucination instruction"

    def test_build_position_placeholders(self):
        """build_position must include all required placeholders."""
        tpl = default_registry.get("build_position")
        required = [
            "{code}",
            "{current_price}",
            "{technical_score}",
            "{technical_state}",
            "{davis_score}",
            "{davis_percentile}",
            "{support_levels}",
            "{resistance_levels}",
            "{volume_ratio}",
        ]
        for ph in required:
            assert ph in tpl.user_template, f"build_position missing placeholder {ph}"

    def test_adjust_position_placeholders(self):
        tpl = default_registry.get("adjust_position")
        for ph in [
            "{code}",
            "{current_price}",
            "{position_pct}",
            "{signals}",
            "{avg_cost}",
            "{unrealized_pnl_pct}",
        ]:
            assert ph in tpl.user_template, f"adjust_position missing placeholder {ph}"

    def test_clear_position_placeholders(self):
        tpl = default_registry.get("clear_position")
        for ph in [
            "{code}",
            "{triggered_signals}",
            "{current_price}",
            "{stop_loss_hard}",
            "{thesis_status}",
        ]:
            assert ph in tpl.user_template, f"clear_position missing placeholder {ph}"

    def test_t_trade_placeholders(self):
        tpl = default_registry.get("t_trade")
        for ph in [
            "{code}",
            "{current_price}",
            "{support_levels}",
            "{resistance_levels}",
            "{volume_ratio}",
            "{recent_volume_trend}",
        ]:
            assert ph in tpl.user_template, f"t_trade missing placeholder {ph}"
