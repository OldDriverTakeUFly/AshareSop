import pytest

from davis_analyzer.rescorer import (
    batch_rescore,
    parse_checklist,
    rescore,
)
from davis_analyzer.types import DavisDoubleScore, PipelineResult, RescoredResult


# ── Fixtures ─────────────────────────────────────────────────────────


_FILLED_CHECKLIST = """\
# 1_000001.SZ_平安银行 深度调研Checklist

## 基础信息（自动填充）
| 项目 | 值 |
|------|-----|
| 股票代码 | 000001.SZ |
| 股票名称 | 平安银行 |
| 所属行业 | 银行 |
| 周期股 | 否 |
| 综合评分 | 72.5 |
| 估值分位 | 80.0 |
| 趋势评分 | 65.0 |
| 景气度评分 | 70.0 |
| 困境反转评分 | 60.0 |
| 排名 | 1 |

## 调研项（请人工填写）
### 1. 行业政策
- [ ] 当前行业政策环境（利好/中性/利空）：利好
- [ ] 政策变化趋势：积极
- [ ] 相关政策文件/日期：2026年1月

### 2. 机构观点
- [ ] 最近3个月机构评级：买入
- [ ] 目标价共识：15.5
- [ ] 主要机构观点摘要：看好

### 3. 公司公告
- [ ] 近期重大公告：回购
- [ ] 公告日期及内容：2026-01-15

### 4. 竞争格局
- [ ] 行业竞争地位：龙头
- [ ] 主要竞争对手及市场份额：40%
- [ ] 竞争优势/护城河：品牌

### 5. 管理层治理
- [ ] 管理层评价：优秀
- [ ] 股权激励/管理层持股：有
- [ ] 公司治理风险点：无

### 6. 定性判断
#### 景气度调整幅度（-20到+20）
> 当前景气度评分：70.0
+10（请填写调整值）

#### 困境反转调整幅度（-20到+20）
> 当前困境反转评分：60.0
-5（请填写调整值）
"""

_EMPTY_CHECKLIST = """\
# 2_000002.SZ_万科A 深度调研Checklist

## 基础信息（自动填充）
| 项目 | 值 |
|------|-----|
| 股票代码 | 000002.SZ |
| 股票名称 | 万科A |
| 所属行业 | 房地产 |
| 周期股 | 否 |
| 综合评分 | 50.0 |
| 估值分位 | 60.0 |
| 趋势评分 | 40.0 |
| 景气度评分 | 55.0 |
| 困境反转评分 | 45.0 |
| 排名 | 2 |

## 调研项（请人工填写）
### 6. 定性判断
#### 景气度调整幅度（-20到+20）
> 当前景气度评分：55.0
___（请填写调整值）

#### 困境反转调整幅度（-20到+20）
> 当前困境反转评分：45.0
___（请填写调整值）
"""

_INVALID_CHECKLIST = """\
# 3_000003.SZ_测试股票 深度调研Checklist

## 基础信息（自动填充）
| 项目 | 值 |
|------|-----|
| 股票代码 | 000003.SZ |
| 股票名称 | 测试股票 |
| 排名 | 3 |

### 6. 定性判断
#### 景气度调整幅度（-20到+20）
> 当前景气度评分：50.0
abc（请填写调整值）

#### 困境反转调整幅度（-20到+20）
> 当前困境反转评分：50.0
xyz（请填写调整值）
"""

_OUT_OF_RANGE_CHECKLIST = """\
# 4_000004.SZ_测试股票2 深度调研Checklist

## 基础信息（自动填充）
| 项目 | 值 |
|------|-----|
| 股票代码 | 000004.SZ |
| 股票名称 | 测试股票2 |
| 排名 | 4 |

### 6. 定性判断
#### 景气度调整幅度（-20到+20）
> 当前景气度评分：60.0
50（请填写调整值）

#### 困境反转调整幅度（-20到+20）
> 当前困境反转评分：50.0
-30（请填写调整值）
"""


