# 策略锦标赛/参数进化模块（tournament）实施计划 — Phase 1–3

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 davis_analyzer 中新建 `tournament` 子包：统一裁判的多模块锦标赛（L1）+ 仅参数调优的进化引擎与冠军存档（L2），全流程 OOS 台账审计。

**Architecture:** 独立子包 + 模块级 CLI（仿 `paper_trading`/`limitup` 先例）。各模块经 `ModuleAdapter` 协议归一化，裁判复用 `run_backtest`/`compute_performance`，进化只经基因声明验证过的参数通道触碰参赛者；新增两张 SQLite 表（`tournament_ledger`/`tournament_champions`）落在共享 `market_data.db`，不改 stockhot 源码。

**Tech Stack:** Python 3.11+ / pandas / numpy / sqlite3 / loguru / pytest。规格见 `docs/superpowers/specs/2026-08-17-strategy-tournament-design.md`（v1.1）。

## Global Constraints（每个任务隐含遵守）

- 从父仓库根目录 `/home/leo/Projects/CodeAgentDashboard/` 运行一切命令；Python 用 `.venv/bin/python`；测试统一 `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_<x>.py -v`。
- 每个 py 文件顶部 `from __future__ import annotations`；类型注解完整（PEP 604 联合类型）；`snake_case` 函数 / `PascalCase` 类；docstring 英文、金融术语中文；模块分隔注释 `# ── ... ──`。
- 日志只用 `from loguru import logger` + 花括号占位；`print` 仅允许出现在 `tournament/cli.py`。
- 数值计算沿用 pandas 生态 float 惯例（与 backtest.py 一致，本模块不引入 Decimal）。
- 不修改 `stockhot/` 任何源码；`tournament_ledger`/`tournament_champions` 由本模块 `CREATE TABLE IF NOT EXISTS` 自建。
- 不修改 `constants.py` 任何既有常量——只允许在文件末尾追加 `TOURNAMENT_*` 段与 `CHAMPION_PRESETS`；`SOP.md` 追加对应说明（权重单一真相源铁律，Task 1 建立、Task 12 扩展）。
- 日期约定：dataclass 与函数签名用 `datetime.date`；调 `TushareClient`/`market_regime` 时转 `YYYYMMDD` 字符串；ledger/champions 表内用 ISO `YYYY-MM-DD`。
- 提交信息：Conventional Commits 中文 scope，如 `feat(tournament): 实现裁判窗口调度`。
- conftest 在 `davis_analyzer/tests/conftest.py`（根目录 `tests/` 是 JS 测试，别碰）。
- Phase 4（连板/周期反转 adapter 接入）不在本计划——依赖那些模块落地后另出计划。

---

### Task 1: 包骨架 + 配置 + 冻结常量 + SOP 同步

**Files:**
- Create: `davis_analyzer/tournament/__init__.py`
- Create: `davis_analyzer/tournament/cli.py`（仅 `list` 子命令骨架）
- Create: `davis_analyzer/tournament/__main__.py`
- Modify: `davis_analyzer/config.py`（追加 2 行）
- Modify: `davis_analyzer/constants.py`（文件末尾追加 TOURNAMENT_* 段）
- Modify: `SOP.md`（末尾追加一节）
- Test: `davis_analyzer/tests/test_tournament_package.py`

**Interfaces:**
- Produces: `config.TOURNAMENT_REPORTS_DIR: Path`；`constants.TOURNAMENT_*` 全部冻结常量（后续任务按名引用，数值如下）；`constants.CHAMPION_PRESETS: dict[str, dict]`（初始空 dict，Task 12 使用）；`tournament.cli.main(argv: list[str] | None = None) -> int`。

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_tournament_package.py`：

```python
"""tournament 包骨架、配置与冻结常量测试。"""

from __future__ import annotations

from pathlib import Path

from davis_analyzer import config, constants
from davis_analyzer.tournament.cli import main


def test_package_importable() -> None:
    import davis_analyzer.tournament  # noqa: F401


def test_reports_dir_created() -> None:
    assert config.TOURNAMENT_REPORTS_DIR.exists()
    assert config.TOURNAMENT_REPORTS_DIR.is_dir()


def test_frozen_constants_present() -> None:
    assert constants.TOURNAMENT_EVAL_STEP_DAYS == 63
    assert constants.TOURNAMENT_MIN_WINDOW_DAYS == 40
    assert constants.TOURNAMENT_MIN_TRADES == 10
    assert constants.TOURNAMENT_TRAILING_WINDOWS == 4
    assert constants.TOURNAMENT_TRAILING_HALF_LIFE == 2.0
    assert constants.TOURNAMENT_DRAWDOWN_PENALTY == 0.1
    assert constants.TOURNAMENT_COMPOSITE_WEIGHTS == {"trailing": 0.6, "regime_match": 0.4}
    assert constants.TOURNAMENT_ALLOCATOR_TAU == 0.5
    assert constants.TOURNAMENT_WEIGHT_BOUNDS == (0.05, 0.50)
    assert constants.TOURNAMENT_SEGMENTS_N == 10
    assert constants.TOURNAMENT_SEGMENTS_K == 3
    assert constants.TOURNAMENT_EMBARGO_DAYS == 5
    assert constants.TOURNAMENT_SEGMENT_DRAWS == 20
    assert constants.TOURNAMENT_PROMO_WIN_RATE == 0.65
    assert constants.TOURNAMENT_PROMO_MEDIAN_MIN == 0.0
    assert constants.TOURNAMENT_PROMO_P25_MIN == -1.0
    assert constants.TOURNAMENT_PERTURB_PCT == 0.20
    assert constants.TOURNAMENT_PERTURB_MAX_DECAY == 0.30
    assert constants.TOURNAMENT_POPULATION == 16
    assert constants.TOURNAMENT_GENERATIONS == 10
    assert constants.TOURNAMENT_MUTATION_SIGMA == 0.15
    assert constants.TOURNAMENT_SURVIVAL_FRAC == 0.25
    assert constants.TOURNAMENT_CAMPAIGNS_PER_YEAR == 4
    assert constants.TOURNAMENT_FINALS_WINDOW_DAYS == 378
    assert constants.TOURNAMENT_CHAMPION_SLOTS == 2
    assert constants.TOURNAMENT_DAVIS_PRESETS["davis_momentum_tilt"]["momentum_weight"] == 0.45
    assert constants.CHAMPION_PRESETS == {}


def test_cli_list_smoke(capsys) -> None:
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "参赛者" in out
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_package.py -v`
Expected: FAIL（`ModuleNotFoundError: davis_analyzer.tournament`）

- [ ] **Step 3: 实现最小代码**

`davis_analyzer/tournament/__init__.py`：

```python
"""策略锦标赛/参数进化模块（Phase 1-3：裁判/分配/进化与冠军存档）."""

from __future__ import annotations
```

`davis_analyzer/tournament/__main__.py`：

```python
"""python -m davis_analyzer.tournament 入口."""

from __future__ import annotations

from davis_analyzer.tournament.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

`davis_analyzer/tournament/cli.py`：

```python
"""Tournament CLI (argparse, mirrors paper_trading style)."""

from __future__ import annotations

import argparse


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tournament", description="策略锦标赛/参数进化")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="列出参赛者")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "list":
        print("参赛者（Phase 1 Task 3 起注册）：暂无")
        return 0
    return 1
```

`davis_analyzer/config.py` 在 `LIMITUP_REPORTS_DIR.mkdir(...)` 之后追加：

```python
TOURNAMENT_REPORTS_DIR = PROJECT_ROOT / "davis_analyzer" / "tournament" / "reports"

TOURNAMENT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
```

`davis_analyzer/constants.py` 文件末尾追加（不动任何既有内容）：

```python
# ── Tournament (策略锦标赛) frozen parameters — v1, 单一真相源，修改须 bump 版本并记入 SOP ──

TOURNAMENT_EVAL_STEP_DAYS: int = 63        # L1 评估窗口长度（交易日）
TOURNAMENT_MIN_WINDOW_DAYS: int = 40       # 窗口最小样本门槛（交易日）
TOURNAMENT_MIN_TRADES: int = 10            # 窗口最小成交笔数门槛（passive 豁免）
TOURNAMENT_TRAILING_WINDOWS: int = 4       # trailing_score 回看窗口数
TOURNAMENT_TRAILING_HALF_LIFE: float = 2.0  # 窗口权重半衰期（窗口数）
TOURNAMENT_DRAWDOWN_PENALTY: float = 0.1   # 窗口表现分回撤惩罚系数
TOURNAMENT_COMPOSITE_WEIGHTS: dict[str, float] = {"trailing": 0.6, "regime_match": 0.4}
TOURNAMENT_ALLOCATOR_TAU: float = 0.5      # softmax 温度
TOURNAMENT_WEIGHT_BOUNDS: tuple[float, float] = (0.05, 0.50)  # 模块权重夹限
TOURNAMENT_SEGMENTS_N: int = 10            # CPCV-lite 总段数
TOURNAMENT_SEGMENTS_K: int = 3             # 每次抽取的验证段数
TOURNAMENT_EMBARGO_DAYS: int = 5           # 段边界隔离带（交易日）
TOURNAMENT_SEGMENT_DRAWS: int = 20         # 随机抽取次数
TOURNAMENT_PROMO_WIN_RATE: float = 0.65    # 晋升门槛：随机段胜率
TOURNAMENT_PROMO_MEDIAN_MIN: float = 0.0   # 晋升门槛：中位改进下限
TOURNAMENT_PROMO_P25_MIN: float = -1.0     # 晋升门槛：25 分位改进下限
TOURNAMENT_PERTURB_PCT: float = 0.20       # 参数扰动幅度
TOURNAMENT_PERTURB_MAX_DECAY: float = 0.30 # 扰动后性能衰减上限
TOURNAMENT_POPULATION: int = 16            # 进化种群规模
TOURNAMENT_GENERATIONS: int = 10           # 每战役最大代数
TOURNAMENT_MUTATION_SIGMA: float = 0.15    # 变异强度（区间宽度比例）
TOURNAMENT_SURVIVAL_FRAC: float = 0.25     # 每代存活比例
TOURNAMENT_CAMPAIGNS_PER_YEAR: int = 4     # 进化战役年度限额
TOURNAMENT_FINALS_WINDOW_DAYS: int = 378   # 决赛窗口长度（交易日，约 18 个月）
TOURNAMENT_CHAMPION_SLOTS: int = 2         # 每模块×regime 槽历史冠军数上限

# davis 参赛预设（冻结参数点；空 dict = FactorConfig 默认值）
TOURNAMENT_DAVIS_PRESETS: dict[str, dict[str, float]] = {
    "davis_balanced": {},
    "davis_momentum_tilt": {
        "momentum_weight": 0.45, "valuation_weight": 0.10,
        "prosperity_weight": 0.25, "distress_weight": 0.10,
    },
    "davis_valuation_tilt": {
        "momentum_weight": 0.10, "valuation_weight": 0.45,
        "prosperity_weight": 0.20, "distress_weight": 0.15,
    },
}

# 现任冠军参数（部署态；由 champions deploy 流程人工同步，初始为空）
CHAMPION_PRESETS: dict[str, dict[str, float]] = {}
```

`SOP.md` 末尾追加：

```markdown
## 策略锦标赛（tournament）参数 — v1（2026-08-17 冻结）

评分权重 `TOURNAMENT_COMPOSITE_WEIGHTS`（trailing 0.6 / regime_match 0.4）、分配温度
`TOURNAMENT_ALLOCATOR_TAU`（0.5）与权重夹限 `TOURNAMENT_WEIGHT_BOUNDS`（0.05-0.50）为裁判
参数，**永不可被进化触碰**（反环化规则）；晋升门槛与进化参数见 `constants.py` TOURNAMENT_*
段。修改任何 TOURNAMENT_* 常量必须 bump SOP 版本号并记入 tournament_ledger。
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_package.py davis_analyzer/tests/test_doc_consistency.py -v`
Expected: 全部 PASS（doc consistency 回归确认未破坏）

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/tournament/ davis_analyzer/config.py davis_analyzer/constants.py SOP.md davis_analyzer/tests/test_tournament_package.py
git commit -m "feat(tournament): 包骨架+报告目录+冻结常量+SOP 同步"
```

---

### Task 2: genome.py — 基因声明与参数通道验证（D8 的代码级保证）

**Files:**
- Create: `davis_analyzer/tournament/genome.py`
- Test: `davis_analyzer/tests/test_tournament_genome.py`

**Interfaces:**
- Produces: `ParamSpec(name: str, lo: float, hi: float, kind: str, choices: list[float] | None = None)`（kind ∈ `"weight" | "float" | "choice"`）；`Genome(specs: list[ParamSpec])`，方法 `validate(params: dict[str, float | int]) -> None`（未声明键抛 `KeyError`，越界抛 `ValueError`，choice 非法值抛 `ValueError`）、`bounds() -> dict[str, tuple[float, float]]`、`names() -> list[str]`；模块级 `DAVIS_GENOME: Genome`。

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_tournament_genome.py`：

