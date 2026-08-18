"""candidates.py 盘后候选构建测试（first_board 口径 + 增强标注 + 风险列 + 空数据防线）."""

from __future__ import annotations

import sqlite3
from typing import Any

import pandas as pd
from loguru import logger

from davis_analyzer.limitup import candidates

EVENT_DAY = "20240415"  # 周一；前置 60 个 bdate 横盘窗口


def _prior_dates(day: str, periods: int) -> list[str]:
    """事件日前恰好 periods 个工作日（end 用工作日本身，过滤非工作日 end 的差一）."""
    end = pd.to_datetime(day, format="%Y%m%d")
    dates = pd.bdate_range(end=end, periods=periods + 1)
    return [d.strftime("%Y%m%d") for d in dates if d.strftime("%Y%m%d") < day]


def _seed_stock(
    conn: sqlite3.Connection, code: str, day: str, *,
    periods: int = 60, alternating: bool = False,
) -> None:
    """前置横盘（10.0）或大箱体交替（10/13）后首板涨停的日线序列.

    60 日横盘 + 涨停创新高 → 突破型（prior_high60=10, box40=0）；
    45 日交替（不足 60 行 prior_high60 缺失 + box40≈0.30 ≥ 0.25）→ 其他。
    """
    rows = []
    for i, d in enumerate(_prior_dates(day, periods)):
        px = (10.0 if i % 2 == 0 else 13.0) if alternating else 10.0
        rows.append((code, d, px, px, px, px, px, 0.0, 1e4, 1e7, 1.0, None))
    # 事件日：非一字开盘、收盘涨停（round(10.0×1.1, 2)=11.0）
    rows.append((code, day, 10.5, 11.0, 10.5, 11.0, 10.0, 10.0, 1e6, 1e8, 1.0, None))
    conn.executemany("INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def _pool_row(
    code6: str, day: str, name: str, sector: str, boards: int,
    seal: float, broken: int, seal_time: str,
) -> tuple[Any, ...]:
    dash = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    return (dash, code6, "limit_up", name, sector, 10.0, seal, boards, broken,
            seal_time, seal_time, 5.0, None)


def _seed_day(conn: sqlite3.Connection, day: str = EVENT_DAY) -> None:
    """单日夹具：5 只目标股 + 30 只陪跑股（抬过 regime 冰点线 30 家）."""
    _seed_stock(conn, "600100.SH", day)                              # 甲 突破型
    _seed_stock(conn, "600200.SH", day)                              # 乙 突破型
    _seed_stock(conn, "600300.SH", day)                              # 丙 突破型(池记 2 板)
    _seed_stock(conn, "600400.SH", day, periods=45, alternating=True)  # 丁 其他
    _seed_stock(conn, "600500.SH", day)                              # 戊 突破型
    pool = [
        _pool_row("600100", day, "甲", "X业", 1, 1e8, 0, "093000"),
        _pool_row("600200", day, "乙", "Y业", 1, 3e7, 2, "103000"),
        _pool_row("600300", day, "丙", "Z业", 2, 1e8, 0, "093000"),
        _pool_row("600400", day, "丁", "W业", 1, 1e8, 0, "093000"),
        _pool_row("600500", day, "戊", "V业", 1, 5e7, 0, "143000"),
    ]
    # 陪跑股仅池行（无日线/无 basic → build_events 剔除，但 _limit_axes 计数）
    pool += [
        _pool_row(f"601{i:02d}", day, f"陪{i}", "P业", 1, 1e7, 0, "093000")
        for i in range(30)
    ]
    conn.executemany("INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", pool)
    dash = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    conn.executemany(
        "INSERT INTO limit_pool_ext VALUES (?,?,?,?)",
        [(dash, c, "limit_up", 1e9) for c in
         ("600100", "600200", "600300", "600400", "600500")],
    )
    conn.executemany(
        "INSERT INTO stock_basic VALUES (?,?,?,?,?,?)",
        [(f"{c}.SH", n, "I", "L", None, "20000101") for c, n in
         (("600100", "甲"), ("600200", "乙"), ("600300", "丙"),
          ("600400", "丁"), ("600500", "戊"))],
    )
    # moneyflow 当日：甲 大单主导 0.6 / 乙 小单主导 0.2 / 戊 无卖出额 → NaN
    conn.executemany(
        "INSERT INTO moneyflow VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (day, "600100.SH", 1e7, 2e7, 1e7, 2e7, 1e7, 3e7, 1e7, 3e7, 0.0, None),
            (day, "600200.SH", 1e7, 6e7, 1e7, 2e7, 1e7, 1e7, 1e7, 1e7, 0.0, None),
            (day, "600500.SH", None, None, None, None, None, None, None, None,
             None, None),
        ],
    )
    conn.commit()


