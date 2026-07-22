"""盘前报告飞书摘要生成器的单元测试.

重点测试 _format_advisor_brief 的 markdown 表格解析（纯函数，易出错），
以及 build_premarket_feishu_summary 的结构完整性。数据获取函数用 monkeypatch mock。
"""

from __future__ import annotations

import pytest

TEST_DATE = "2026-07-22"


# ===================================================================
# _format_advisor_brief — advisor markdown 表格解析
# ===================================================================


class TestFormatAdvisorBrief:
    """测试 advisor section markdown → 纯文本的转换."""

    def test_empty_advisor_section_returns_placeholder(self, monkeypatch):
        """无建议时返回占位."""
        from stockhot.invest_sop.scripts import premarket_feishu_summary as mod

        empty_section = """
---

<!-- ADVISOR_SECTION_START -->
## AI 综合建议（2026-07-22）

暂无 AI 建议

> ⚠️ 以上建议仅供参考。
<!-- ADVISOR_SECTION_END -->
"""
        monkeypatch.setattr(
            "stockhot.advisor.report_integration.build_advisor_section",
            lambda _: empty_section,
        )
        lines = mod._format_advisor_brief(TEST_DATE)
        assert lines == ["  · 暂无 AI 建议"]

    def test_advisor_table_rows_parsed(self, monkeypatch):
        """有建议表格行时正确解析为纯文本."""
        from stockhot.invest_sop.scripts import premarket_feishu_summary as mod

        section = """
<!-- ADVISOR_SECTION_START -->
## AI 综合建议（2026-07-22）

| 代码 | 操作 | 置信度 | 入场区间 | 止损 | 目标价 | 理由 |
|------|------|--------|----------|------|--------|------|
| 000001 | 建仓 | HIGH | 10-11 | 9.5 | 13 | 估值低估且景气度回升 |
| 600000 | 调仓 | MEDIUM | - | - | - | 技术面转弱减仓一成 |

> ⚠️ 以上建议仅供参考。
<!-- ADVISOR_SECTION_END -->
"""
        monkeypatch.setattr(
            "stockhot.advisor.report_integration.build_advisor_section",
            lambda _: section,
        )
        lines = mod._format_advisor_brief(TEST_DATE)
        # 应解析出 2 条建议（跳过表头/分隔线/标题/哨兵）
        assert len(lines) == 2
        assert "000001" in lines[0]
        assert "建仓" in lines[0]
        assert "HIGH" in lines[0]
        assert "600000" in lines[1]
        assert "调仓" in lines[1]

    def test_long_reason_truncated(self, monkeypatch):
        """理由过长时截断并加省略号."""
        from stockhot.invest_sop.scripts import premarket_feishu_summary as mod

        long_reason = "这是一段非常长的理由说明" * 10  # 远超 40 字
        section = f"""
<!-- ADVISOR_SECTION_START -->
## AI 综合建议（2026-07-22）

| 代码 | 操作 | 置信度 | 理由 |
|------|------|--------|------|
| 000001 | 建仓 | HIGH | {long_reason} |

<!-- ADVISOR_SECTION_END -->
"""
        monkeypatch.setattr(
            "stockhot.advisor.report_integration.build_advisor_section",
            lambda _: section,
        )
        lines = mod._format_advisor_brief(TEST_DATE)
        assert len(lines) == 1
        assert "…" in lines[0]  # 被截断

    def test_advisor_import_failure_returns_error_msg(self, monkeypatch):
        """build_advisor_section 抛异常时返回错误提示而非 crash."""
        from stockhot.invest_sop.scripts import premarket_feishu_summary as mod

        def _raise(*_):
            raise RuntimeError("DB locked")

        monkeypatch.setattr(
            "stockhot.advisor.report_integration.build_advisor_section", _raise
        )
        lines = mod._format_advisor_brief(TEST_DATE)
        assert len(lines) == 1
        assert "数据读取失败" in lines[0]


# ===================================================================
# build_premarket_feishu_summary — 结构完整性
# ===================================================================


