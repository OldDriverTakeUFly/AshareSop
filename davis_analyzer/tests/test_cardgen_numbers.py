# davis_analyzer/tests/test_cardgen_numbers.py
"""数值 token 提取、豁免掩码与事实命中判定。"""
from decimal import Decimal

from davis_analyzer.cardgen.numbers import extract_tokens, mask_exemptions, unmatched_tokens
from davis_analyzer.cardgen.types import Fact


def _f(fid: str, value: str, unit: str, display: str) -> Fact:
    return Fact(id=fid, value=Decimal(value), unit=unit, display=display,
                as_of="2026-08-28", source_kind="report", source_ref="docs/x.md#a")


FACTS = [
    _f("ps", "143", "x", "143x"), _f("rev", "17.36", "亿", "17.36亿"),
    _f("gr", "1998", "%", "+1998%"), _f("gap", "3.5", "倍", "3.5倍"),
    _f("disc", "75", "%", "75%"), _f("fw", "16", "x", "≈16x前瞻"),
    _f("dd", "43", "%", "-43%"), _f("mv", "2700", "亿", "2700亿"),
    _f("low", "400", "亿", "400-600亿"), _f("high", "600", "亿", "400-600亿"),
    _f("g1", "326", "%", "+326%~455%"), _f("g2", "455", "%", "+326%~455%"),
    _f("nv", "98.8", "x", "98.8x"), _f("hk", "1000", "亿港元", "≈1000亿港元"),
]


class TestExtract:
    def test_simple(self):
        t = extract_tokens("H1营收17.36亿超去年全年")
        assert (t[0].value, t[0].unit) == (Decimal("17.36"), "亿")

    def test_percent_with_sign(self):
        t = extract_tokens("同比+1998%增长")
        assert (t[0].value, t[0].unit) == (Decimal("1998"), "%")
        t2 = extract_tokens("距峰值-43%背景下")
        assert (t2[0].value, t2[0].unit) == (Decimal("43"), "%")

    def test_multiple_and_approx(self):
        t = extract_tokens("PS 143x vs 98.8x,发行约≈16x")
        assert [(x.value, x.unit) for x in t] == [(Decimal("143"), "x"), (Decimal("98.8"), "x"), (Decimal("16"), "x")]

    def test_range_dash(self):
        t = extract_tokens("发行隐含市值400-600亿")
        assert [(x.value, x.unit) for x in t] == [(Decimal("400"), "亿"), (Decimal("600"), "亿")]

    def test_range_tilde_percent(self):
        t = extract_tokens("9月指引+326%~455%")
        assert [(x.value, x.unit) for x in t] == [(Decimal("326"), "%"), (Decimal("455"), "%")]

    def test_hkd_unit(self):
        t = extract_tokens("港股≈1000亿港元")
        assert (t[0].value, t[0].unit) == (Decimal("1000"), "亿港元")

    def test_four_digit_quantity_not_masked_as_year(self):
        # 回归:裸 \d{4} 年份掩码曾把 2700亿/4000万 当年份豁免,绕过溯源
        assert [(t.value, t.unit) for t in extract_tokens("总市值2700亿")] == [(Decimal("2700"), "亿")]
        assert [(t.value, t.unit) for t in extract_tokens("约4000万")] == [(Decimal("4000"), "万")]


class TestExemptions:
    def test_dates_masked(self):
        assert extract_tokens("8/31沐曦中报 + 燧原定价") == []
        assert extract_tokens("12/07大解禁1.86亿股") == [extract_tokens("1.86亿")[0]]

    def test_year_and_halfyear(self):
        assert extract_tokens("2026H2训推一体量产") == []
        assert extract_tokens("TrendForce预计2026国产份额") == []

    def test_compact_tradedate(self):
        assert extract_tokens("trade_date=20260828") == []

    def test_identifiers(self):
        assert extract_tokens("沐曦C600全流程、壁仞BR166、B30A许可、燧原688801") == []

    def test_ratio_and_time(self):
        assert extract_tokens("3:4版式,19:55盘后") == []

    def test_chinese_numerals_ignored(self):
        assert extract_tokens("四小龙、九道关、四家") == []

    def test_hex_color_masked(self):
        assert extract_tokens("#ff2442 和 #0f172a") == []


class TestMatch:
    def test_all_registered_passes(self):
        text = "PS(TTM) 143x,营收17.36亿,同比+1998%,差3.5倍,折价75%,≈16x前瞻,-43%,2700亿"
        assert unmatched_tokens(text, FACTS) == []

    def test_unregistered_number_flagged(self):
        t = unmatched_tokens("毛利率30.4%垫底", FACTS)
        assert len(t) == 1 and t[0].value == Decimal("30.4")

    def test_unit_mismatch_flagged(self):
        # 143 已登记但单位是 x;文中写 143亿 → 不算命中
        t = unmatched_tokens("市值143亿", FACTS)
        assert len(t) == 1

    def test_range_members_both_required(self):
        assert unmatched_tokens("400-600亿区间", FACTS) == []
        t = unmatched_tokens("400-800亿区间", FACTS)
        assert {str(x.value) for x in t} == {"800"}
