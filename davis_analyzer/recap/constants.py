"""recap 子系统配置(戏剧权重在父级 constants.py,此处只放非权重配置)。"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RECAP_ROOT = Path(__file__).resolve().parent
EPISODES_DIR = RECAP_ROOT / "episodes"
INBOX_DIR = RECAP_ROOT / "inbox"

# ── TTS 双解说音色(edge-tts;实况=激情男声,嘉宾=沉稳女声)──
VOICE_PB = "zh-CN-YunjianNeural"
VOICE_COLOR = "zh-CN-XiaoxiaoNeural"

# ── 素材文件名协议:20260918_605577.SH_01.mp4 ──
CLIP_FILENAME_RE = re.compile(r"^(\d{8})_([0-9]{6}\.(?:SH|SZ|BJ))_(\d{2})\.mp4$")

# ── 视频专用附加敏感词(cardgen 词表之上;解说=资讯复盘,禁交易指令)──
EXTRA_SENSITIVE_WORDS: tuple[str, ...] = (
    "买入", "抄底", "上车", "必涨", "满仓", "加仓", "止盈", "止损", "目标价", "建仓",
)
# 收窄版诱导句式:不复用 cardgen 的 你(应该|可以|要|不妨)——解说文体合法用「你看/你看到没有」
INDUCEMENT_PATTERNS_NARROW: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"你应该|快去|赶紧"), "第二人称劝导(收窄版)"),
    (re.compile(r"赌[A-Za-z0-9\u4e00-\u9fa5]{1,6}|赌徒"), "赌博化表述"),
)

# ── NBA 转播语言映射(节目灵魂,scriptwriter prompt 引用)──
NBA_STYLE_TABLE: tuple[tuple[str, str], ...] = (
    ("涨停封板", "扣筐得手;尾盘封板=压哨绝杀"),
    ("炸板", "被帽/关键失误"),
    ("烂板回封", "被帽后再扣,统治力"),
    ("地天板", "大逆转,更衣室归来"),
    ("连板天梯", "积分榜/连胜纪录"),
    ("游资席位", "球星对位(席位=球员)"),
    ("三大指数+涨跌家数", "片头比分牌"),
    ("振幅/换手", "数据弹出卡"),
)

REQUIRED_DISCLAIMER = "不构成投资建议"