def test_build_candidates_filters_order_and_annotations(
    limitup_db: sqlite3.Connection,
) -> None:
    _seed_day(limitup_db)
    df = candidates.build_candidates(limitup_db, EVENT_DAY)
    # 过滤：2 板（600300）与其他形态（600400）被滤；陪跑股无日线不入事件表
    assert list(df["ts_code"]) == ["600100.SH", "600500.SH", "600200.SH"]
    for col in candidates.CANDIDATE_COLUMNS:
        assert col in df.columns
    a, e, b = df.iloc[0], df.iloc[1], df.iloc[2]
    # 形态：60 日横盘后涨停创新高 → 突破型
    assert (df["pattern_label"] == "突破型").all()
    assert a["name"] == "甲" and a["sector"] == "X业"
    # seal_ratio = seal_amount/float_mv，降序输出
    assert abs(a["seal_ratio"] - 0.10) < 1e-9
    assert abs(e["seal_ratio"] - 0.05) < 1e-9
    assert abs(b["seal_ratio"] - 0.03) < 1e-9
    # lookback 窗口内前置涨停计数（窗口正确性证据）
    assert a["prev_limit_count_60"] == 0
    # 封档（pd.cut 右闭）：0.10→强；0.05/0.03→中
    assert a["封档"] == "强" and e["封档"] == "中" and b["封档"] == "中"
    # 首封时间档 / 炸板次数透传
    assert a["first_seal_band"] == "早盘"
    assert b["first_seal_band"] == "午盘"
    assert e["first_seal_band"] == "尾盘"
    assert b["broken_count"] == 2
    # 卖出结构：lg_sell_share = (大单+特大单卖出)/总卖出
    assert abs(a["lg_sell_share"] - 0.6) < 1e-9
    assert abs(b["lg_sell_share"] - 0.2) < 1e-9
    assert pd.isna(e["lg_sell_share"])  # sell_total<=0 → NaN
    # enhanced = 大单主导(≥0.50) × 强封(≥0.05)
    assert bool(a["enhanced"]) and a["enhanced"] is not None
    assert not bool(e["enhanced"])  # lg NaN → False
    assert not bool(b["enhanced"])  # seal 0.03 < 0.05
    # fill_prob（engine base 档）：早盘硬板 0.20 / 尾盘 0.35 / 炸板回封 0.70
    assert abs(a["fill_prob"] - 0.20) < 1e-9
    assert abs(e["fill_prob"] - 0.35) < 1e-9
    assert abs(b["fill_prob"] - 0.70) < 1e-9
    assert df["fill_prob"].between(0.05, 0.95).all()


def test_enhanced_filter_subset(limitup_db: sqlite3.Connection) -> None:
    _seed_day(limitup_db)
    df = candidates.build_candidates(limitup_db, EVENT_DAY, enhanced_filter=True)
    # 仅「大单主导 × 强封」子集（双臂对照的增强臂口径）
    assert list(df["ts_code"]) == ["600100.SH"]
    assert bool(df.iloc[0]["enhanced"])


