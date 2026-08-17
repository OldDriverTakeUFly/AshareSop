"""evolution 战役循环与晋升门槛测试。"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

import davis_analyzer.tournament.evolution as evolution_mod
import davis_analyzer.tushare_client as tushare_client_mod
from davis_analyzer.tournament import judge as judge_mod
from davis_analyzer.tournament import ledger as ledger_mod
from davis_analyzer.tournament.cli import main
from davis_analyzer.tournament.evolution import (
    check_promotion,
    improvement_distribution,
    perturb_decay,
    run_campaign,
)
from davis_analyzer.tournament.ledger import LedgerRecord, append_record


def _mutate(params, rng):
    return {k: v + rng.gauss(0, 0.05) for k, v in params.items()}


def test_campaign_converges_toward_optimum() -> None:
    # 适应度只认 momentum_weight→0.8；初始 0.2，进化应显著逼近
    score_fn = lambda params, ranges: 1.0 - abs(params["momentum_weight"] - 0.8)  # noqa: E731
    best, best_score = run_campaign(
        {"momentum_weight": 0.2}, _mutate, score_fn,
        selection_ranges=[("s1", "e1")], seed=3,
    )
    assert best["momentum_weight"] > 0.5
    assert best_score > 0.7


def test_improvement_distribution_signs() -> None:
    score_fn = lambda params, ranges: params["momentum_weight"]  # noqa: E731
    inc = {"momentum_weight": 0.5}
    chall = {"momentum_weight": 0.7}
    splits = [[("v1", "v2")], [("v3", "v4")]]
    diffs = improvement_distribution(score_fn, inc, chall, splits)
    assert diffs == [pytest.approx(0.2), pytest.approx(0.2)]


def test_perturb_decay_ratio() -> None:
    decay = perturb_decay(challenger_score=1.0, perturbed_scores=[0.9, 0.8])
    assert decay == pytest.approx(0.15)  # (1 − 0.85) / |1|
    # base≤0：旧公式 1 − (−2/−1) = −1 会反向"通过"；新公式对称触发失败
    assert perturb_decay(challenger_score=-1.0, perturbed_scores=[-2.0]) == pytest.approx(1.0)
    # 改进不得为负 decay（perturbed 优于 base 时钳到 0）
    assert perturb_decay(challenger_score=1.0, perturbed_scores=[1.2, 1.1]) == 0.0
    # 非有限输入 / 空扰动样本 → fail-closed 为 inf（门槛必拒）
    assert perturb_decay(float("nan"), [0.9]) == float("inf")
    assert perturb_decay(1.0, [float("inf")]) == float("inf")
    assert perturb_decay(1.0, []) == float("inf")


def test_promotion_nonfinite_fails_closed() -> None:
    nan_case = check_promotion([float("nan")] * 20, 0.1, True)
    assert nan_case.ok is False
    assert nan_case.reasons == ["非有限评分（窗口样本不足）"]
    inf_case = check_promotion([0.5] * 20, float("inf"), True)
    assert inf_case.ok is False
    assert inf_case.reasons == ["非有限评分（窗口样本不足）"]


def test_evolve_perturbation_changes_weight_ratios(monkeypatch: pytest.MonkeyPatch) -> None:
    """C1 回归锁：backtest_factors._blend 会归一化权重——全参数同乘 (1±pct)
    的旧扰动被完全抵消（perturbed==base → decay 恒 0 → 门槛永不失败）。
    逐参数独立符号必须改变权重比例，使 decay 脱离 0。"""
    conn = _patch_evolve_env(monkeypatch)
    # 冻结变异强度：best 保持在种子（内部值），单独检验扰动块本身
    monkeypatch.setattr(evolution_mod, "TOURNAMENT_MUTATION_SIGMA", 0.0)

    def _ratio_score(params: dict, ranges: list) -> float:
        m = float(params.get("momentum_weight", 0.2))
        v = float(params.get("valuation_weight", 0.2))
        ratio = m / (m + v)  # 齐次零次：对全参数统一缩放不变（模拟 _blend 归一化）
        return 1.0 - (ratio - 0.5) ** 2  # 在 m=v（种子值 0.2/0.2）处取极大

    monkeypatch.setattr(
        evolution_mod, "build_score_fn", lambda judge, participant: _ratio_score,
    )
    try:
        main(list(_EVO_ARGS))  # davis_balanced 种子 m=v → base 为内部极大值
        detail = json.loads(conn.execute(
            "SELECT detail FROM tournament_ledger WHERE op_type='evolve'"
        ).fetchone()[0])
        # 6 个扰动向量中必有 momentum/valuation 异号者 → mean(perturbed)<base
        assert detail["decay"] > 0.0
    finally:
        conn.close()


def test_promotion_gates_truth_table() -> None:
    ok_all = check_promotion([0.5] * 20, decay=0.1, finals_pass=True)
    assert ok_all.ok and not ok_all.reasons
    low_win_rate = check_promotion([1.0] * 10 + [-1.0] * 10, decay=0.1, finals_pass=True)
    assert not low_win_rate.ok and any("胜率" in r for r in low_win_rate.reasons)
    # 坏尾样本须让 ≥25% 的改进落在 P25_MIN=-1.0 之下才能触发该门槛：
    # 原稿 17+3 仅 15% 负值，线性插值 p25=2.0 无法触发，故改为 14+6（胜率
    # 0.70、中位 2.0 仍通过，仅 25 分位 = -3.0 触发，单门独中）
    bad_tail = check_promotion([2.0] * 14 + [-3.0] * 6, decay=0.1, finals_pass=True)
    assert not bad_tail.ok and any("25 分位" in r for r in bad_tail.reasons)
    bad_decay = check_promotion([0.5] * 20, decay=0.5, finals_pass=True)
    assert not bad_decay.ok and any("扰动" in r for r in bad_decay.reasons)
    no_finals = check_promotion([0.5] * 20, decay=0.1, finals_pass=False)
    assert not no_finals.ok and any("决赛" in r for r in no_finals.reasons)


# ── evolve CLI contract (mocked end-to-end, no DB/network) ──


_EVO_ARGS = [
    "evolve", "--participant", "davis_balanced",
    "--start", "20230101", "--end", "20251231", "--seed", "3",
]


def _patch_evolve_env(monkeypatch: pytest.MonkeyPatch, n_days: int = 900) -> sqlite3.Connection:
    """Patch every external touchpoint of the cli evolve branch.

    - ledger.open_db → in-memory sqlite（ensure_tables 已建表）
    - TushareClient → MagicMock 类（cli 分支在函数内 import，运行时取属性）
    - judge.trading_calendar → n_days 个合成交易日（默认 900，高于 828 守卫）
    - evolution.build_score_fn → 确定性假分（只认 momentum_weight，缺省 0.2）
    """
    conn = sqlite3.connect(":memory:")
    ledger_mod.ensure_tables(conn)
    monkeypatch.setattr(ledger_mod, "open_db", lambda: conn)
    monkeypatch.setattr(tushare_client_mod, "TushareClient", MagicMock)
    calendar = [date(2023, 1, 2) + timedelta(days=i) for i in range(n_days)]
    monkeypatch.setattr(judge_mod, "trading_calendar", lambda client, start, end: calendar)
    monkeypatch.setattr(
        evolution_mod, "build_score_fn",
        lambda judge, participant: (
            lambda params, ranges: float(params.get("momentum_weight", 0.2))
        ),
    )
    return conn


def test_evolve_cli_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _patch_evolve_env(monkeypatch, n_days=900)
    try:
        rc = main(list(_EVO_ARGS))
        assert rc in (0, 2)
        rows = conn.execute(
            "SELECT detail FROM tournament_ledger WHERE op_type='evolve'"
        ).fetchall()
        assert len(rows) == 1  # 恰好追加一条 evolve 台账记录
        detail = json.loads(rows[0][0])
        assert set(detail.keys()) == {
            "improvements", "decay", "finals_pass", "ok", "reasons", "best_params",
        }
        assert (rc == 0) == (detail["ok"] is True)  # 退出码与晋升判定一致
    finally:
        conn.close()


def test_evolve_cli_seed_defaults_fill_empty_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    """I2：空预设参与者（davis_balanced）的 incumbent 由种子补全全部 8 键。"""
    from davis_analyzer.tournament.evolution import DAVIS_SEED_DEFAULTS
    from davis_analyzer.tournament.genome import DAVIS_GENOME

    conn = _patch_evolve_env(monkeypatch)
    try:
        main(list(_EVO_ARGS))
        detail = json.loads(conn.execute(
            "SELECT detail FROM tournament_ledger WHERE op_type='evolve'"
        ).fetchone()[0])
        best = detail["best_params"]
        assert set(best) == set(DAVIS_SEED_DEFAULTS)  # mutate 保留种子全集
        for k, v in best.items():
            lo, hi = DAVIS_GENOME.bounds()[k]
            assert lo <= v <= hi
    finally:
        conn.close()


def test_evolve_cli_quota_rejection(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    conn = _patch_evolve_env(monkeypatch)
    try:
        for _ in range(4):  # TOURNAMENT_CAMPAIGNS_PER_YEAR
            append_record(conn, LedgerRecord(
                op_type="evolve", run_date=date.today(),
                participants=[("davis_balanced", "v1")],
                params_version="quota-seed", oos_windows_used=1, detail={},
            ))
        rc = main(list(_EVO_ARGS))
        assert rc == 1
        n_rows = conn.execute(
            "SELECT COUNT(*) FROM tournament_ledger WHERE op_type='evolve'"
        ).fetchone()[0]
        assert n_rows == 4  # 限额拒绝后不新增台账记录
        assert "限额" in capsys.readouterr().out
    finally:
        conn.close()


def test_evolve_cli_short_calendar_guard(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    # I4 守卫阈值（常量表达式推导，=828）：差一天也必须拒绝且不落台账
    from davis_analyzer import constants as C

    threshold = (
        C.TOURNAMENT_SEGMENTS_N
        * (C.TOURNAMENT_MIN_WINDOW_DAYS + C.TOURNAMENT_EMBARGO_DAYS)
        + C.TOURNAMENT_FINALS_WINDOW_DAYS
    )
    conn = _patch_evolve_env(monkeypatch, n_days=threshold - 1)
    try:
        rc = main(list(_EVO_ARGS))
        assert rc == 1
        assert conn.execute("SELECT COUNT(*) FROM tournament_ledger").fetchone()[0] == 0
        assert "日历长度不足" in capsys.readouterr().out
    finally:
        conn.close()