class TestBuildSummary:
    """测试完整摘要的结构（用 mock 数据，无网络/DB 依赖）."""

    def _mock_data(self, monkeypatch, *, holdings=None, index_tech=None, volatility=None):
        """mock 所有数据获取函数."""
        from stockhot.invest_sop.scripts import premarket_feishu_summary as mod

        # _fetch_active_holdings() 无参；其余两个接收 date 参数
        monkeypatch.setattr(mod, "_fetch_active_holdings", lambda: holdings or [])
        monkeypatch.setattr(mod, "_fetch_latest_index_technical", lambda _: index_tech)
        monkeypatch.setattr(mod, "_fetch_latest_volatility", lambda _: volatility)
        # _derive_market_sentiment 和 _format_volatility_row 复用真实逻辑，
        # 用真实 index_tech/volatility 结构驱动
        monkeypatch.setattr(
            "stockhot.advisor.report_integration.build_advisor_section",
            lambda _: "<!-- ADVISOR_SECTION_START -->\n## AI 综合建议\n暂无 AI 建议\n<!-- ADVISOR_SECTION_END -->",
        )

    def test_summary_contains_required_sections(self, monkeypatch):
        """摘要包含所有必备章节标题."""
        from stockhot.invest_sop.scripts import premarket_feishu_summary as mod

        self._mock_data(monkeypatch)
        summary = mod.build_premarket_feishu_summary(TEST_DATE)

        assert "📊 盘前SOP报告" in summary
        assert "🎯 整体判断" in summary
        assert "📈 大盘技术面" in summary
        assert "💼 持仓标的" in summary
        assert "🤖 AI 综合建议" in summary
        assert "⚠️ 风控检查" in summary
        assert "📄 完整报告" in summary

    def test_summary_contains_github_link(self, monkeypatch):
        """摘要末尾有正确的 GitHub 完整报告链接."""
        from stockhot.invest_sop.scripts import premarket_feishu_summary as mod

        self._mock_data(monkeypatch)
        summary = mod.build_premarket_feishu_summary(TEST_DATE)

        expected = (
            "https://github.com/OldDriverTakeUFly/AshareSop/blob/master/"
            "storage/files/reports/invest_sop/2026-07-22_pre_market.md"
        )
        assert expected in summary

    def test_summary_contains_weekday(self, monkeypatch):
        """摘要标题含星期（TEST_DATE 2026-07-22 是周三）."""
        from stockhot.invest_sop.scripts import premarket_feishu_summary as mod

        self._mock_data(monkeypatch)
        summary = mod.build_premarket_feishu_summary(TEST_DATE)
        assert "星期三" in summary

    def test_summary_no_markdown_table_syntax(self, monkeypatch):
        """摘要不应残留 markdown 表格语法（飞书 text 模式不渲染）."""
        from stockhot.invest_sop.scripts import premarket_feishu_summary as mod

        self._mock_data(monkeypatch)
        summary = mod.build_premarket_feishu_summary(TEST_DATE)
        # 不应含 markdown 表格分隔线或表头标记
        assert "|--" not in summary
        assert "| 代码" not in summary

    def test_holdings_rendered_when_present(self, monkeypatch):
        """有持仓时正确渲染持仓信息."""
        from stockhot.invest_sop.scripts import premarket_feishu_summary as mod

        holdings = [
            {
                "name": "扬杰科技", "code": "300373", "position_pct": 15,
                "target_price": 136.04, "stop_loss_hard": 92.51,
            }
        ]
        self._mock_data(monkeypatch, holdings=holdings)
        summary = mod.build_premarket_feishu_summary(TEST_DATE)

        assert "扬杰科技" in summary
        assert "300373" in summary
        assert "15%" in summary
        assert "136.04" in summary

    def test_no_holdings_shows_placeholder(self, monkeypatch):
        """无持仓时显示占位."""
        from stockhot.invest_sop.scripts import premarket_feishu_summary as mod

        self._mock_data(monkeypatch, holdings=[])
        summary = mod.build_premarket_feishu_summary(TEST_DATE)
        assert "无活跃持仓" in summary