def test_empty_limit_pool_day_warns_and_returns_empty(
    limitup_db: sqlite3.Connection,
) -> None:
    # 日历非空但当日 limit_pool 无行（19:20 daily 刷新失败场景）
    limitup_db.execute(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("600100.SH", "20240412", 10, 10, 10, 10, 10, 0, 1e4, 1e7, 1.0, None),
    )
    limitup_db.commit()
    msgs: list[str] = []
    handler = logger.add(lambda m: msgs.append(str(m)), level="WARNING")
    try:
        df = candidates.build_candidates(limitup_db, EVENT_DAY)
    finally:
        logger.remove(handler)
    assert df.empty  # 不抛异常
    assert "ts_code" in df.columns  # 契约列仍在（CLI 层可直接渲染）
    assert any("limit_pool" in m for m in msgs)  # 告警供 CLI 退出提示


def test_candidate_context(limitup_db: sqlite3.Connection) -> None:
    _seed_day(limitup_db)
    ctx = candidates.candidate_context(limitup_db, EVENT_DAY)
    assert ctx["trade_date"] == EVENT_DAY
    # 35 家涨停 > 30（冰点线），premium/promo_12 窗口末日不可观测 → 回暖
    assert ctx["regime_label"] == "回暖"
    assert ctx["limit_up_count"] == 35  # 5 目标 + 30 陪跑
    assert ctx["promo_12"] is None
    assert ctx["premium"] is None
    assert ctx["index_ma_bull"] is None  # §2.3.1 指数多空轴（夹具无 index_daily → None）


def test_candidate_context_empty_db(limitup_db: sqlite3.Connection) -> None:
    ctx = candidates.candidate_context(limitup_db, EVENT_DAY)
    assert ctx == {"trade_date": EVENT_DAY, "regime_label": "无数据"}


# ── markdown 渲染（纯 DataFrame，不走 DB）──

def _render_cands() -> pd.DataFrame:
    """渲染夹具：3 条候选（强封 enhanced / 中封 NaN / 弱封）覆盖全部分支."""
    return pd.DataFrame(
        {
            "ts_code": ["600100.SH", "600200.SH", "600300.SH"],
            "name": ["甲", "乙", "丙"],
            "sector": ["X业", "Y业", "Z业"],
            "pattern_label": ["突破型", "突破型", "横盘首板型"],
            "seal_ratio": [0.10, 0.05, 0.012],
            "封档": ["强", "中", "弱"],
            "first_seal_band": ["早盘", "尾盘", "早盘"],
            "broken_count": [0, 1, 0],
            "lg_sell_share": [0.6, float("nan"), 0.2],
            "enhanced": [True, False, False],
            "fill_prob": [0.20, 0.70, 0.20],
        }
    )


def _render_ctx(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "trade_date": EVENT_DAY,
        "limit_up_count": 35,
        "promo_12": None,       # 窗口末日不可观测 → "—"
        "premium": 0.005,
        "index_ma_bull": True,
        "regime_label": "回暖",
    }
    base.update(overrides)
    return base


