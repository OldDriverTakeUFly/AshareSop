"""R_q200 滚动宇宙在幸存者修复数据下的复测 (0010 主变体敏感度验证).

复用 rolling_universe_g2_abx.py 的全部口径(宇宙∪持仓/段循环/指标),
仅覆盖: 输出路径、账户名前缀(refix_ru_, 不覆盖 0010 旧账户)、限定 R_q200。
MARKET_DB_ATTACH_DELISTED=1 → 每季宇宙构建可见退市股, 强平规则生效。
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

ru.OUT_PATH = "logs/abx/rolling_universe_refix_R_q200.json"
_orig_reset = ru.reset_account
ru.reset_account = lambda name: _orig_reset(name.replace("ru_", "refix_ru_"))
ru.main()
