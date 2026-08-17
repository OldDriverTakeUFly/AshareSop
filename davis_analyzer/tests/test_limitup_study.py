"""study.py 晋级率矩阵/收益分布/分桶有效性测试。"""

from __future__ import annotations

import pandas as pd

from davis_analyzer.limitup import report, study


def _ev(n: int = 40) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": [f"6000{i:02d}.SH" for i in range(n)],
        "trade_date": ["20240102"] * n,
        "consecutive_boards": ([1] * 20 + [2] * 20)[:n],
        "ret_open_1": ([0.03] * 10 + [-0.02] * 10 + [0.05] * 15 + [-0.06] * 5)[:n],
        "promoted": ([True] * 12 + [False] * 8 + [True] * 10 + [False] * 10)[:n],
        "pattern_label": (["突破型"] * 20 + ["其他"] * 20)[:n],
    })


def test_promotion_matrix() -> None:
    m = study.promotion_matrix(_ev())
    assert list(m.index) == [1, 2]
    assert abs(m.loc[1, "promo_rate"] - 0.6) < 1e-9
    assert m.loc[1, "n"] == 20


def test_return_distribution_stats() -> None:
    d = study.return_distribution(_ev(), by=["consecutive_boards"])
    row1 = d[d["consecutive_boards"] == 1].iloc[0]
    assert abs(row1["mean"] - 0.005) < 1e-9  # (10*0.03 + 10*-0.02)/20
    assert row1["win_rate"] == 0.5
    assert row1["n"] == 20


def test_feature_effectiveness_flags_small_sample() -> None:
    df = study.feature_effectiveness(_ev(10), "pattern_label")
    assert set(df["pattern_label"]) == {"突破型"}
    assert not bool(df.iloc[0]["enough_sample"])  # 10 < 30


def test_report_writes_markdown(tmp_path) -> None:
    tbl = pd.DataFrame({"a": [1, 2], "b": [0.5, -0.25]})
    md = report.df_to_md_table(tbl)
    # iterrows 将混合 int/float 行上溯为 float64，_fmt 统一 %.4f 渲染
    assert "| a | b |" in md and "|---|---|" in md and "| 1.0000 | 0.5000 |" in md
    out = report.write_report(tmp_path / "r.md", "测试", [("小节", md)])
    assert out.exists() and "测试" in out.read_text(encoding="utf-8")
