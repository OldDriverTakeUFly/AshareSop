"""对照腿: 当前数据 + 不混入退市数据(view off)重跑冻结 u200 — 量化纯数据漂移.

归因分解(0011 日志 §6.1):
  旧(+126.4, 8-18缓存) vs 本对照 = 数据版本漂移
  本对照 vs refix_frozen_u200(+60.0, 同期缓存+view on) = 幸存者混入净效应
复用 refix_frozen_baseline 全部口径, 仅翻转 MARKET_DB_ATTACH_DELISTED 并改名。
"""
import os, sys
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts", "abx"))

import refix_frozen_baseline as rf  # 模块导入时会设 view=1, 此后翻回 0
os.environ["MARKET_DB_ATTACH_DELISTED"] = "0"
rf.OUT_PATH = "logs/abx/refix_ctrl_u200.json"
rf.ACCOUNT = "refix_ctrl_u200"
rf.main()