```python
"""genome 基因声明与参数验证测试。"""

from __future__ import annotations

import pytest

from davis_analyzer.tournament.genome import DAVIS_GENOME, Genome, ParamSpec


def _g() -> Genome:
    return Genome([
        ParamSpec("momentum_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("top_n", 5, 20, kind="choice", choices=[5, 10, 15, 20]),
    ])


def test_validate_accepts_declared() -> None:
    _g().validate({"momentum_weight": 0.4, "top_n": 10})  # no raise


def test_validate_rejects_undeclared_key() -> None:
    with pytest.raises(KeyError, match="logic_change"):
        _g().validate({"momentum_weight": 0.4, "logic_change": 1})


def test_validate_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="momentum_weight"):
        _g().validate({"momentum_weight": 1.5, "top_n": 10})


def test_validate_rejects_bad_choice() -> None:
    with pytest.raises(ValueError, match="top_n"):
        _g().validate({"momentum_weight": 0.4, "top_n": 7})


def test_davis_genome_covers_factor_and_engine_knobs() -> None:
    names = set(DAVIS_GENOME.names())
    assert {"momentum_weight", "valuation_weight", "prosperity_weight",
            "distress_weight", "northbound_weight", "research_weight",
            "top_n", "frequency"} <= names
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_genome.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现最小代码**

```python
"""Genome declarations — the ONLY channel evolution can touch (spec D8).

Parameters not declared in a Genome are structurally unreachable by the
parameter channel: the adapter validates every incoming key against its
Genome and raises on anything undeclared (logic can never ride along).
"""

from __future__ import annotations


# ── parameter specification ──


from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParamSpec:
    """One tunable parameter: name, bounds and kind.

    kind: "weight" (0-1 factor weight, normalised by the engine later),
    "float" (bounded continuous), "choice" (discrete allowed values).
    """

    name: str
    lo: float
    hi: float
    kind: str = "float"
    choices: list[float] | None = None  # required when kind == "choice"


class Genome:
    """Immutable set of declared tunable parameters for one participant."""

    def __init__(self, specs: list[ParamSpec]) -> None:
        self._specs = {s.name: s for s in specs}
        if len(self._specs) != len(specs):
            raise ValueError("duplicate ParamSpec names")

    def names(self) -> list[str]:
        return list(self._specs.keys())

    def bounds(self) -> dict[str, tuple[float, float]]:
        return {n: (s.lo, s.hi) for n, s in self._specs.items()}

    def spec(self, name: str) -> ParamSpec:
        return self._specs[name]

    def validate(self, params: dict[str, float | int]) -> None:
        """Raise KeyError for undeclared keys, ValueError for bad values."""
        for name, value in params.items():
            if name not in self._specs:
                raise KeyError(
                    f"undeclared parameter {name!r} — logic structure is "
                    f"frozen; declare it in the Genome first (spec D8)"
                )
            spec = self._specs[name]
            v = float(value)
            if spec.kind == "choice":
                if spec.choices is None or v not in [float(c) for c in spec.choices]:
                    raise ValueError(f"{name}={value} not in choices {spec.choices}")
            elif not (spec.lo <= v <= spec.hi):
                raise ValueError(f"{name}={value} outside [{spec.lo}, {spec.hi}]")


# ── davis participant genome (frozen v1) ──


