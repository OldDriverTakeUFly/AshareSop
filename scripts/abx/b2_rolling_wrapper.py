"""B2: 退市财务回补后的滚动宇宙复测 — 测「完整幸存者代价」(plan 2026-09-12 §1.B2).

对照链: 0010 剪枝口径滚动 R_q200 = -52.20% → 0011 价格回补后 = -54.61%
(仅名额挤占通道) → 本复测(财务完整, 退市股可被买入+可被强平) = 完整代价。
预期方向: 退市股偶尔过闸被买入并承受损失 → 数字应再下移; 若仍 0 买入,
则结论为「十道闸天然过滤死票」, 幸存者议题关闭(-2.4pp 即全量)。

复用 rolling_universe_g2_abx 全口径, 仅改输出/账户前缀(b2_ru_, 不覆盖 0010/0011)。
"""
import os, sys
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.environ["MARKET_DB_ATTACH_DELISTED"] = "1"
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts", "abx"))
sys.argv = [sys.argv[0], "--variants", "R_q200"]

import rolling_universe_g2_abx as ru

ru.OUT_PATH = "logs/abx/rolling_universe_B2_R_q200.json"
_orig_reset = ru.reset_account
ru.reset_account = lambda name: _orig_reset(name.replace("ru_", "b2_ru_"))
ru.main()