def test_render_candidates_md_sections_and_pct() -> None:
    md = candidates.render_candidates_md(_render_cands(), _render_ctx())
    # 节 1：标题 + 四轴摘要行（None 轴显示 "—"）
    assert f"# 打板候选清单 {EVENT_DAY}" in md
    assert "情绪档位：回暖" in md
    assert "涨停家数 35" in md
    assert "晋级率 promo_12 —" in md
    assert "开盘溢价 0.5%" in md
    assert "指数多空 多" in md  # True → 多（§2.3.1 规格轴）
    # 节 2：候选表契约列头 + 百分比化（NaN → "—"）
    assert "| ts_code | name | sector | pattern_label | seal_ratio | 封档 | " \
           "first_seal_band | broken_count | lg_sell_share | enhanced | fill_prob |" in md
    assert "10.0%" in md and "60.0%" in md and "20.0%" in md
    assert "1.2%" in md  # seal_ratio 0.012
    row_yi = next(ln for ln in md.splitlines() if ln.startswith("| 600200.SH"))
    assert "—" in row_yi  # 乙 lg_sell_share=NaN → "—"
    # 节 3/4/5：标题齐全
    for heading in ("## 候选表", "## 增强标注", "## 次日执行提示", "## 免责声明"):
        assert heading in md
    assert "enhanced 为研究标注非过滤" in md
    # 节 4：中性化后无条件卖出指令不复存在；条件式持有 + 弱质注记两分支
    assert not hasattr(candidates, "SELL_HINT")  # 常量已移除
    assert "建议 T+1 开盘直接卖出" not in md  # 无条件卖出指令文案不复存在
    assert candidates.HOLD_HINT in md
    assert candidates.WEAK_SEAL_NOTE in md
    hint_line = next(
        ln for ln in md.splitlines() if ln.startswith("- 600100.SH 甲（封档=")
    )
    assert candidates.HOLD_HINT in hint_line  # fill_prob 0.20 → 纯条件式持有
    note_line = next(
        ln for ln in md.splitlines() if ln.startswith("- 600200.SH 乙（封档=")
    )
    assert candidates.WEAK_SEAL_NOTE in note_line  # fill_prob 0.70 → 持有+风险注记


def test_render_candidates_md_summary_bull_axis() -> None:
    # 指数多空轴三态：False → 空；None/缺失 → "—"
    md_false = candidates.render_candidates_md(
        _render_cands().head(1), _render_ctx(index_ma_bull=False)
    )
    assert "指数多空 空" in md_false
    md_none = candidates.render_candidates_md(
        _render_cands().head(1), _render_ctx(index_ma_bull=None)
    )
    assert "指数多空 —" in md_none


def test_render_candidates_md_enhanced_section_lists_only_true() -> None:
    md = candidates.render_candidates_md(_render_cands(), _render_ctx())
    enh_sec = md.split("## 增强标注")[1].split("## 次日执行提示")[0]
    assert "600100.SH 甲" in enh_sec  # enhanced=True 单独列出
    assert "600200.SH" not in enh_sec and "600300.SH" not in enh_sec


def test_render_candidates_md_top_truncates_table() -> None:
    md = candidates.render_candidates_md(_render_cands(), _render_ctx(), top=2)
    table_sec = md.split("## 候选表")[1].split("## 增强标注")[0]
    assert "600100.SH" in table_sec and "600200.SH" in table_sec
    assert "600300.SH" not in table_sec  # 第 3 条不入表


def test_render_candidates_md_empty_normal_regime() -> None:
    empty = pd.DataFrame(columns=candidates.CANDIDATE_COLUMNS)
    md = candidates.render_candidates_md(empty, _render_ctx())
    assert "当日无候选（regime=回暖，涨停 35 家）" in md
    assert "今日无" in md  # 增强标注节空态文案
    assert "## 免责声明" in md  # 空态报告结构完整


def test_render_candidates_md_no_data_regime_and_note() -> None:
    empty = pd.DataFrame(columns=candidates.CANDIDATE_COLUMNS)
    ctx = {"trade_date": EVENT_DAY, "regime_label": "无数据"}
    md = candidates.render_candidates_md(empty, ctx)
    assert "regime 无数据" in md  # 自动注明原因
    assert "情绪档位：无数据" in md
    # note 显式覆盖（limit_pool 缺数场景由 CLI 传入）
    md2 = candidates.render_candidates_md(
        empty, _render_ctx(), note="当日 limit_pool 无数据（daily 刷新失败?）"
    )
    assert "当日 limit_pool 无数据" in md2