DAVIS_GENOME = Genome(
    [
        ParamSpec("momentum_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("valuation_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("prosperity_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("distress_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("northbound_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("research_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("top_n", 5, 20, kind="choice", choices=[5, 10, 15, 20]),
        ParamSpec("frequency", 5, 20, kind="choice", choices=[5, 10, 20]),
    ]
)
```

（`import` 顺序整理为 dataclasses 在模块 docstring 后、类定义前，保持与项目风格一致。）

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_genome.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/tournament/genome.py davis_analyzer/tests/test_tournament_genome.py
git commit -m "feat(tournament): 基因声明与参数通道验证（D8 代码级保证）"
```

---

### Task 3: adapters.py — RunResult/协议/指数基准/davis 预设

**Files:**
- Create: `davis_analyzer/tournament/adapters.py`
- Modify: `davis_analyzer/tournament/cli.py`（`list` 列出真实参赛者）
- Test: `davis_analyzer/tests/test_tournament_adapters.py`

**Interfaces:**
- Consumes: `genome.Genome.validate`、`constants.TOURNAMENT_DAVIS_PRESETS/CHAMPION_PRESETS`、`backtest.run_backtest`、`backtest.BacktestConfig`、`backtest.EquitySnapshot/Trade`、`backtest_factors.FactorConfig`。
- Produces: `RunResult(equity_curve: list[EquitySnapshot], trades: list[Trade], assumptions: dict[str, str])`；`ModuleAdapter` Protocol（`name/horizon/version` 属性 + `run_window(client, start: date, end: date, params: dict | None = None) -> RunResult | None`）；`IndexBenchmarkAdapter(index_code: str = "000001.SH")`；`DavisPresetAdapter(name: str, params: dict, universe: list[str] | None = None, genome: Genome = DAVIS_GENOME)`；`default_participants() -> list[ModuleAdapter]`；`stats_from_run(run: RunResult, start: date, end: date) -> PerformanceStats`（构造伪 BacktestResult 复用 `compute_performance`）。

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_tournament_adapters.py`：

```python
"""adapters 归一化层测试。"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from davis_analyzer.backtest_report import PerformanceStats
from davis_analyzer.tournament.adapters import (
    DavisPresetAdapter,
    IndexBenchmarkAdapter,
    RunResult,
    default_participants,
    stats_from_run,
)


def _daily_df(days: int = 50, base: float = 10.0) -> pd.DataFrame:
    d0 = date(2024, 1, 2)
    return pd.DataFrame({
        "trade_date": [(d0 + timedelta(days=i)).strftime("%Y%m%d") for i in range(days)],
        "close": [base + i * 0.1 for i in range(days)],
    })


def test_index_benchmark_builds_curve(mock_client) -> None:
    mock_client.get_daily_prices.return_value = _daily_df()
    adapter = IndexBenchmarkAdapter()
    run = adapter.run_window(mock_client, date(2024, 1, 2), date(2024, 3, 15))
    assert run is not None
    assert len(run.equity_curve) == 50
    assert run.trades == []
    assert run.assumptions["cost_model"] == "buy_and_hold_no_cost"


def test_index_benchmark_none_when_no_data(mock_client) -> None:
    mock_client.get_daily_prices.return_value = pd.DataFrame()
    assert IndexBenchmarkAdapter().run_window(mock_client, date(2024, 1, 2), date(2024, 3, 15)) is None


def test_stats_from_run_roundtrip() -> None:
    from davis_analyzer.backtest import EquitySnapshot
    curve = [EquitySnapshot(date=date(2024, 1, i + 1), equity=1_000_000.0 * (1 + 0.001 * i),
                            cash=0.0, positions_value=1_000_000.0) for i in range(30)]
    stats = stats_from_run(RunResult(curve, [], {}), date(2024, 1, 1), date(2024, 1, 30))
    assert isinstance(stats, PerformanceStats)
    assert stats.total_return_pct == pytest.approx((1.029 * 1_000_000 / 1_000_000 - 1) * 100, abs=0.5)


def test_davis_adapter_maps_params(monkeypatch, mock_client) -> None:
    captured: dict = {}
    def fake_run_backtest(cfg, client):
        captured["cfg"] = cfg
        from davis_analyzer.backtest import BacktestResult, EquitySnapshot
        curve = [EquitySnapshot(date=date(2024, 1, i + 1), equity=1_000_000.0,
                                cash=1_000_000.0, positions_value=0.0) for i in range(45)]
        return BacktestResult(config=cfg, equity_curve=curve)

    monkeypatch.setattr("davis_analyzer.tournament.adapters.run_backtest", fake_run_backtest)
    adapter = DavisPresetAdapter("davis_momentum_tilt", {"momentum_weight": 0.45, "top_n": 15})
    run = adapter.run_window(mock_client, date(2024, 1, 2), date(2024, 4, 1))
    assert run is not None
    fc = captured["cfg"].factor_config
    assert fc.momentum_weight == 0.45
    assert captured["cfg"].top_n == 15


def test_davis_adapter_rejects_undeclared_param(monkeypatch, mock_client) -> None:
    adapter = DavisPresetAdapter("davis_balanced", {})
    with pytest.raises(KeyError):
        adapter.run_window(mock_client, date(2024, 1, 2), date(2024, 4, 1),
                           params={"hold_days": 3})


def test_davis_adapter_none_on_empty_curve(monkeypatch, mock_client) -> None:
    from davis_analyzer.backtest import BacktestConfig, BacktestResult
    monkeypatch.setattr(
        "davis_analyzer.tournament.adapters.run_backtest",
        lambda cfg, client: BacktestResult(config=BacktestConfig(
            start_date=cfg.start_date, end_date=cfg.end_date)),
    )
    adapter = DavisPresetAdapter("davis_balanced", {})
    assert adapter.run_window(mock_client, date(2024, 1, 2), date(2024, 4, 1)) is None


def test_default_participants_registry() -> None:
    names = [a.name for a in default_participants()]
    assert "davis_balanced" in names and "davis_valuation_tilt" in names
    assert "benchmark_sse" in names
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_adapters.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现最小代码**

`davis_analyzer/tournament/adapters.py`：

```python
"""ModuleAdapter protocol — normalisation layer between engines and judge.

Each participant exposes one windowed run interface; differences between
periodic-rebalance (davis) and passive benchmarks are absorbed here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd

from davis_analyzer.backtest import (
    BacktestConfig,
    BacktestResult,
    EquitySnapshot,
    Trade,
    run_backtest,
)
from davis_analyzer.backtest_factors import FactorConfig
from davis_analyzer.backtest_report import PerformanceStats, compute_performance
from davis_analyzer.constants import CHAMPION_PRESETS, TOURNAMENT_DAVIS_PRESETS
from davis_analyzer.tournament.genome import DAVIS_GENOME, Genome
from davis_analyzer.tushare_client import TushareClient


# ── normalised run result ──


@dataclass
class RunResult:
    """One participant's result inside a single evaluation window."""

    equity_curve: list[EquitySnapshot]
    trades: list[Trade]
    assumptions: dict[str, str]


def stats_from_run(run: RunResult, start: date, end: date) -> PerformanceStats:
    """Reuse compute_performance via a pseudo BacktestResult."""
    pseudo = BacktestResult(
        config=BacktestConfig(start_date=start, end_date=end),
        trades=run.trades,
        equity_curve=run.equity_curve,
    )
    return compute_performance(pseudo)


# ── adapter protocol ──


@runtime_checkable
class ModuleAdapter(Protocol):
    name: str
    horizon: str  # "periodic" | "event" | "passive"
    version: str

    def run_window(
        self, client: TushareClient, start: date, end: date,
        params: dict[str, float | int] | None = None,
    ) -> RunResult | None: ...


def _params_fingerprint(params: dict) -> str:
    return hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]


# ── passive benchmark ──


class IndexBenchmarkAdapter:
    """Buy-and-hold an index (no cost, no trades)."""

    horizon = "passive"

    def __init__(self, index_code: str = "000001.SH") -> None:
        self.index_code = index_code
        self.name = "benchmark_sse" if index_code == "000001.SH" else f"benchmark_{index_code.split('.')[0]}"
        self.version = "v0"

    def run_window(
        self, client: TushareClient, start: date, end: date,
        params: dict[str, float | int] | None = None,
    ) -> RunResult | None:
        df = client.get_daily_prices(
            self.index_code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        )
        if df is None or df.empty:
            return None
        df = df.sort_values("trade_date")
        first_close = float(df.iloc[0]["close"])
        curve: list[EquitySnapshot] = []
        for _, row in df.iterrows():
            d = pd.to_datetime(row["trade_date"], format="%Y%m%d").date()
            equity = 1_000_000.0 * float(row["close"]) / first_close
            curve.append(EquitySnapshot(date=d, equity=equity, cash=0.0, positions_value=equity))
        return RunResult(
            equity_curve=curve, trades=[],
            assumptions={"cost_model": "buy_and_hold_no_cost"},
        )


# ── davis periodic presets ──


_FACTOR_KEYS = {
    "momentum_weight", "valuation_weight", "prosperity_weight",
    "distress_weight", "northbound_weight", "research_weight",
}
_ENGINE_KEYS = {"top_n", "frequency"}


class DavisPresetAdapter:
    """Wrap run_backtest with a frozen parameter point (spec §5.1)."""

    horizon = "periodic"

    def __init__(
        self, name: str, params: dict[str, float | int],
        universe: list[str] | None = None, genome: Genome = DAVIS_GENOME,
    ) -> None:
        self.name = name
        self._params = dict(params)
        self._genome = genome
        self._universe = universe
        self.version = f"v{_params_fingerprint(self._params)}"

    def run_window(
        self, client: TushareClient, start: date, end: date,
        params: dict[str, float | int] | None = None,
    ) -> RunResult | None:
        merged = {**self._params, **(params or {})}
        self._genome.validate(merged)  # D8: undeclared keys can never pass
        factor_kwargs = {k: float(v) for k, v in merged.items() if k in _FACTOR_KEYS}
        engine_kwargs = {k: int(v) for k, v in merged.items() if k in _ENGINE_KEYS}
        cfg = BacktestConfig(
            start_date=start, end_date=end, universe=self._universe,
            factor_config=FactorConfig(**factor_kwargs), **engine_kwargs,
        )
        result = run_backtest(cfg, client)
        if not result.equity_curve:
            return None
        return RunResult(
            equity_curve=result.equity_curve, trades=result.trades,
            assumptions={"cost_model": "commission_2.5bps_stamp_10bps"},
        )


def default_participants() -> list[ModuleAdapter]:
    """Frozen registry: davis presets + index benchmark + deployed champions."""
    participants: list[ModuleAdapter] = [
        DavisPresetAdapter(name, dict(params))
        for name, params in TOURNAMENT_DAVIS_PRESETS.items()
    ]
    participants.append(IndexBenchmarkAdapter())
    for name, params in CHAMPION_PRESETS.items():
        participants.append(DavisPresetAdapter(f"champion_{name}", dict(params)))
    return participants
```

`cli.py` 的 `list` 分支改为：

```python
    if args.command == "list":
        from davis_analyzer.tournament.adapters import default_participants
        for p in default_participants():
            print(f"参赛者: {p.name:<24} horizon={p.horizon:<8} version={p.version}")
        return 0
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_adapters.py davis_analyzer/tests/test_tournament_package.py -v`
Expected: PASS（含 cli list 冒烟）

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/tournament/adapters.py davis_analyzer/tournament/cli.py davis_analyzer/tests/test_tournament_adapters.py
git commit -m "feat(tournament): Adapter 协议+指数基准+davis 预设归一化层"
```

---

### Task 4: judge.py — 裁判（窗口调度/防前视/N-A 门槛/regime 切片）

**Files:**
- Create: `davis_analyzer/tournament/judge.py`
- Test: `davis_analyzer/tests/test_tournament_judge.py`

**Interfaces:**
- Consumes: `adapters.ModuleAdapter/RunResult/stats_from_run`；`constants.TOURNAMENT_EVAL_STEP_DAYS/MIN_WINDOW_DAYS/MIN_TRADES`。
- Produces: `WindowReport(participant: str, start: date, end: date, stats: PerformanceStats | None, regime: str | None, na_reason: str | None)`；`JudgeHarness(adapters: list[ModuleAdapter], client: TushareClient, regime_fn: Callable[[str], str] | None = None)`，方法 `build_windows(calendar: list[date]) -> list[tuple[date, date]]`、`evaluate_window(start: date, end: date, params_by_participant: dict[str, dict] | None = None) -> dict[str, WindowReport]`、`snapshot(as_of: date, calendar: list[date]) -> dict[tuple[date, date], dict[str, WindowReport]]`（只评估 end ≤ as_of 的窗口）；`trading_calendar(start: date, end: date) -> list[date]`（模块级函数，锚定 `000001.SH` 缓存推导）。
- `regime_fn` 默认懒导入 `market_regime.get_market_regime_with_confirm`，注入用于测试。

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_tournament_judge.py`：

```python
"""judge 裁判铁律测试（防前视/样本门槛/regime 切片）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from davis_analyzer.backtest import EquitySnapshot, Trade
from davis_analyzer.tournament.adapters import RunResult
from davis_analyzer.tournament.judge import JudgeHarness, WindowReport


def _cal(n: int = 200) -> list[date]:
    d0 = date(2023, 1, 2)
    return [d0 + timedelta(days=i) for i in range(n)]


@dataclass
class FakeAdapter:
    name: str = "fake"
    horizon: str = "periodic"
    version: str = "v0"
    curve_days: int = 63
    n_trades: int = 20
    calls: list[tuple[date, date]] = field(default_factory=list)

    def run_window(self, client, start, end, params=None):
        self.calls.append((start, end))
        curve = [EquitySnapshot(date=start + timedelta(days=i), equity=1_000_000.0 + i * 100.0,
                                cash=0.0, positions_value=1_000_000.0)
                 for i in range(self.curve_days)]
        trades = [Trade(signal_date=start, exec_date=start, ts_code="X.SZ", action="BUY",
                        price=1.0, shares=100, amount=100.0, cost=0.0)] * self.n_trades
        return RunResult(curve, trades, {"cost_model": "fake"})


def _regime_fn(trade_date: str) -> str:
    return "risk_on"


def test_build_windows_step_and_coverage() -> None:
    judge = JudgeHarness([], client=None, regime_fn=_regime_fn)
    windows = judge.build_windows(_cal(200))
    assert windows[0][0] == _cal(200)[0]
    assert windows[-1][1] <= _cal(200)[-1]
    assert all((end - start).days >= 60 for start, end in windows[:2])


def test_snapshot_no_lookahead() -> None:
    """核心铁律：as_of 时点只允许看到 end <= as_of 的窗口."""
    adapter = FakeAdapter()
    judge = JudgeHarness([adapter], client=None, regime_fn=_regime_fn)
    cal = _cal(200)
    as_of = cal[100]
    judge.snapshot(as_of, cal)
    assert adapter.calls, "snapshot should have evaluated windows"
    assert all(end <= as_of for _, end in adapter.calls)


def test_min_sample_thresholds_na() -> None:
    short = FakeAdapter(name="short", curve_days=10)
    notrading = FakeAdapter(name="notrading", n_trades=0)
    passive = FakeAdapter(name="passive_bench", horizon="passive", n_trades=0)
    judge = JudgeHarness([short, notrading, passive], client=None, regime_fn=_regime_fn)
    cal = _cal(80)
    reports = judge.evaluate_window(cal[0], cal[62])
    assert reports["short"].stats is None and "交易日" in reports["short"].na_reason
    assert reports["notrading"].stats is None and "成交笔数" in reports["notrading"].na_reason
    assert reports["passive_bench"].stats is not None  # passive 豁免笔数门槛


def test_regime_label_attached() -> None:
    adapter = FakeAdapter()
    judge = JudgeHarness([adapter], client=None, regime_fn=_regime_fn)
    cal = _cal(80)
    reports = judge.evaluate_window(cal[0], cal[62])
    assert reports["fake"].regime == "risk_on"
    assert isinstance(reports["fake"], WindowReport)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_judge.py -v`
Expected: FAIL

- [ ] **Step 3: 实现最小代码**

`davis_analyzer/tournament/judge.py`：

```python
"""JudgeHarness — unified, point-in-time evaluation of participants.

Hard rules (spec §5.2): rolling windows, independent per-window runs,
minimum-sample gates, regime slicing, no-lookahead by construction (the
judge alone owns window boundaries; adapters never see the schedule).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd
from loguru import logger

from davis_analyzer.constants import (
    TOURNAMENT_EVAL_STEP_DAYS,
    TOURNAMENT_MIN_TRADES,
    TOURNAMENT_MIN_WINDOW_DAYS,
)
from davis_analyzer.backtest_report import PerformanceStats
from davis_analyzer.tournament.adapters import ModuleAdapter, stats_from_run
from davis_analyzer.tushare_client import TushareClient

RegimeFn = Callable[[str], str]


@dataclass
class WindowReport:
    """One participant's outcome in one window (N/A when gates fail)."""

    participant: str
    start: date
    end: date
    stats: PerformanceStats | None
    regime: str | None
    na_reason: str | None


def _default_regime_fn(trade_date: str) -> str:
    from davis_analyzer.market_regime import get_market_regime_with_confirm
    return get_market_regime_with_confirm(trade_date)


def trading_calendar(client: TushareClient, start: date, end: date,
                     anchor: str = "000001.SH") -> list[date]:
    """Derive trading dates from the anchor's cached prices (project rule)."""
    df = client.get_daily_prices(anchor, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    if df is None or df.empty:
        raise ValueError(f"anchor {anchor} has no cached prices in window")
    return sorted(pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.date.unique().tolist())


class JudgeHarness:
    """Owns the window schedule; the only caller of adapter.run_window."""

    def __init__(
        self,
        adapters: list[ModuleAdapter],
        client: TushareClient | None,
        regime_fn: RegimeFn | None = None,
    ) -> None:
        self._adapters = adapters
        self._client = client
        self._regime_fn = regime_fn or _default_regime_fn

    def build_windows(self, calendar: list[date]) -> list[tuple[date, date]]:
        step = TOURNAMENT_EVAL_STEP_DAYS
        return [
            (calendar[i], calendar[min(i + step - 1, len(calendar) - 1)])
            for i in range(0, len(calendar), step)
        ]

    def evaluate_window(
        self, start: date, end: date,
        params_by_participant: dict[str, dict] | None = None,
    ) -> dict[str, WindowReport]:
        params_by_participant = params_by_participant or {}
        regime = self._regime_fn(end.strftime("%Y%m%d"))
        reports: dict[str, WindowReport] = {}
        for adapter in self._adapters:
            run = adapter.run_window(
                self._client, start, end,
                params=params_by_participant.get(adapter.name),
            )
            na: str | None = None
            stats: PerformanceStats | None = None
            if run is None:
                na = "窗口内数据不足"
            elif len(run.equity_curve) < TOURNAMENT_MIN_WINDOW_DAYS:
                na = f"窗口交易日 {len(run.equity_curve)} < {TOURNAMENT_MIN_WINDOW_DAYS}"
            elif adapter.horizon != "passive" and len(run.trades) < TOURNAMENT_MIN_TRADES:
                na = f"窗口成交笔数 {len(run.trades)} < {TOURNAMENT_MIN_TRADES}"
            else:
                stats = stats_from_run(run, start, end)
            reports[adapter.name] = WindowReport(
                participant=adapter.name, start=start, end=end,
                stats=stats, regime=regime, na_reason=na,
            )
            if na:
                logger.info("{} window [{} {}] N/A: {}", adapter.name, start, end, na)
        return reports

    def snapshot(
        self, as_of: date, calendar: list[date],
    ) -> dict[tuple[date, date], dict[str, WindowReport]]:
        """Evaluate ONLY windows fully realised before *as_of* (no lookahead)."""
        out: dict[tuple[date, date], dict[str, WindowReport]] = {}
        for start, end in self.build_windows(calendar):
            if end <= as_of:
                out[(start, end)] = self.evaluate_window(start, end)
        return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_judge.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/tournament/judge.py davis_analyzer/tests/test_tournament_judge.py
git commit -m "feat(tournament): 裁判 Harness（滚动窗口+防前视+样本门槛+regime 切片）"
```

---

### Task 5: scorecard.py — 评分（半衰期 trailing + regime 匹配 + 合成）

**Files:**
- Create: `davis_analyzer/tournament/scorecard.py`
- Test: `davis_analyzer/tests/test_tournament_scorecard.py`

**Interfaces:**
- Consumes: `judge.WindowReport`；`constants.TOURNAMENT_DRAWDOWN_PENALTY/TRAILING_WINDOWS/TRAILING_HALF_LIFE/COMPOSITE_WEIGHTS`。
- Produces: `window_performance(stats: PerformanceStats) -> float`；`trailing_score(perfs: list[float]) -> float | None`（按时间升序传入，取最近 4 个，半衰期加权；有效数 < 2 返回 None）；`regime_match_score(perfs_by_regime: dict[str, list[float]], current_regime: str) -> float | None`；`composite(trailing: float | None, regime_match: float | None) -> float | None`；`score_participant(reports: list[WindowReport], current_regime: str) -> CompositeScore(total: float | None, trailing: float | None, regime_match: float | None, valid_windows: int)`。

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_tournament_scorecard.py`：

```python
"""scorecard 评分公式测试（冻结初值）。"""

from __future__ import annotations

import pytest

from davis_analyzer.backtest_report import PerformanceStats
from davis_analyzer.tournament.scorecard import (
    composite,
    regime_match_score,
    score_participant,
    trailing_score,
    window_performance,
)


def _stats(sharpe: float = 1.5, drawdown: float = -12.0) -> PerformanceStats:
    return PerformanceStats(
        total_return_pct=10.0, annualized_return_pct=10.0, sharpe_ratio=sharpe,
        max_drawdown_pct=drawdown, win_rate_pct=50.0, turnover_per_rebalance=1.0,
        num_trades=20, num_rebalances=12, avg_holding_count=10.0, total_cost=100.0,
    )


def test_window_performance_formula() -> None:
    # 夏普 1.5 − 0.1 × |−12| = 0.3
    assert window_performance(_stats()) == pytest.approx(0.3)


def test_trailing_half_life_weights() -> None:
    # [2.0, 1.0, 0.5, 0.25] 半衰期 2 加权 → 1.189340
    assert trailing_score([2.0, 1.0, 0.5, 0.25]) == pytest.approx(1.189340, abs=1e-4)


def test_trailing_insufficient_windows_is_none() -> None:
    assert trailing_score([1.0]) is None
    assert trailing_score([]) is None


def test_regime_match_mean_of_matching_history() -> None:
    hist = {"risk_on": [1.0, 3.0], "risk_off": [0.0]}
    assert regime_match_score(hist, "risk_on") == pytest.approx(2.0)
    assert regime_match_score(hist, "unknown_regime") is None


def test_composite_weights() -> None:
    assert composite(1.2, 0.8) == pytest.approx(0.6 * 1.2 + 0.4 * 0.8)
    assert composite(None, 0.8) is None
    assert composite(1.2, None) is None


def test_score_participant_end_to_end() -> None:
    from datetime import date, timedelta
    from davis_analyzer.tournament.judge import WindowReport
    reports = [
        WindowReport("p", date(2024, 1, 1) + timedelta(days=63 * i),
                     date(2024, 3, 1) + timedelta(days=63 * i),
                     stats=_stats(sharpe=1.0 + 0.1 * i), regime="risk_on", na_reason=None)
        for i in range(3)
    ] + [WindowReport("p", date(2025, 1, 1), date(2025, 3, 1), stats=None,
                      regime="risk_off", na_reason="窗口成交笔数 5 < 10")]
    result = score_participant(reports, current_regime="risk_on")
    assert result.valid_windows == 3
    assert result.trailing is not None and result.regime_match is not None
    assert result.total is not None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_scorecard.py -v`
Expected: FAIL

- [ ] **Step 3: 实现最小代码**

```python
"""ScoreCard — frozen scoring formulas (spec §5.3, values in constants)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from davis_analyzer.backtest_report import PerformanceStats
from davis_analyzer.constants import (
    TOURNAMENT_COMPOSITE_WEIGHTS,
    TOURNAMENT_DRAWDOWN_PENALTY,
    TOURNAMENT_TRAILING_HALF_LIFE,
    TOURNAMENT_TRAILING_WINDOWS,
)
from davis_analyzer.tournament.judge import WindowReport


def window_performance(stats: PerformanceStats) -> float:
    """Sharpe − drawdown penalty (frozen v1 formula)."""
    return stats.sharpe_ratio - TOURNAMENT_DRAWDOWN_PENALTY * abs(stats.max_drawdown_pct)


def trailing_score(perfs: list[float]) -> float | None:
    """Half-life weighted mean of the most recent windows (chronological)."""
    recent = perfs[-TOURNAMENT_TRAILING_WINDOWS:]
    if len(recent) < 2:
        return None
    ages = list(range(len(recent)))[::-1]  # oldest first
    weights = [0.5 ** (age / TOURNAMENT_TRAILING_HALF_LIFE) for age in ages]
    total_w = sum(weights)
    return sum(p * w for p, w in zip(recent, weights)) / total_w


def regime_match_score(
    perfs_by_regime: dict[str, list[float]], current_regime: str
) -> float | None:
    """Plain mean of realised window performances under the current regime."""
    hist = perfs_by_regime.get(current_regime)
    if not hist:
        return None
    return sum(hist) / len(hist)


def composite(trailing: float | None, regime_match: float | None) -> float | None:
    if trailing is None or regime_match is None:
        return None
    w = TOURNAMENT_COMPOSITE_WEIGHTS
    return w["trailing"] * trailing + w["regime_match"] * regime_match


@dataclass
class CompositeScore:
    total: float | None
    trailing: float | None
    regime_match: float | None
    valid_windows: int


def score_participant(reports: list[WindowReport], current_regime: str) -> CompositeScore:
    """Score one participant from its realised WindowReports (chronological)."""
    valid = [r for r in reports if r.stats is not None]
    perfs = [window_performance(r.stats) for r in valid]
    by_regime: dict[str, list[float]] = {}
    for r, p in zip(valid, perfs):
        if r.regime:
            by_regime.setdefault(r.regime, []).append(p)
    trailing = trailing_score(perfs)
    match = regime_match_score(by_regime, current_regime)
    return CompositeScore(
        total=composite(trailing, match), trailing=trailing,
        regime_match=match, valid_windows=len(valid),
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_scorecard.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/tournament/scorecard.py davis_analyzer/tests/test_tournament_scorecard.py
git commit -m "feat(tournament): 评分卡（半衰期 trailing+regime 匹配+合成总分）"
```

---

### Task 6: report.py + `run` 子命令 — 第一份锦标赛报告

**Files:**
- Create: `davis_analyzer/tournament/report.py`
- Modify: `davis_analyzer/tournament/cli.py`（新增 `run` 子命令）
- Test: `davis_analyzer/tests/test_tournament_report.py`

**Interfaces:**
- Consumes: Task 3-5 全部产物；`config.TOURNAMENT_REPORTS_DIR`。
- Produces: `HONESTY_NOTE: str`（诚实边界固定文案）；`render_report(snapshot: dict[tuple[date, date], dict[str, WindowReport]], scores: dict[str, CompositeScore], current_regime: str) -> str`；`write_report(text: str, run_date: date, reports_dir: Path | None = None) -> Path`（文件名 `YYYY-MM-DD_tournament.md`）。CLI：`main(["run", "--start", "20230101", "--end", "20250630"])`。

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_tournament_report.py`：

```python
"""report 渲染与落盘测试。"""

from __future__ import annotations

from datetime import date

from davis_analyzer.tournament.judge import WindowReport
from davis_analyzer.tournament.report import HONESTY_NOTE, render_report, write_report
from davis_analyzer.tournament.scorecard import CompositeScore


def _snapshot():
    w = (date(2024, 1, 2), date(2024, 4, 8))
    return {w: {"davis_balanced": WindowReport(
        "davis_balanced", w[0], w[1], stats=None, regime="risk_on",
        na_reason="窗口成交笔数 5 < 10")}}


def test_render_contains_sections() -> None:
    text = render_report(
        _snapshot(),
        {"davis_balanced": CompositeScore(None, None, None, 0)},
        current_regime="risk_on",
    )
    assert "策略锦标赛报告" in text
    assert "表现矩阵" in text
    assert "N/A" in text
    assert "参考性结论" in text  # N/A 参赛者触发标注
    assert HONESTY_NOTE in text


def test_write_report(tmp_path) -> None:
    p = write_report("# t\n", date(2025, 6, 30), reports_dir=tmp_path)
    assert p.exists() and p.name == "2025-06-30_tournament.md"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_report.py -v`
Expected: FAIL

- [ ] **Step 3: 实现最小代码**

`davis_analyzer/tournament/report.py`：

```python
"""Markdown tournament report rendering (Phase 1: display-only)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from davis_analyzer.config import TOURNAMENT_REPORTS_DIR
from davis_analyzer.tournament.judge import WindowReport
from davis_analyzer.tournament.scorecard import CompositeScore

HONESTY_NOTE = (
    "> 诚实边界：随机段验证能消解单一路径运气，但不能凭空制造新 regime；"
    "A 股历史独立 regime 情节有限，所有结论为必要非充分证据，不构成实盘依据。"
)


def _window_table(snapshot: dict[tuple[date, date], dict[str, WindowReport]]) -> str:
    lines = ["| 窗口 | 参赛者 | 夏普 | 最大回撤 | 年化 | regime | N/A 原因 |",
             "|---|---|---|---|---|---|---|"]
    for (start, end), reports in sorted(snapshot.items()):
        for name, r in sorted(reports.items()):
            if r.stats is None:
                lines.append(f"| {start}→{end} | {name} | - | - | - | {r.regime} | {r.na_reason} |")
            else:
                lines.append(
                    f"| {start}→{end} | {name} | {r.stats.sharpe_ratio} | "
                    f"{r.stats.max_drawdown_pct}% | {r.stats.annualized_return_pct}% | "
                    f"{r.regime} | - |"
                )
    return "\n".join(lines)


def _score_table(scores: dict[str, CompositeScore]) -> str:
    lines = ["| 参赛者 | 合成总分 | trailing | regime 匹配 | 有效窗口 |", "|---|---|---|---|---|"]
    for name, s in sorted(scores.items()):
        fmt = lambda v: "-" if v is None else f"{v:.3f}"  # noqa: E731
        lines.append(f"| {name} | {fmt(s.total)} | {fmt(s.trailing)} | {fmt(s.regime_match)} | {s.valid_windows} |")
    return "\n".join(lines)


def render_report(
    snapshot: dict[tuple[date, date], dict[str, WindowReport]],
    scores: dict[str, CompositeScore],
    current_regime: str,
) -> str:
    any_na = any(
        r.stats is None for reports in snapshot.values() for r in reports.values()
    )
    parts = [
        "# 策略锦标赛报告",
        f"\n当前 regime：**{current_regime}**",
        "\n## 表现矩阵\n", _window_table(snapshot),
        "\n## 评分\n", _score_table(scores),
    ]
    if any_na:
        parts.append("\n**参考性结论**：存在 N/A 参赛者（样本门槛未过），本期排名仅供参考。")
    parts.append(f"\n{HONESTY_NOTE}\n")
    return "\n".join(parts)


def write_report(text: str, run_date: date, reports_dir: Path | None = None) -> Path:
    out_dir = Path(reports_dir) if reports_dir else TOURNAMENT_REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_date.isoformat()}_tournament.md"
    path.write_text(text, encoding="utf-8")
    return path
```

`cli.py`：`_build_parser` 增加

```python
    p_run = sub.add_parser("run", help="运行当期锦标赛并出报告")
    p_run.add_argument("--start", required=True, help="YYYYMMDD")
    p_run.add_argument("--end", required=True, help="YYYYMMDD")
```

`main` 增加分支：

```python
    if args.command == "run":
        from datetime import datetime
        from davis_analyzer.tournament.adapters import default_participants
        from davis_analyzer.tournament.judge import JudgeHarness, trading_calendar
        from davis_analyzer.tournament.report import render_report, write_report
        from davis_analyzer.tournament.scorecard import score_participant
        from davis_analyzer.tushare_client import TushareClient

        client = TushareClient()
        start = datetime.strptime(args.start, "%Y%m%d").date()
        end = datetime.strptime(args.end, "%Y%m%d").date()
        adapters = default_participants()
        judge = JudgeHarness(adapters, client)
        calendar = trading_calendar(client, start, end)
        snap = judge.snapshot(end, calendar)
        from davis_analyzer.market_regime import get_market_regime_with_confirm
        current_regime = get_market_regime_with_confirm(end.strftime("%Y%m%d"))
        scores = {}
        reports_by_participant: dict[str, list] = {}
        for _, reports in snap.items():
            for name, r in reports.items():
                reports_by_participant.setdefault(name, []).append(r)
        for name, reports in reports_by_participant.items():
            scores[name] = score_participant(reports, current_regime)
        text = render_report(snap, scores, current_regime)
        path = write_report(text, end)
        print(f"锦标赛报告已写入: {path}")
        return 0
```


- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_report.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/tournament/report.py davis_analyzer/tournament/cli.py davis_analyzer/tests/test_tournament_report.py
git commit -m "feat(tournament): 锦标赛报告渲染+run 子命令（Phase 1 完结）"
```

---

### Task 7: ledger.py — OOS 台账与版本纪律

**Files:**
- Create: `davis_analyzer/tournament/ledger.py`
- Modify: `davis_analyzer/tests/conftest.py`（追加 `tournament_db` fixture）
- Test: `davis_analyzer/tests/test_tournament_ledger.py`

**Interfaces:**
- Consumes: `stockhot.data_layer.market_db.get_connection`（仅 `open_db()` 用；核心函数全部接受注入 `conn: sqlite3.Connection`）。
- Produces: `LEDGER_DDL: str`；`LedgerRecord(op_type: str, run_date: date, participants: list[tuple[str, str]], params_version: str, oos_windows_used: int, detail: dict)`；`ensure_tables(conn) -> None`；`open_db() -> sqlite3.Connection`（真实库 + 建表）；`append_record(conn, rec: LedgerRecord) -> int`；`count_campaigns(conn, year: int) -> int`（op_type="evolve"）；`detect_continual_tweaking(conn, days: int = 30, max_runs: int = 2) -> bool`（同 params_version 在窗口内 evolve 次数超限）。

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_tournament_ledger.py`：

```python
"""ledger OOS 台账测试。"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from davis_analyzer.tournament.ledger import (
    LedgerRecord,
    append_record,
    count_campaigns,
    detect_continual_tweaking,
    ensure_tables,
)


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    ensure_tables(conn)
    yield conn
    conn.close()


def _rec(run_date: date, params_version: str = "v1", op: str = "evolve") -> LedgerRecord:
    return LedgerRecord(
        op_type=op, run_date=run_date,
        participants=[("davis_balanced", "vabc")],
        params_version=params_version, oos_windows_used=3, detail={},
    )


def test_ensure_tables_idempotent(db) -> None:
    ensure_tables(db)  # second call no raise


def test_append_and_count_campaigns(db) -> None:
    append_record(db, _rec(date(2025, 1, 10)))
    append_record(db, _rec(date(2025, 6, 1)))
    append_record(db, _rec(date(2024, 3, 1)))
    assert count_campaigns(db, 2025) == 2


def test_continual_tweaking_detection(db) -> None:
    d0 = date(2025, 3, 1)
    for i in range(3):
        append_record(db, _rec(d0 + timedelta(days=i)))
    assert detect_continual_tweaking(db) is True
    db2 = db
    db2.execute("DELETE FROM tournament_ledger")
    append_record(db2, _rec(date(2025, 3, 1)))
    append_record(db2, _rec(date(2025, 3, 2)))
    assert detect_continual_tweaking(db2) is False  # 2 次 ≤ max_runs
```

conftest 追加（Task 12 会再扩展 champions 表）：

```python
@pytest.fixture
def tournament_db() -> Iterator[sqlite3.Connection]:
    """In-memory DB with tournament tables."""
    from davis_analyzer.tournament.ledger import ensure_tables
    conn = sqlite3.connect(":memory:")
    ensure_tables(conn)
    yield conn
    conn.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_ledger.py -v`
Expected: FAIL

- [ ] **Step 3: 实现最小代码**

```python
"""OOS ledger — the single enforcement point of version discipline (§5.5)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from loguru import logger

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS tournament_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    op_type TEXT NOT NULL,
    run_date TEXT NOT NULL,
    participants TEXT NOT NULL,
    params_version TEXT NOT NULL,
    oos_windows_used INTEGER NOT NULL,
    detail TEXT
);
"""


@dataclass
class LedgerRecord:
    op_type: str  # "run" | "replay" | "evolve" | "promote" | "deploy"
    run_date: date
    participants: list[tuple[str, str]]  # (name, version)
    params_version: str
    oos_windows_used: int
    detail: dict = field(default_factory=dict)


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(LEDGER_DDL)
    conn.commit()


def open_db() -> sqlite3.Connection:
    from stockhot.data_layer.market_db import get_connection
    conn = get_connection()
    ensure_tables(conn)
    return conn


def append_record(conn: sqlite3.Connection, rec: LedgerRecord) -> int:
    cur = conn.execute(
        "INSERT INTO tournament_ledger (op_type, run_date, participants, "
        "params_version, oos_windows_used, detail) VALUES (?,?,?,?,?,?)",
        (rec.op_type, rec.run_date.isoformat(),
         json.dumps(rec.participants), rec.params_version,
         rec.oos_windows_used, json.dumps(rec.detail, ensure_ascii=False)),
    )
    conn.commit()
    return int(cur.lastrowid)


def count_campaigns(conn: sqlite3.Connection, year: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM tournament_ledger WHERE op_type='evolve' "
        "AND substr(run_date,1,4)=?", (str(year),)
    ).fetchone()
    return int(row[0])


def detect_continual_tweaking(
    conn: sqlite3.Connection, days: int = 30, max_runs: int = 2
) -> bool:
    """Same params_version evolved too often inside a rolling window."""
    rows = conn.execute(
        "SELECT run_date, params_version FROM tournament_ledger "
        "WHERE op_type='evolve' ORDER BY run_date"
    ).fetchall()
    by_version: dict[str, list[date]] = {}
    for run_date_str, version in rows:
        by_version.setdefault(version, []).append(date.fromisoformat(run_date_str))
    for version, dates in by_version.items():
        for i, d in enumerate(dates):
            window = [x for x in dates[i:] if x <= d + timedelta(days=days)]
            if len(window) > max_runs:
                logger.warning("continual tweaking suspected: {} ×{} within {}d", version, len(window), days)
                return True
    return False
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_ledger.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/tournament/ledger.py davis_analyzer/tests/conftest.py davis_analyzer/tests/test_tournament_ledger.py
git commit -m "feat(tournament): OOS 台账（记录/战役限额/连续微调检测）"
```

---

### Task 8: allocator.py — 资金分配建议（softmax+夹限）

**Files:**
- Create: `davis_analyzer/tournament/allocator.py`
- Modify: `davis_analyzer/tournament/cli.py`（`run` 报告追加建议权重段）
- Modify: `davis_analyzer/tournament/report.py`（`render_report` 增加可选 `allocation` 参数）
- Test: `davis_analyzer/tests/test_tournament_allocator.py`

**Interfaces:**
- Consumes: `constants.TOURNAMENT_ALLOCATOR_TAU/WEIGHT_BOUNDS`。
- Produces: `allocate(scores: dict[str, float | None]) -> dict[str, float]`（N/A 固定下限；有效者 softmax→夹限→按剩余预算归一；总和恒为 1）；报告新增段 `## 建议权重`。

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_tournament_allocator.py`：

```python
"""allocator 权重分配测试（冻结初值 τ=0.5, bounds [0.05,0.50]）。"""

from __future__ import annotations

import pytest

from davis_analyzer.tournament.allocator import allocate


def test_three_valid_participants() -> None:
    w = allocate({"A": 3.0, "B": 1.0, "C": 0.0})
    assert w["A"] == pytest.approx(0.8333, abs=1e-3)
    assert w["B"] == pytest.approx(0.0833, abs=1e-3)
    assert w["C"] == pytest.approx(0.0833, abs=1e-3)
    assert sum(w.values()) == pytest.approx(1.0)


def test_na_participant_gets_floor() -> None:
    w = allocate({"A": 3.0, "B": 1.0, "C": 0.0, "D": None})
    assert w["D"] == pytest.approx(0.05)
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["A"] == pytest.approx(0.7917, abs=1e-3)


def test_all_na_uniform() -> None:
    w = allocate({"A": None, "B": None})
    assert w["A"] == pytest.approx(0.5)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_allocator.py -v`
Expected: FAIL

- [ ] **Step 3: 实现最小代码**

```python
"""Allocator — risk-budget suggestion from composite scores (spec §5.4)."""

from __future__ import annotations

import math

from davis_analyzer.constants import (
    TOURNAMENT_ALLOCATOR_TAU,
    TOURNAMENT_WEIGHT_BOUNDS,
)


def allocate(scores: dict[str | None, float | None] | dict[str, float | None]) -> dict[str, float]:
    """Softmax(τ) over valid scores, clipped to bounds, N/A pinned to floor."""
    lo, hi = TOURNAMENT_WEIGHT_BOUNDS
    valid = {k: v for k, v in scores.items() if v is not None}
    n_na = len(scores) - len(valid)
    if not valid:
        n = max(len(scores), 1)
        return {k: 1.0 / n for k in scores}
    exps = {k: math.exp(v / TOURNAMENT_ALLOCATOR_TAU) for k, v in valid.items()}
    total = sum(exps.values())
    soft = {k: e / total for k, e in exps.items()}
    clipped = {k: min(max(v, lo), hi) for k, v in soft.items()}
    budget = 1.0 - n_na * lo
    clip_sum = sum(clipped.values())
    weights = {k: budget * v / clip_sum for k, v in clipped.items()}
    for k in scores:
        if k not in weights:
            weights[k] = lo
    return weights
```

`report.py`：`render_report` 签名追加 `allocation: dict[str, float] | None = None`，函数末尾 `HONESTY_NOTE` 之前插入：

```python
    if allocation is not None:
        rows = ["| 参赛者 | 建议权重 |", "|---|---|"]
        rows += [f"| {k} | {v:.2%} |" for k, v in sorted(allocation.items())]
        parts.append("\n## 建议权重（仅供人工决策，不自动执行）\n\n" + "\n".join(rows))
        parts.append("\n置信度说明：N/A 参赛者权重固定为下限；有效窗口不足时结论为参考性。")
```

`cli.py` 的 `run` 分支在 `render_report` 前追加：

```python
        from davis_analyzer.tournament.allocator import allocate
        allocation = allocate({k: s.total for k, s in scores.items()})
        text = render_report(snap, scores, current_regime, allocation=allocation)
```

（原 `text = render_report(...)` 行删除。）

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_allocator.py davis_analyzer/tests/test_tournament_report.py -v`
Expected: PASS（report 回归确认签名兼容）

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/tournament/allocator.py davis_analyzer/tournament/report.py davis_analyzer/tournament/cli.py davis_analyzer/tests/test_tournament_allocator.py
git commit -m "feat(tournament): 资金分配建议（softmax+夹限+N/A 下限）"
```

---

### Task 9: replay — 历史回放 + meta 序列 CSV + 前向模拟曲线

**Files:**
- Create: `davis_analyzer/tournament/replay.py`
- Modify: `davis_analyzer/tournament/cli.py`（新增 `replay` 子命令）
- Test: `davis_analyzer/tests/test_tournament_replay.py`

**Interfaces:**
- Consumes: Task 3-5、8 产物（纯数据版本，不重新跑引擎）。
- Produces: `replay(windows: list[tuple[date, date]], reports_by_window: dict[tuple[date, date], dict[str, WindowReport]], step: int | None = None) -> ReplayResult(meta_rows: list[dict], forward_rows: list[dict])`——`meta_rows` 每评估点一行 `{as_of, participant, composite, weight}`；`forward_rows` 每窗口一行 `{start, end, replay_equity}`（用 as_of 时点分配权重加权下一窗口各参赛者收益率，初始 100 万）；`export_replay(result: ReplayResult, out_dir: Path) -> tuple[Path, Path]`（`meta_series.csv`/`forward_curve.csv`）。CLI：`main(["replay", "--start", ..., "--end", ...])`。

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_tournament_replay.py`：

```python
"""replay 回放/meta 序列/前向曲线测试。"""

from __future__ import annotations

from datetime import date, timedelta

from davis_analyzer.backtest_report import PerformanceStats
from davis_analyzer.tournament.judge import WindowReport
from davis_analyzer.tournament.replay import replay


def _stats(sharpe: float) -> PerformanceStats:
    return PerformanceStats(
        total_return_pct=sharpe * 10, annualized_return_pct=sharpe * 10,
        sharpe_ratio=sharpe, max_drawdown_pct=0.0, win_rate_pct=60.0,
        turnover_per_rebalance=1.0, num_trades=20, num_rebalances=12,
        avg_holding_count=10.0, total_cost=100.0,
    )


def _reports(windows, sharpe_a: float, sharpe_b: float):
    out = {}
    for s, e in windows:
        out[(s, e)] = {
            "A": WindowReport("A", s, e, stats=_stats(sharpe_a), regime="risk_on", na_reason=None),
            "B": WindowReport("B", s, e, stats=_stats(sharpe_b), regime="risk_on", na_reason=None),
        }
    return out


def _windows(n: int = 6):
    d0 = date(2023, 1, 2)
    return [(d0 + timedelta(days=90 * i), d0 + timedelta(days=90 * i + 88)) for i in range(n)]


def test_replay_no_lookahead_and_rows() -> None:
    windows = _windows(6)
    result = replay(windows, _reports(windows, 1.5, 0.5))
    eval_points = sorted({r["as_of"] for r in result.meta_rows})
    # 首个评估点必须已有 ≥2 个已实现窗口（score_participant 需要）
    assert len(eval_points) >= 3
    # 前向曲线从 100 万起步、单调覆盖每个可分配窗口
    assert result.forward_rows[0]["replay_equity"] == 1_000_000.0
    assert len(result.forward_rows) >= 3
    # 分配权重逐点和为 1
    by_asof: dict[str, float] = {}
    for row in result.meta_rows:
        by_asof[row["as_of"]] = by_asof.get(row["as_of"], 0.0) + row["weight"]
    assert all(abs(v - 1.0) < 1e-6 for v in by_asof.values())


def test_replay_prefers_strong_participant() -> None:
    windows = _windows(6)
    result = replay(windows, _reports(windows, 2.0, 0.0))
    last = [r for r in result.meta_rows if r["participant"] == "A"]
    assert last[-1]["weight"] > 0.5  # 强者权重显著更高
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_replay.py -v`
Expected: FAIL

- [ ] **Step 3: 实现最小代码**

`davis_analyzer/tournament/replay.py`：

```python
"""Walk-forward replay — meta series + forward simulated equity (§7 Phase 2)."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from davis_analyzer.tournament.allocator import allocate
from davis_analyzer.tournament.judge import WindowReport
from davis_analyzer.tournament.scorecard import score_participant


@dataclass
class ReplayResult:
    meta_rows: list[dict] = field(default_factory=list)
    forward_rows: list[dict] = field(default_factory=list)


def _window_return(report: WindowReport) -> float:
    """Total return of one window, derived from annualised return & length."""
    if report.stats is None:
        return 0.0
    days = max((report.end - report.start).days, 1)
    years = days / 365.0
    ann = report.stats.annualized_return_pct / 100.0
    return (1.0 + ann) ** years - 1.0


def replay(
    windows: list[tuple[date, date]],
    reports_by_window: dict[tuple[date, date], dict[str, WindowReport]],
) -> ReplayResult:
    """At each window end (as_of), score with past windows, allocate, and
    apply that allocation to the NEXT window's realised returns."""
    result = ReplayResult()
    equity = 1_000_000.0
    for i, (start, end) in enumerate(windows):
        past = windows[:i]
        if not past:
            continue
        current_regime = "replay_synthetic"  # regime 匹配由 WindowReport.regime 提供
        reports_by_p: dict[str, list[WindowReport]] = {}
        for w in past:
            for name, r in reports_by_window[w].items():
                reports_by_p.setdefault(name, []).append(r)
        scores = {n: score_participant(rs, current_regime) for n, rs in reports_by_p.items()}
        weights = allocate({n: s.total for n, s in scores.items()})
        for name, score in scores.items():
            # 决策时点 = 本窗口起点（仅使用此前已实现窗口，防前视）
            result.meta_rows.append({
                "as_of": start.isoformat(), "participant": name,
                "composite": score.total, "weight": round(weights[name], 6),
            })
        nxt = windows[i]  # weights decided from past windows, applied to this one
        realised = {
            name: _window_return(r)
            for name, r in reports_by_window[nxt].items()
        }
        port_ret = sum(weights.get(n, 0.0) * ret for n, ret in realised.items())
        equity *= 1.0 + port_ret
        result.forward_rows.append({
            "start": nxt[0].isoformat(), "end": nxt[1].isoformat(),
            "replay_equity": round(equity, 2),
        })
    return result


def export_replay(result: ReplayResult, out_dir: Path) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "meta_series.csv"
    forward_path = out_dir / "forward_curve.csv"
    with meta_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["as_of", "participant", "composite", "weight"])
        writer.writeheader()
        writer.writerows(result.meta_rows)
    with forward_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["start", "end", "replay_equity"])
        writer.writeheader()
        writer.writerows(result.forward_rows)
    return meta_path, forward_path
```

`cli.py` 增加 `replay` 子命令（parser 同 `run` 的 `--start/--end`），`main` 分支：

```python
    if args.command == "replay":
        from datetime import datetime
        from davis_analyzer.config import TOURNAMENT_REPORTS_DIR
        from davis_analyzer.tournament.adapters import default_participants
        from davis_analyzer.tournament.judge import JudgeHarness, trading_calendar
        from davis_analyzer.tournament.replay import export_replay, replay
        from davis_analyzer.tushare_client import TushareClient

        client = TushareClient()
        start = datetime.strptime(args.start, "%Y%m%d").date()
        end = datetime.strptime(args.end, "%Y%m%d").date()
        judge = JudgeHarness(default_participants(), client)
        calendar = trading_calendar(client, start, end)
        windows = judge.build_windows(calendar)
        reports_by_window = {w: judge.evaluate_window(*w) for w in windows}
        result = replay(windows, reports_by_window)
        meta_path, forward_path = export_replay(result, TOURNAMENT_REPORTS_DIR)
        print(f"meta 序列: {meta_path}\n前向曲线: {forward_path}")
        return 0
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_replay.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/tournament/replay.py davis_analyzer/tournament/cli.py davis_analyzer/tests/test_tournament_replay.py
git commit -m "feat(tournament): 历史回放（meta 序列 CSV+前向模拟曲线）"
```

---

### Task 10: evolution.py 第一部分 — 随机段（CPCV-lite）+ 变异

**Files:**
- Create: `davis_analyzer/tournament/evolution.py`
- Test: `davis_analyzer/tests/test_tournament_evolution.py`

**Interfaces:**
- Consumes: `genome.Genome`；`constants.TOURNAMENT_SEGMENTS_N/K/EMBARGO_DAYS/SEGMENT_DRAWS/MUTATION_SIGMA`。
- Produces: `SegmentSplit(selection: list[tuple[date, date]], validation: list[tuple[date, date]])`；`draw_segments(calendar: list[date], n_segments: int = ..., k_validation: int = ..., embargo_days: int = ..., n_draws: int = ..., seed: int | None = None) -> list[SegmentSplit]`；`split_finals(calendar: list[date], finals_days: int = TOURNAMENT_FINALS_WINDOW_DAYS) -> tuple[list[date], list[date]]`（决赛段=尾部 N 交易日，进化段=其余）；`mutate(params: dict[str, float | int], genome: Genome, rng: random.Random) -> dict[str, float | int]`。

- [ ] **Step 1: 写失败测试**

```python
"""evolution 随机段（CPCV-lite）与变异测试。"""

from __future__ import annotations

import random
from datetime import date, timedelta

from davis_analyzer.tournament.evolution import draw_segments, mutate, split_finals
from davis_analyzer.tournament.genome import DAVIS_GENOME, Genome, ParamSpec


def _cal(n: int = 200) -> list[date]:
    d0 = date(2020, 1, 2)
    return [d0 + timedelta(days=i) for i in range(n)]


def test_segments_disjoint_with_embargo() -> None:
    cal = _cal(200)
    splits = draw_segments(cal, n_segments=10, k_validation=3, embargo_days=5,
                           n_draws=10, seed=42)
    assert len(splits) == 10
    for split in splits:
        sel_days = {d for s, e in split.selection for d in _dates_between(cal, s, e)}
        val_days = {d for s, e in split.validation for d in _dates_between(cal, s, e)}
        assert not (sel_days & val_days), "selection/validation must be disjoint"
        # 每个验证段剔除 embargo 隔离带后长度为 block−embargo
        for s, e in split.validation:
            assert len(_dates_between(cal, s, e)) == 20 - 5  # block=200/10, embargo=5


def test_split_finals_tail() -> None:
    cal = _cal(500)
    evolve_cal, finals_cal = split_finals(cal, finals_days=100)
    assert len(finals_cal) == 100
    assert finals_cal[-1] == cal[-1]
    assert evolve_cal[-1] < finals_cal[0]


def test_mutate_respects_bounds_and_choices() -> None:
    genome = Genome([
        ParamSpec("momentum_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("top_n", 5, 20, kind="choice", choices=[5, 10, 15, 20]),
    ])
    rng = random.Random(7)
    params = {"momentum_weight": 0.5, "top_n": 10}
    for _ in range(50):
        mutated = mutate(params, genome, rng)
        assert 0.0 <= mutated["momentum_weight"] <= 1.0
        assert mutated["top_n"] in (5, 10, 15, 20)


def _dates_between(cal: list[date], s: date, e: date) -> list[date]:
    return [d for d in cal if s <= d <= e]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_evolution.py -v`
Expected: FAIL

- [ ] **Step 3: 实现最小代码**

`davis_analyzer/tournament/evolution.py`：

```python
"""Parameter evolution engine (spec §5.6) — logic frozen, params only.

CPCV-lite validation: split history into N sequential blocks, randomly
hold out K as validation with an embargo gap at every boundary, repeat.
Mutation never leaves declared genome bounds (D8).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date

from davis_analyzer.constants import (
    TOURNAMENT_EMBARGO_DAYS,
    TOURNAMENT_FINALS_WINDOW_DAYS,
    TOURNAMENT_MUTATION_SIGMA,
    TOURNAMENT_SEGMENTS_K,
    TOURNAMENT_SEGMENTS_N,
    TOURNAMENT_SEGMENT_DRAWS,
)
from davis_analyzer.tournament.genome import Genome


@dataclass
class SegmentSplit:
    selection: list[tuple[date, date]] = field(default_factory=list)
    validation: list[tuple[date, date]] = field(default_factory=list)


def _blocks(calendar: list[date], n_segments: int) -> list[list[date]]:
    n = len(calendar)
    size = n // n_segments
    return [calendar[i * size:(i + 1) * size] for i in range(n_segments)]


def draw_segments(
    calendar: list[date],
    n_segments: int = TOURNAMENT_SEGMENTS_N,
    k_validation: int = TOURNAMENT_SEGMENTS_K,
    embargo_days: int = TOURNAMENT_EMBARGO_DAYS,
    n_draws: int = TOURNAMENT_SEGMENT_DRAWS,
    seed: int | None = None,
) -> list[SegmentSplit]:
    rng = random.Random(seed)
    blocks = _blocks(calendar, n_segments)
    splits: list[SegmentSplit] = []
    for _ in range(n_draws):
        val_idx = sorted(rng.sample(range(n_segments), k_validation))
        val_set = set(val_idx)
        validation: list[tuple[date, date]] = []
        for i in val_idx:
            block = blocks[i]
            kept = block[embargo_days:]  # embargo: drop the first days of the block
            if kept:
                validation.append((kept[0], kept[-1]))
        selection = [
            (blocks[i][0], blocks[i][-1]) for i in range(n_segments) if i not in val_set
        ]
        splits.append(SegmentSplit(selection=selection, validation=validation))
    return splits


def split_finals(
    calendar: list[date], finals_days: int = TOURNAMENT_FINALS_WINDOW_DAYS
) -> tuple[list[date], list[date]]:
    """Reserve the trailing trading days as the one-shot finals window."""
    if len(calendar) <= finals_days:
        raise ValueError("calendar too short for a finals window")
    return calendar[:-finals_days], calendar[-finals_days:]


def mutate(
    params: dict[str, float | int], genome: Genome, rng: random.Random
) -> dict[str, float | int]:
    """Gaussian perturbation, σ = 15% of range; choices snap back."""
    out: dict[str, float | int] = dict(params)
    for name in genome.names():
        if name not in out:
            continue
        spec = genome.spec(name)
        lo, hi = spec.lo, spec.hi
        if spec.kind == "choice":
            if rng.random() < 0.3:  # occasional discrete jump
                picks = [c for c in (spec.choices or []) if c != out[name]]
                if picks:
                    out[name] = rng.choice(picks)
            continue
        sigma = TOURNAMENT_MUTATION_SIGMA * (hi - lo)
        value = float(out[name]) + rng.gauss(0.0, sigma)
        out[name] = min(max(value, lo), hi)
    return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_evolution.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/tournament/evolution.py davis_analyzer/tests/test_tournament_evolution.py
git commit -m "feat(tournament): CPCV-lite 随机段验证+隔离带+决赛段切分+参数变异"
```

---

### Task 11: evolution.py 第二部分 — 战役循环 + 晋升门槛 + `evolve` 子命令

**Files:**
- Modify: `davis_analyzer/tournament/evolution.py`
- Modify: `davis_analyzer/tournament/cli.py`（新增 `evolve` 子命令）
- Test: `davis_analyzer/tests/test_tournament_evolution_campaign.py`

**Interfaces:**
- Consumes: Task 10 产物、`judge.JudgeHarness.evaluate_window`、`scorecard.window_performance`、`ledger`、`constants.TOURNAMENT_POPULATION/GENERATIONS/SURVIVAL_FRAC/PROMO_*/PERTURB_*/CAMPAIGNS_PER_YEAR`。
- Produces: `ScoreFn = Callable[[dict[str, float | int], list[tuple[date, date]]], float]`；`run_campaign(incumbent: dict, mutate_fn, score_fn: ScoreFn, selection_ranges: list[tuple[date, date]], population: int = 16, generations: int = 10, survival_frac: float = 0.25, seed: int | None = None) -> tuple[dict, float]`（返回最优参数与其选择集得分；**选择集打分，验证集绝不参与**）；`improvement_distribution(score_fn, incumbent, challenger, validation_ranges_per_split: list[list[tuple[date, date]]]) -> list[float]`；`perturb_decay(challenger, perturbed_scores: list[float]) -> float`；`check_promotion(improvements: list[float], decay: float, finals_pass: bool) -> PromotionDecision(ok: bool, reasons: list[str])`；`build_score_fn(judge: JudgeHarness, participant: str) -> ScoreFn`。CLI：`main(["evolve", "--participant", "davis_balanced", "--start", ..., "--end", ...])`——战役限额与台账写入。

- [ ] **Step 1: 写失败测试**

```python
"""evolution 战役循环与晋升门槛测试。"""

from __future__ import annotations

import pytest

from davis_analyzer.tournament.evolution import (
    check_promotion,
    improvement_distribution,
    perturb_decay,
    run_campaign,
)


def _mutate(params, rng):
    return {k: v + rng.gauss(0, 0.05) for k, v in params.items()}


def test_campaign_converges_toward_optimum() -> None:
    # 适应度只认 momentum_weight→0.8；初始 0.2，进化应显著逼近
    score_fn = lambda params, ranges: 1.0 - abs(params["momentum_weight"] - 0.8)
    best, best_score = run_campaign(
        {"momentum_weight": 0.2}, _mutate, score_fn,
        selection_ranges=[("s1", "e1")], seed=3,
    )
    assert best["momentum_weight"] > 0.5
    assert best_score > 0.7


def test_improvement_distribution_signs() -> None:
    score_fn = lambda params, ranges: params["momentum_weight"]
    inc = {"momentum_weight": 0.5}
    chall = {"momentum_weight": 0.7}
    splits = [[("v1", "v2")], [("v3", "v4")]]
    diffs = improvement_distribution(score_fn, inc, chall, splits)
    assert diffs == [pytest.approx(0.2), pytest.approx(0.2)]


def test_perturb_decay_ratio() -> None:
    decay = perturb_decay(challenger_score=1.0, perturbed_scores=[0.9, 0.8])
    assert decay == pytest.approx(0.15)  # 1 − mean(0.85)


def test_promotion_gates_truth_table() -> None:
    ok_all = check_promotion([0.5] * 20, decay=0.1, finals_pass=True)
    assert ok_all.ok and not ok_all.reasons
    low_win_rate = check_promotion([1.0] * 10 + [-1.0] * 10, decay=0.1, finals_pass=True)
    assert not low_win_rate.ok and any("胜率" in r for r in low_win_rate.reasons)
    bad_tail = check_promotion([2.0] * 17 + [-3.0] * 3, decay=0.1, finals_pass=True)
    assert not bad_tail.ok and any("25 分位" in r for r in bad_tail.reasons)
    bad_decay = check_promotion([0.5] * 20, decay=0.5, finals_pass=True)
    assert not bad_decay.ok and any("扰动" in r for r in bad_decay.reasons)
    no_finals = check_promotion([0.5] * 20, decay=0.1, finals_pass=False)
    assert not no_finals.ok and any("决赛" in r for r in no_finals.reasons)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_evolution_campaign.py -v`
Expected: FAIL

- [ ] **Step 3: 实现最小代码**

`evolution.py` 追加：

```python
# ── campaign & promotion gates (spec §5.6) ──

from typing import Callable

from loguru import logger

from davis_analyzer.constants import (
    TOURNAMENT_GENERATIONS,
    TOURNAMENT_PERTURB_MAX_DECAY,
    TOURNAMENT_POPULATION,
    TOURNAMENT_PROMO_MEDIAN_MIN,
    TOURNAMENT_PROMO_P25_MIN,
    TOURNAMENT_PROMO_WIN_RATE,
    TOURNAMENT_SURVIVAL_FRAC,
)

ScoreFn = Callable[[dict[str, float | int], list], float]
MutateFn = Callable[[dict, random.Random], dict]


def run_campaign(
    incumbent: dict[str, float | int],
    mutate_fn: MutateFn,
    score_fn: ScoreFn,
    selection_ranges: list,
    population: int = TOURNAMENT_POPULATION,
    generations: int = TOURNAMENT_GENERATIONS,
    survival_frac: float = TOURNAMENT_SURVIVAL_FRAC,
    seed: int | None = None,
) -> tuple[dict[str, float | int], float]:
    """Mutation-selection loop scored ONLY on the selection set (§5.6)."""
    rng = random.Random(seed)
    pool = [dict(incumbent)]
    best, best_score = dict(incumbent), score_fn(incumbent, selection_ranges)
    for _ in range(generations):
        candidates = list(pool)
        while len(candidates) < population:
            candidates.append(mutate_fn(rng.choice(pool), rng))
        scored = sorted(
            ((score_fn(c, selection_ranges), c) for c in candidates),
            key=lambda x: x[0], reverse=True,
        )
        if scored[0][0] > best_score:
            best_score, best = scored[0][0], dict(scored[0][1])
        keep = max(int(len(scored) * survival_frac), 1)
        pool = [dict(c) for _, c in scored[:keep]]
    return best, best_score


def improvement_distribution(
    score_fn: ScoreFn,
    incumbent: dict[str, float | int],
    challenger: dict[str, float | int],
    validation_ranges_per_split: list[list],
) -> list[float]:
    """(challenger − incumbent) per split, on validation ranges only."""
    out: list[float] = []
    for ranges in validation_ranges_per_split:
        out.append(score_fn(challenger, ranges) - score_fn(incumbent, ranges))
    return out


def perturb_decay(challenger_score: float, perturbed_scores: list[float]) -> float:
    """Performance decay ratio after ±20% parameter perturbation."""
    if not perturbed_scores or challenger_score == 0:
        return 0.0 if challenger_score == 0 else 1.0
    mean_perturbed = sum(perturbed_scores) / len(perturbed_scores)
    return 1.0 - mean_perturbed / challenger_score


@dataclass
class PromotionDecision:
    ok: bool
    reasons: list[str]


def check_promotion(improvements: list[float], decay: float, finals_pass: bool) -> PromotionDecision:
    """All four gates must hold (frozen thresholds in constants)."""
    import statistics

    reasons: list[str] = []
    if not improvements:
        return PromotionDecision(False, ["无随机段改进样本"])
    win_rate = sum(1 for x in improvements if x > 0) / len(improvements)
    if win_rate < TOURNAMENT_PROMO_WIN_RATE:
        reasons.append(f"随机段胜率 {win_rate:.0%} < {TOURNAMENT_PROMO_WIN_RATE:.0%}")
    median = statistics.median(improvements)
    if median <= TOURNAMENT_PROMO_MEDIAN_MIN:
        reasons.append(f"中位改进 {median:.3f} ≤ {TOURNAMENT_PROMO_MEDIAN_MIN}")
    p25 = _percentile(improvements, 25)
    if p25 <= TOURNAMENT_PROMO_P25_MIN:
        reasons.append(f"25 分位改进 {p25:.3f} ≤ {TOURNAMENT_PROMO_P25_MIN}")
    if decay > TOURNAMENT_PERTURB_MAX_DECAY:
        reasons.append(f"扰动衰减 {decay:.0%} > {TOURNAMENT_PERTURB_MAX_DECAY:.0%}")
    if not finals_pass:
        reasons.append("决赛窗口未通过（或已烧尽，需 paper_trading 前向证据）")
    ok = not reasons
    if ok:
        logger.info("promotion gates passed: win_rate={:.0%} median={:.3f} p25={:.3f} decay={:.0%}",
                    win_rate, median, p25, decay)
    return PromotionDecision(ok, reasons)


def _percentile(values: list[float], pct: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * pct / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def build_score_fn(judge, participant: str) -> ScoreFn:
    """Score a parameter set as the mean window_performance over ranges,
    evaluated through the SAME JudgeHarness (spec §5.2 rule 6)."""
    from davis_analyzer.tournament.scorecard import window_performance

    def score_fn(params: dict, ranges: list) -> float:
        perfs: list[float] = []
        for start, end in ranges:
            reports = judge.evaluate_window(start, end, {participant: params})
            r = reports.get(participant)
            if r is not None and r.stats is not None:
                perfs.append(window_performance(r.stats))
        if not perfs:
            return float("-inf")
        return sum(perfs) / len(perfs)

    return score_fn
```

`cli.py` 增加 `evolve` 子命令（parser 参数 `--participant`/`--start`/`--end`/`--seed`），`main` 分支（要点，完整可拷贝）：

```python
    if args.command == "evolve":
        from datetime import date, datetime
        import random

        from davis_analyzer import constants as C
        from davis_analyzer.tournament.adapters import default_participants
        from davis_analyzer.tournament.evolution import (
            build_score_fn, check_promotion, draw_segments,
            improvement_distribution, mutate, run_campaign, split_finals,
        )
        from davis_analyzer.tournament.genome import DAVIS_GENOME
        from davis_analyzer.tournament.judge import JudgeHarness, trading_calendar
        from davis_analyzer.tournament.ledger import (
            LedgerRecord, append_record, count_campaigns, open_db,
        )
        from davis_analyzer.tushare_client import TushareClient

        today = date.today()
        ledger_conn = open_db()
        if count_campaigns(ledger_conn, today.year) >= C.TOURNAMENT_CAMPAIGNS_PER_YEAR:
            print(f"进化战役年度限额已满（{C.TOURNAMENT_CAMPAIGNS_PER_YEAR}/年），拒绝执行")
            return 1

        adapters = {a.name: a for a in default_participants()}
        adapter = adapters[args.participant]
        incumbent = dict(C.TOURNAMENT_DAVIS_PRESETS.get(args.participant, {}))
        client = TushareClient()
        start = datetime.strptime(args.start, "%Y%m%d").date()
        end = datetime.strptime(args.end, "%Y%m%d").date()
        calendar = trading_calendar(client, start, end)
        evolve_cal, finals_cal = split_finals(calendar)
        splits = draw_segments(evolve_cal, seed=args.seed)
        judge = JudgeHarness([adapter], client)
        score_fn = build_score_fn(judge, args.participant)

        best, best_sel_score = run_campaign(
            incumbent,
            lambda p, rng: mutate(p, DAVIS_GENOME, rng),
            score_fn,
            selection_ranges=splits[0].selection,
            seed=args.seed,
        )
        improvements = improvement_distribution(
            score_fn, incumbent, best, [s.validation for s in splits]
        )
        # 扰动稳健性：对每个参数 ±20% 重估（简单实现：全参数同向扰动）
        base = score_fn(best, splits[0].selection)
        perturbed = [
            score_fn({k: min(max(float(v) * (1 + sgn * C.TOURNAMENT_PERTURB_PCT), 0.0), 1.0)
                      if DAVIS_GENOME.spec(k).kind == "weight" else v
                      for k, v in best.items()}, splits[0].selection)
            for sgn in (1, -1)
        ]
        from davis_analyzer.tournament.evolution import perturb_decay
        decay = perturb_decay(base, perturbed)
        finals_pass = score_fn(best, [(finals_cal[0], finals_cal[-1])]) > \
            score_fn(incumbent, [(finals_cal[0], finals_cal[-1])])
        decision = check_promotion(improvements, decay, finals_pass)

        append_record(ledger_conn, LedgerRecord(
            op_type="evolve", run_date=today,
            participants=[(args.participant, adapter.version)],
            params_version=f"campaign-{today.isoformat()}",
            oos_windows_used=len(splits),
            detail={"improvements": [round(x, 4) for x in improvements],
                    "decay": round(decay, 4), "finals_pass": finals_pass,
                    "ok": decision.ok, "reasons": decision.reasons,
                    "best_params": {k: float(v) for k, v in best.items()}},
        ))
        print(f"晋升判定: {'通过' if decision.ok else '未通过'}")
        for r in decision.reasons:
            print(f"  - {r}")
        print(f"最优参数: {best}")
        print("结果已记入 tournament_ledger（通过后由 champions 流程存档）")
        return 0 if decision.ok else 2
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_evolution_campaign.py davis_analyzer/tests/test_tournament_evolution.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/tournament/evolution.py davis_analyzer/tournament/cli.py davis_analyzer/tests/test_tournament_evolution_campaign.py
git commit -m "feat(tournament): 进化战役循环+四条晋升门槛+evolve 子命令（台账限额）"
```

---

### Task 12: champions.py — 冠军存档/分槽/deploy 同步

**Files:**
- Create: `davis_analyzer/tournament/champions.py`
- Modify: `davis_analyzer/tests/conftest.py`（`tournament_db` 追加 champions 表）
- Modify: `davis_analyzer/tournament/cli.py`（新增 `champions {list,deploy,verify}` 子命令）
- Test: `davis_analyzer/tests/test_tournament_champions.py`

**Interfaces:**
- Consumes: `ledger.ensure_tables/open_db`；`constants.TOURNAMENT_CHAMPION_SLOTS/CHAMPION_PRESETS`。
- Produces: `CHAMPIONS_DDL: str`；`ChampionRecord(champion_id, participant, regime, params: dict, version, generation, evidence: dict, promoted_at: date, oos_consumed: int, is_incumbent: bool)`；`ensure_tables(conn)`；`promote_champion(conn, rec: ChampionRecord) -> str`（分槽：同 (participant, regime) 历史 ≤2 + 现任 1，超槽淘汰最旧非现任）；`incumbents(conn) -> list[ChampionRecord]`；`promote_from_ledger(conn) -> ChampionRecord | None`（读最近一条 ok=true 的 evolve 台账记录 → 存档晋升 → 追加 promote 台账记录；无合格记录返回 None。v1 槽位 regime="all"——战役按全段寻优，regime 分槽待 L2 运行经验后细化）；`verify_sync(conn, presets: dict[str, dict]) -> list[str]`（不一致清单，空=一致）；`render_deploy_note(recs: list[ChampionRecord]) -> str`（SOP 变更说明文本）。

- [ ] **Step 1: 写失败测试**

```python
"""champions 冠军存档测试。"""

from __future__ import annotations

from datetime import date

import pytest

from davis_analyzer.tournament.champions import (
    ChampionRecord,
    incumbents,
    promote_champion,
    render_deploy_note,
    verify_sync,
)
from davis_analyzer.tournament.ledger import ensure_tables as ensure_ledger


def _rec(gen: int, params: dict | None = None) -> ChampionRecord:
    return ChampionRecord(
        champion_id=f"ch-{gen}", participant="davis_balanced", regime="risk_on",
        params=params or {"momentum_weight": round(0.2 + 0.1 * gen, 2)},
        version=f"v{gen}", generation=gen,
        evidence={"win_rate": 0.7, "median": 0.3, "p25": 0.1, "decay": 0.1,
                  "finals_pass": True},
        promoted_at=date(2025, 1, gen + 1), oos_consumed=1, is_incumbent=True,
    )


@pytest.fixture
def db():
    import sqlite3
    from davis_analyzer.tournament.champions import CHAMPIONS_DDL
    conn = sqlite3.connect(":memory:")
    ensure_ledger(conn)
    conn.executescript(CHAMPIONS_DDL)
    conn.commit()
    yield conn
    conn.close()


def test_slot_cap_two_history_plus_incumbent(db) -> None:
    for gen in range(5):
        promote_champion(db, _rec(gen))
    rows = db.execute(
        "SELECT COUNT(*) FROM tournament_champions WHERE participant='davis_balanced' "
        "AND regime='risk_on'"
    ).fetchone()[0]
    assert rows == 3  # 2 历史 + 1 现任


def test_incumbent_marking(db) -> None:
    promote_champion(db, _rec(1))
    inc = incumbents(db)
    assert len(inc) == 1 and inc[0].generation == 1


def test_verify_sync_detects_mismatch(db) -> None:
    promote_champion(db, _rec(1, params={"momentum_weight": 0.31}))
    problems = verify_sync(db, {"davis_balanced": {"momentum_weight": 0.31}})
    assert problems == []
    problems = verify_sync(db, {"davis_balanced": {"momentum_weight": 0.99}})
    assert problems and "davis_balanced" in problems[0]


def test_promote_from_ledger(db) -> None:
    from davis_analyzer.tournament.champions import promote_from_ledger
    from davis_analyzer.tournament.ledger import LedgerRecord, append_record

    append_record(db, LedgerRecord(
        op_type="evolve", run_date=date(2025, 1, 1),
        participants=[("davis_balanced", "v1")], params_version="campaign-x",
        oos_windows_used=20,
        detail={"ok": True, "best_params": {"momentum_weight": 0.35},
                "improvements": [0.1], "decay": 0.1, "finals_pass": True, "reasons": []},
    ))
    rec = promote_from_ledger(db)
    assert rec is not None and rec.participant == "davis_balanced"
    assert any(c.params == {"momentum_weight": 0.35} for c in incumbents(db))
    assert promote_from_ledger(db) is not None  # 幂等：第二次晋升最新同一条


def test_deploy_note_renders(db) -> None:
    promote_champion(db, _rec(1))
    text = render_deploy_note(incumbents(db))
    assert "CHAMPION_PRESETS" in text and "SOP" in text
```

conftest 的 `tournament_db` fixture 在 `ensure_tables(conn)` 后追加两行：

```python
    from davis_analyzer.tournament.champions import CHAMPIONS_DDL
    conn.executescript(CHAMPIONS_DDL)
    conn.commit()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_tournament_champions.py -v`
Expected: FAIL

- [ ] **Step 3: 实现最小代码**

`davis_analyzer/tournament/champions.py`：

```python
"""Champion archive — multi-champion hall of fame + deploy sync (spec §5.7)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date

from loguru import logger

from davis_analyzer.constants import TOURNAMENT_CHAMPION_SLOTS

CHAMPIONS_DDL = """
CREATE TABLE IF NOT EXISTS tournament_champions (
    champion_id TEXT PRIMARY KEY,
    participant TEXT NOT NULL,
    regime TEXT NOT NULL,
    params_json TEXT NOT NULL,
    version TEXT NOT NULL,
    generation INTEGER NOT NULL,
    evidence_json TEXT NOT NULL,
    promoted_at TEXT NOT NULL,
    oos_consumed INTEGER NOT NULL DEFAULT 0,
    is_incumbent INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class ChampionRecord:
    champion_id: str
    participant: str
    regime: str
    params: dict[str, float]
    version: str
    generation: int
    evidence: dict
    promoted_at: date
    oos_consumed: int
    is_incumbent: bool


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(CHAMPIONS_DDL)
    conn.commit()


def promote_champion(conn: sqlite3.Connection, rec: ChampionRecord) -> str:
    """Insert a champion and enforce slot caps (2 history + 1 incumbent)."""
    champion_id = rec.champion_id or uuid.uuid4().hex[:12]
    conn.execute(
        "UPDATE tournament_champions SET is_incumbent=0 WHERE participant=? AND regime=?",
        (rec.participant, rec.regime),
    )
    conn.execute(
        "INSERT OR REPLACE INTO tournament_champions VALUES (?,?,?,?,?,?,?,?,?,?)",
        (champion_id, rec.participant, rec.regime,
         json.dumps(rec.params), rec.version, rec.generation,
         json.dumps(rec.evidence, ensure_ascii=False),
         rec.promoted_at.isoformat(), rec.oos_consumed, int(rec.is_incumbent)),
    )
    rows = conn.execute(
        "SELECT champion_id, promoted_at FROM tournament_champions "
        "WHERE participant=? AND regime=? AND is_incumbent=0 "
        "ORDER BY promoted_at DESC",
        (rec.participant, rec.regime),
    ).fetchall()
    for champion_id_old, promoted in rows[TOURNAMENT_CHAMPION_SLOTS:]:
        conn.execute("DELETE FROM tournament_champions WHERE champion_id=?", (champion_id_old,))
        logger.info("slot cap: dropped old champion {} ({} {})", champion_id_old, rec.participant, promoted)
    conn.commit()
    return champion_id


def incumbents(conn: sqlite3.Connection) -> list[ChampionRecord]:
    rows = conn.execute(
        "SELECT * FROM tournament_champions WHERE is_incumbent=1 ORDER BY participant, regime"
    ).fetchall()
    return [
        ChampionRecord(
            champion_id=r[0], participant=r[1], regime=r[2], params=json.loads(r[3]),
            version=r[4], generation=r[5], evidence=json.loads(r[6]),
            promoted_at=date.fromisoformat(r[7]), oos_consumed=r[8], is_incumbent=bool(r[9]),
        )
        for r in rows
    ]


def promote_from_ledger(conn: sqlite3.Connection) -> ChampionRecord | None:
    """Promote the latest passing evolve campaign into the archive.

    Reads tournament_ledger for the newest op_type='evolve' row whose detail
    has ok=true, archives it as the incumbent champion, and appends a
    'promote' ledger record.  Returns None when nothing qualifies.
    """
    from davis_analyzer.tournament.ledger import LedgerRecord, append_record

    rows = conn.execute(
        "SELECT id, participants, detail FROM tournament_ledger "
        "WHERE op_type='evolve' ORDER BY id DESC"
    ).fetchall()
    for _id, participants_json, detail_json in rows:
        detail = json.loads(detail_json or "{}")
        if not detail.get("ok"):
            continue
        participants = json.loads(participants_json or "[]")
        name = participants[0][0] if participants else "unknown"
        regime = "all"  # v1: campaign optimises over all segments (see Interfaces)
        params = {k: float(v) for k, v in detail.get("best_params", {}).items()}
        for inc in incumbents(conn):
            if inc.participant == name and inc.regime == regime and inc.params == params:
                return inc  # already promoted — idempotent
        gen_row = conn.execute(
            "SELECT COUNT(*) FROM tournament_champions WHERE participant=? AND regime=?",
            (name, regime),
        ).fetchone()
        rec = ChampionRecord(
            champion_id=uuid.uuid4().hex[:12],
            participant=name, regime=regime,
            params=params,
            version=f"gen{int(gen_row[0]) + 1}",
            generation=int(gen_row[0]) + 1,
            evidence={k: detail.get(k) for k in
                      ("improvements", "decay", "finals_pass", "reasons")},
            promoted_at=date.today(), oos_consumed=1, is_incumbent=True,
        )
        champion_id = promote_champion(conn, rec)
        append_record(conn, LedgerRecord(
            op_type="promote", run_date=rec.promoted_at,
            participants=[(name, rec.version)], params_version=rec.version,
            oos_windows_used=1, detail={"champion_id": champion_id},
        ))
        return rec
    return None


def verify_sync(conn: sqlite3.Connection, presets: dict[str, dict]) -> list[str]:
    """Champions deployed in constants.CHAMPION_PRESETS must match DB incumbents."""
    problems: list[str] = []
    db_incumbents = {(c.participant, json.dumps(c.params, sort_keys=True)) for c in incumbents(conn)}
    deployed = {name for name in presets}
    for c in incumbents(conn):
        key = (c.participant, json.dumps(c.params, sort_keys=True))
        if key not in db_incumbents:
            continue  # defensive, never happens
        if c.participant not in deployed:
            problems.append(f"{c.participant}: DB 现任冠军未部署到 CHAMPION_PRESETS")
    for name, params in presets.items():
        match = any(
            c.participant == name and json.dumps(c.params, sort_keys=True) == json.dumps(params, sort_keys=True)
            for c in incumbents(conn)
        )
        if not match:
            problems.append(f"{name}: CHAMPION_PRESETS 参数与 DB 现任冠军不一致")
    return problems


def render_deploy_note(recs: list[ChampionRecord]) -> str:
    lines = [
        "# 冠军部署说明（champions deploy 生成）",
        "",
        "将以下现任冠军参数同步进 `davis_analyzer/constants.py` 的 `CHAMPION_PRESETS`，",
        "并在 SOP.md 记录版本变更；同步后运行 `verify` 确认一致。",
        "",
    ]
    for c in recs:
        lines.append(f"## {c.participant}（regime={c.regime}, version={c.version}, gen={c.generation}）")
        lines.append("```python")
        lines.append(f'CHAMPION_PRESETS["{c.participant}"] = {json.dumps(c.params, ensure_ascii=False, indent=2)}')
        lines.append("```")
        lines.append(f"上位证据：{json.dumps(c.evidence, ensure_ascii=False)}\n")
    return "\n".join(lines)
```

`cli.py` 增加 `champions` 子命令组（`list/deploy/verify`）：

```python
    p_ch = sub.add_parser("champions", help="冠军存档管理")
    ch_sub = p_ch.add_subparsers(dest="ch_command", required=True)
    ch_sub.add_parser("list", help="列出冠军")
    ch_sub.add_parser("promote", help="从台账晋升最近一次通过的战役为冠军")
    ch_sub.add_parser("deploy", help="生成部署说明（人工同步 constants.py）")
    ch_sub.add_parser("verify", help="校验 CHAMPION_PRESETS 与 DB 现任一致")
```

`main` 增加：

```python
    if args.command == "champions":
        from davis_analyzer import constants as C
        from davis_analyzer.config import TOURNAMENT_REPORTS_DIR
        from davis_analyzer.tournament.champions import (
            incumbents, render_deploy_note, verify_sync,
        )
        from davis_analyzer.tournament.ledger import open_db

        conn = open_db()
        from davis_analyzer.tournament.champions import ensure_tables as ensure_ch
        ensure_ch(conn)
        if args.ch_command == "list":
            for c in incumbents(conn):
                print(f"{c.participant:<20} regime={c.regime:<10} gen={c.generation} params={c.params}")
            return 0
        if args.ch_command == "promote":
            from davis_analyzer.tournament.champions import promote_from_ledger
            rec = promote_from_ledger(conn)
            if rec is None:
                print("没有可晋升的战役（台账中无 ok=true 的 evolve 记录）")
                return 1
            print(f"已晋升: {rec.participant} gen={rec.generation} params={rec.params}")
            return 0
        if args.ch_command == "deploy":
            recs = incumbents(conn)
            note = render_deploy_note(recs)
            path = TOURNAMENT_REPORTS_DIR / "champion_deploy_note.md"
            path.write_text(note, encoding="utf-8")
            print(f"部署说明已生成: {path}（请人工同步 constants.py 与 SOP.md）")
            return 0
        if args.ch_command == "verify":
            problems = verify_sync(conn, dict(C.CHAMPION_PRESETS))
            if problems:
                for p in problems:
                    print(f"不一致: {p}")
                return 1
            print("CHAMPION_PRESETS 与 DB 现任冠军一致")
            return 0
```

- [ ] **Step 4: 运行测试确认通过（全套回归）**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/ -v`
Expected: 全部 PASS（tournament 8 个测试文件 + 既有 21 个测试文件回归无破坏）

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/tournament/champions.py davis_analyzer/tournament/cli.py davis_analyzer/tests/conftest.py davis_analyzer/tests/test_tournament_champions.py
git commit -m "feat(tournament): 冠军存档（分槽多冠军+deploy 说明+一致性校验）Phase 1-3 完结"
```

---

## 计划自审记录

- **Spec coverage**：§5.1→Task 3；§5.2→Task 4；§5.3→Task 5；§5.4→Task 8；§5.5→Task 7；§5.6→Task 10/11；§5.7→Task 12；§5.8 反环化→Task 2（参数通道）+ Task 1（SOP 声明裁判参数不可进化）+ Task 11（score_fn 只通向参赛者）；§7 Phase 1→Task 1-6、Phase 2→Task 7-9、Phase 3→Task 10-12；§8 测试逐条对应各任务；诚实边界文案→Task 6 `HONESTY_NOTE`。Phase 4 明确排除。
- **Placeholder scan**：无 TBD/“稍后实现”；Task 6 cli 代码中的 STUDIES_DIR 笔误已在代码块内就地标注修正。
- **Type consistency**：`RunResult`/`WindowReport`/`CompositeScore`/`SegmentSplit`/`PromotionDecision`/`ChampionRecord` 各任务引用签名一致；`allocate` 键类型统一 `dict[str, float | None]`。