@pytest.fixture
def _write_checklists(tmp_path):
    filled = tmp_path / "1_000001.SZ_平安银行_调研checklist.md"
    filled.write_text(_FILLED_CHECKLIST, encoding="utf-8")

    empty = tmp_path / "2_000002.SZ_万科A_调研checklist.md"
    empty.write_text(_EMPTY_CHECKLIST, encoding="utf-8")

    invalid = tmp_path / "3_000003.SZ_测试股票_调研checklist.md"
    invalid.write_text(_INVALID_CHECKLIST, encoding="utf-8")

    out_of_range = tmp_path / "4_000004.SZ_测试股票2_调研checklist.md"
    out_of_range.write_text(_OUT_OF_RANGE_CHECKLIST, encoding="utf-8")

    return {
        "filled": str(filled),
        "empty": str(empty),
        "invalid": str(invalid),
        "out_of_range": str(out_of_range),
        "dir": str(tmp_path),
    }


# ── parse_checklist tests ────────────────────────────────────────────


class TestParseChecklist:
    def test_parses_filled_checklist(self, _write_checklists):
        result = parse_checklist(_write_checklists["filled"])
        assert result["ts_code"] == "000001.SZ"
        assert result["name"] == "平安银行"
        assert result["rank"] == 1
        assert result["prosperity_adjustment"] == pytest.approx(10.0, abs=0.01)
        assert result["distress_adjustment"] == pytest.approx(-5.0, abs=0.01)

    def test_extracts_raw_research(self, _write_checklists):
        result = parse_checklist(_write_checklists["filled"])
        research = result["raw_research"]
        assert "行业政策/当前行业政策环境（利好/中性/利空）" in research
        assert research["行业政策/当前行业政策环境（利好/中性/利空）"] == "利好"
        assert "机构观点/最近3个月机构评级" in research
        assert research["机构观点/最近3个月机构评级"] == "买入"

    def test_empty_adjustment_defaults_zero(self, _write_checklists):
        result = parse_checklist(_write_checklists["empty"])
        assert result["prosperity_adjustment"] == 0.0
        assert result["distress_adjustment"] == 0.0

    def test_invalid_value_defaults_zero(self, _write_checklists):
        result = parse_checklist(_write_checklists["invalid"])
        assert result["prosperity_adjustment"] == 0.0
        assert result["distress_adjustment"] == 0.0

    def test_out_of_range_clamped(self, _write_checklists):
        result = parse_checklist(_write_checklists["out_of_range"])
        assert result["prosperity_adjustment"] == pytest.approx(20.0, abs=0.01)
        assert result["distress_adjustment"] == pytest.approx(-20.0, abs=0.01)

    def test_parses_ts_code_from_empty_checklist(self, _write_checklists):
        result = parse_checklist(_write_checklists["empty"])
        assert result["ts_code"] == "000002.SZ"
        assert result["name"] == "万科A"
        assert result["rank"] == 2

    def test_negative_adjustment_with_sign(self, tmp_path):
        checklist = """\
# 1_000001.SZ_测试 深度调研Checklist

| 股票代码 | 000001.SZ |
| 股票名称 | 测试 |
| 排名 | 1 |

#### 景气度调整幅度（-20到+20）
> 当前景气度评分：50.0
-15.5（请填写调整值）

#### 困境反转调整幅度（-20到+20）
> 当前困境反转评分：50.0
+8.5（请填写调整值）
"""
        path = tmp_path / "test.md"
        path.write_text(checklist, encoding="utf-8")
        result = parse_checklist(str(path))
        assert result["prosperity_adjustment"] == pytest.approx(-15.5, abs=0.01)
        assert result["distress_adjustment"] == pytest.approx(8.5, abs=0.01)


# ── rescore tests ────────────────────────────────────────────────────