def test_execution_hint_two_branches() -> None:
    # 评审中性化裁决：T 日 fill_prob 是事前特征不能预判 T+1 情形 →
    # 不再输出无条件卖出指令，统一条件式持有；fill_prob>=0.35 附加风险注记
    hold, note = candidates.HOLD_HINT, candidates.WEAK_SEAL_NOTE
    assert candidates.execution_hint(0.20) == hold
    assert candidates.execution_hint(0.34) == hold    # <0.35 边界内 → 纯持有纪律
    assert candidates.execution_hint(0.35) == note    # 0.35 非低（严格 <）→ 注记
    assert candidates.execution_hint(0.70) == note    # 易成交（炸板回封）→ 注记
    # 注记本身仍是条件式（保留 A 桶一字持有期权），非无条件指令
    assert "开盘=涨停价仍可持有观察" in note and "建议" not in note


def test_empty_candidates_message() -> None:
    msg = candidates.empty_candidates_message(
        {"regime_label": "回暖", "limit_up_count": 42}
    )
    assert msg == "当日无候选（regime=回暖，涨停 42 家）"


# ── --date 默认逻辑（daily_price 最新交易日）──

def test_latest_trade_date(limitup_db: sqlite3.Connection) -> None:
    from davis_analyzer.limitup import db

    assert db.latest_trade_date(limitup_db) is None  # 空库 → None（CLI 退出路径）
    limitup_db.execute(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("600100.SH", "20240412", 10, 10, 10, 10, 10, 0, 1e4, 1e7, 1.0, None),
    )
    limitup_db.execute(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("600100.SH", EVENT_DAY, 10, 11, 10, 11, 10, 10, 1e6, 1e8, 1.0, None),
    )
    limitup_db.commit()
    assert db.latest_trade_date(limitup_db) == EVENT_DAY


def test_cli_parser_candidates_defaults() -> None:
    from davis_analyzer.limitup import cli as limitup_cli

    args = limitup_cli._build_parser().parse_args(["candidates"])
    assert args.date is None  # 默认留给运行期查 daily_price MAX(trade_date)
    assert args.top == 10
    assert args.func is limitup_cli.cmd_candidates
    args2 = limitup_cli._build_parser().parse_args(
        ["candidates", "--date", "2024-04-15", "--top", "5"]
    )
    assert args2.date == "2024-04-15"
    assert args2.top == 5


def test_data_readiness_guards(limitup_db: sqlite3.Connection) -> None:
    """无池 / 池股无日线（覆盖率<80%）/ 就绪 三态判定。"""
    assert candidates.data_readiness(limitup_db, "20260818") == "no_pool"
    limitup_db.execute(
        "INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-08-18", "600001", "limit_up", "甲", "X", 10.0, 1e8, 1, 0,
         "092500", "092500", 3.0, None),
    )
    limitup_db.commit()
    # 池有行但该股当日无日线 → 覆盖率 0% → incomplete_prices
    assert candidates.data_readiness(limitup_db, "20260818") == "incomplete_prices"
    limitup_db.execute(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("600001.SH", "20260818", 10, 11, 10, 11, 10, 10, 0, 0, 1.0, None),
    )
    limitup_db.commit()
    assert candidates.data_readiness(limitup_db, "20260818") == "ok"


def test_volume_band_annotation() -> None:
    """量档（锁仓因子标注）：缩量/温和/放量/爆量四档 + NaN→渲染为"—"."""
    import pandas as pd

    from davis_analyzer.limitup import candidates as C

    assert "量档" in C.CANDIDATE_COLUMNS
    row = {"vol_ratio_20": 0.6}
    cut = pd.cut(pd.Series([0.6, 1.5, 3.0, 8.0, None]),
                 C.VOLUME_BANDS, labels=C.VOLUME_BAND_LABELS)
    assert [None if pd.isna(v) else v for v in cut] == ["缩量", "温和", "放量", "爆量", None]
    assert C._fmt_cell("量档", "缩量") == "缩量"
    assert C._fmt_cell("量档", None) == "—"