class TestRescore:
    def test_normal_adjustment(self):
        checklist_data = {
            "ts_code": "000001.SZ",
            "name": "TestStock",
            "prosperity_adjustment": 10.0,
            "distress_adjustment": -5.0,
        }
        result = rescore(55.0, 60.0, checklist_data)
        assert isinstance(result, RescoredResult)
        assert result.adjusted_prosperity == pytest.approx(65.0, abs=0.01)
        assert result.adjusted_distress == pytest.approx(55.0, abs=0.01)
        assert result.original_prosperity == 55.0
        assert result.original_distress == 60.0
        assert result.prosperity_adjustment == 10.0
        assert result.distress_adjustment == -5.0
        assert result.ts_code == "000001.SZ"

    def test_clamp_high_to_100(self):
        checklist_data = {
            "ts_code": "000001.SZ",
            "name": "TestStock",
            "prosperity_adjustment": 10.0,
            "distress_adjustment": 0.0,
        }
        result = rescore(95.0, 50.0, checklist_data)
        assert result.adjusted_prosperity == pytest.approx(100.0, abs=0.01)
        assert result.adjusted_distress == 50.0

    def test_clamp_low_to_zero(self):
        checklist_data = {
            "ts_code": "000001.SZ",
            "name": "TestStock",
            "prosperity_adjustment": -20.0,
            "distress_adjustment": 0.0,
        }
        result = rescore(15.0, 50.0, checklist_data)
        assert result.adjusted_prosperity == pytest.approx(0.0, abs=0.01)

    def test_zero_adjustment_no_change(self):
        checklist_data = {
            "ts_code": "000001.SZ",
            "name": "TestStock",
            "prosperity_adjustment": 0.0,
            "distress_adjustment": 0.0,
        }
        result = rescore(42.5, 67.5, checklist_data)
        assert result.adjusted_prosperity == pytest.approx(42.5, abs=0.01)
        assert result.adjusted_distress == pytest.approx(67.5, abs=0.01)

    def test_distress_clamp_high(self):
        checklist_data = {
            "ts_code": "000001.SZ",
            "name": "TestStock",
            "prosperity_adjustment": 0.0,
            "distress_adjustment": 20.0,
        }
        result = rescore(50.0, 90.0, checklist_data)
        assert result.adjusted_distress == pytest.approx(100.0, abs=0.01)

    def test_distress_clamp_low(self):
        checklist_data = {
            "ts_code": "000001.SZ",
            "name": "TestStock",
            "prosperity_adjustment": 0.0,
            "distress_adjustment": -20.0,
        }
        result = rescore(50.0, 10.0, checklist_data)
        assert result.adjusted_distress == pytest.approx(0.0, abs=0.01)


# ── batch_rescore tests ──────────────────────────────────────────────


def _make_pipeline_result():
    return PipelineResult(
        scores=[
            DavisDoubleScore(
                ts_code="000001.SZ",
                name="平安银行",
                valuation_score=80.0,
                prosperity_score=70.0,
                distress_score=60.0,
                final_score=72.5,
                rank=1,
            ),
            DavisDoubleScore(
                ts_code="000002.SZ",
                name="万科A",
                valuation_score=60.0,
                prosperity_score=55.0,
                distress_score=45.0,
                final_score=50.0,
                rank=2,
            ),
        ],
        stock_infos={},
        valuation_data={},
        prosperity_scores={},
        distress_signals={},
        financial_data={},
    )


class TestBatchRescore:
    def test_rescores_multiple_stocks(self, _write_checklists):
        pipeline = _make_pipeline_result()
        results = batch_rescore(pipeline, _write_checklists["dir"])
        assert len(results) >= 2
        assert "000001.SZ" in results
        assert "000002.SZ" in results

    def test_applies_adjustments(self, _write_checklists):
        pipeline = _make_pipeline_result()
        results = batch_rescore(pipeline, _write_checklists["dir"])
        r1 = results["000001.SZ"]
        assert r1.adjusted_prosperity == pytest.approx(80.0, abs=0.01)
        assert r1.adjusted_distress == pytest.approx(55.0, abs=0.01)

    def test_empty_adjustment_stays_same(self, _write_checklists):
        pipeline = _make_pipeline_result()
        results = batch_rescore(pipeline, _write_checklists["dir"])
        r2 = results["000002.SZ"]
        assert r2.adjusted_prosperity == pytest.approx(55.0, abs=0.01)
        assert r2.adjusted_distress == pytest.approx(45.0, abs=0.01)

    def test_skips_unknown_ts_code(self, _write_checklists):
        pipeline = _make_pipeline_result()
        results = batch_rescore(pipeline, _write_checklists["dir"])
        assert "000003.SZ" not in results
        assert "000004.SZ" not in results

    def test_empty_dir_returns_empty(self, tmp_path):
        pipeline = _make_pipeline_result()
        results = batch_rescore(pipeline, str(tmp_path))
        assert results == {}

    def test_returns_rescored_result_type(self, _write_checklists):
        pipeline = _make_pipeline_result()
        results = batch_rescore(pipeline, _write_checklists["dir"])
        for value in results.values():
            assert isinstance(value, RescoredResult)
