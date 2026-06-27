#!/usr/bin/env python
"""研报语音化转换器——将 markdown 研报转为适合朗读的口语化纯文本（.txt）。

解决「表格/数字密集段落无法听」的核心痛点：md 报告是给"看"设计的，
密集表格、对照矩阵、ASCII 框图读出来全是流水账。本工具把它们转成
口语化叙述，输出到 docs_speech/ 镜像目录（与 docs_audio/ 平行）。

架构（规则层 + LLM 层 + 降级）：
- 规则层（确定性，免费）：剥离 frontmatter/代码块/ASCII 框图，跳过
  免责声明等非内容段，去 markdown 标记，并把连续 `|` 行识别为表格块。
- LLM 层（按需，复用 stockhot.advisor.llm_provider）：对表格块和数字
  密集段做语义叙述化（取首尾/极值，转"从 X 到 Y"口语）。
- 降级（LLM 不可用 / --no-llm）：规则叙述，拼成"键值"句式，保证
  零依赖、可重复、离线可用。

Usage:
    .venv/bin/python scripts/report_to_speech_text.py <markdown_file> [--no-llm] [--output FILE]

Examples:
    # 默认（LLM 叙述化，镜像到 docs_speech/）
    python scripts/report_to_speech_text.py "docs/盘后总结/2026-06-26_盘后总结.md"

    # 关闭 LLM，纯规则降级（离线/省成本）
    python scripts/report_to_speech_text.py report.md --no-llm

    # 指定输出路径
    python scripts/report_to_speech_text.py report.md --output /tmp/x.txt
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# 把仓库根加入 sys.path，使 `import stockhot.advisor.llm_provider` 可达。
# 本脚本位于 <repo>/scripts/，故仓库根是上两级目录。
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 清除 socks 代理：openai 库底层的 httpx 不支持 socks:// scheme（会抛
# "Unknown scheme for proxy URL"）。保留 HTTP(S)_PROXY 即可让 GLM/OpenAI
# 等正常走 http 代理。仅在脚本进程内生效，不污染用户全局环境。
for _var in ("ALL_PROXY", "all_proxy"):
    if os.environ.get(_var, "").lower().startswith("socks"):
        os.environ.pop(_var, None)

# 加载 .env（与 advisor cli 一致），让 get_provider 能读到 LLM_API_KEY
try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except Exception:
    pass  # dotenv 未装或文件缺失时静默降级


# ── 配置 ────────────────────────────────────────────────────────────────────

# 跳过的非内容段落（标题关键词命中即跳过整段直到下一个同级标题）
SKIP_SECTIONS = {
    "免责声明",
    "版本历史",
    "附录",
    "数据源索引",
    "数据源与计算方法",
    "Source of Truth",
    "Ultraworked with",
    "Co-authored-by",
    "数据源清单",
    "关键计算说明",
    "引擎调用记录",
    "横向市场估值锚定",
}

# emoji 范围（TTS 会朗读 emoji 描述，很奇怪）
_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001F600-\U0001F64F]"
)
# 纯 URL 行
_URL_LINE_RE = re.compile(r"^https?://\S+$")
# 数字密集判定：一行内出现 ≥3 个百分比或大数（≥1000 的整数/小数）
_NUM_DENSE_RE = re.compile(r"-?\d+(\.\d+)?%")
_BIG_NUM_RE = re.compile(r"\b\d{4,}(\.\d+)?\b")
# 表格分隔行 |---|:---:|
_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|?$")
# ASCII 框图字符（┌─┐│├┤└┘┬┴┼ 等）
_ASCII_BOX_RE = re.compile(r"^[┌┐└┘├┤┬┴┼─│\s║╗╔╝╚╠╣╦╩╬+=|]+")


# ── 规则层：文档切分为块 ────────────────────────────────────────────────────


def _strip_frontmatter(lines: list[str]) -> list[str]:
    """移除 YAML frontmatter（--- ... ---）。"""
    if not lines or lines[0].strip() != "---":
        return lines
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[i + 1 :]
    return lines


def _is_skip_section(heading: str) -> bool:
    """标题是否属于应跳过的段落。"""
    title = heading.lstrip("#").strip()
    return any(kw in title for kw in SKIP_SECTIONS)


def _split_into_blocks(lines: list[str]) -> list[tuple[str, str]]:
    """把文档切分为有序块列表。

    每个块是 ``(kind, content)``：
    - ``("heading", text)``   — 标题（含 # 级数，保留用于段落分隔）
    - ``("table", joined)``   — 表格块（连续 | 行，分隔行已剔除）
    - ``("ascii", "")``       — ASCII 框图（丢弃，内容不可读）
    - ``("text", line)``      — 普通文本行（已去 markdown 标记）

    跳过段落（免责声明等）在此处整体剔除。
    """
    lines = _strip_frontmatter(lines)
    blocks: list[tuple[str, str]] = []
    in_code_block = False
    skip_mode = False

    # 表格行缓冲
    table_buf: list[str] = []

    def flush_table() -> None:
        if table_buf:
            joined = "\n".join(table_buf)
            blocks.append(("table", joined))
            table_buf.clear()

    for raw in lines:
        stripped = raw.strip()

        # 代码块（```）
        if stripped.startswith("```"):
            flush_table()
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        # HTML 注释
        if stripped.startswith("<!--") or stripped.endswith("-->"):
            continue

        # ASCII 框图（整行都是框线字符）
        if stripped and _ASCII_BOX_RE.fullmatch(stripped) and set(stripped) & set("┌┐└┘├┤┬┴┼║╗╔╝╚╠╣╦╩╬"):
            flush_table()
            continue

        # 标题
        if stripped.startswith("#"):
            flush_table()
            heading = stripped.split()[0]  # ### xxx 取 ###
            # 跳过段落：进入/退出 skip 模式
            if _is_skip_section(stripped):
                skip_mode = True
                continue
            skip_mode = False
            blocks.append(("heading", stripped))
            continue

        if skip_mode:
            continue

        # 表格行
        if stripped.startswith("|") and stripped.endswith("|"):
            if _TABLE_SEP_RE.match(stripped):
                continue  # 跳过分隔行
            table_buf.append(_clean_inline(stripped))
            continue

        # 非表格行：先 flush 累积的表格
        flush_table()

        cleaned = _clean_inline(stripped)
        # 纯 URL 行丢弃
        if _URL_LINE_RE.match(stripped):
            continue
        # 空行
        if not cleaned:
            blocks.append(("text", ""))
            continue
        blocks.append(("text", cleaned))

    flush_table()
    return blocks


def _clean_inline(line: str) -> str:
    """去除行内 markdown 标记（保留文本内容）。"""
    # 图片 ![alt](url) → alt
    line = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", line)
    # 链接 [text](url) → text
    line = re.sub(r"\[([^\]]*)\]\([^)]+\)", r"\1", line)
    # 加粗/斜体/代码
    line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
    line = re.sub(r"\*(.+?)\*", r"\1", line)
    line = re.sub(r"`(.+?)`", r"\1", line)
    # 标题/引用/列表标记（行首）
    line = re.sub(r"^#+\s*", "", line)
    line = re.sub(r"^>\s*", "", line)
    line = re.sub(r"^\s*[-*]\s+", "", line)
    # emoji
    line = _EMOJI_RE.sub("", line)
    return line.strip()


# ── 规则层：表格 → 键值叙述（降级路径）─────────────────────────────────────


def _parse_table(table_text: str) -> tuple[list[str], list[list[str]]]:
    """解析表格块文本为 (表头, 数据行)。"""
    rows = [r for r in table_text.split("\n") if r.strip()]
    if not rows:
        return [], []
    header = [c.strip() for c in rows[0].split("|")[1:-1]]
    data = []
    for r in rows[1:]:
        cells = [c.strip() for c in r.split("|")[1:-1]]
        data.append(cells)
    return header, data


def rule_narrate_table(table_text: str) -> str:
    """规则降级：表格 → 键值句式叙述。

    策略：用表头作"键"，取首行、末行、及含极值的行，拼成
    "首行：键 值；末行：键 值" 的句式。信息损失较多但可读、零依赖。
    """
    header, data = _parse_table(table_text)
    if not data:
        return ""

    def fmt_row(row: list[str], label: str) -> str:
        pairs = []
        for i, cell in enumerate(row):
            key = header[i] if i < len(header) else ""
            if cell:
                pairs.append(f"{key}{cell}" if key else cell)
        return f"{label}：{'，'.join(pairs)}" if pairs else ""

    parts = []
    # 首行
    if len(data) >= 1:
        parts.append(fmt_row(data[0], "起始"))
    # 末行（与首行不同时）
    if len(data) >= 2 and data[-1] != data[0]:
        parts.append(fmt_row(data[-1], "最新"))
    # 中间行数据过多（>5）时只取首尾，否则全报
    if 2 < len(data) <= 5:
        for r in data[1:-1]:
            parts.append(fmt_row(r, ""))
    result = "；".join(p for p in parts if p)
    return result + "。" if result else ""


# ── LLM 层：语义叙述化 ──────────────────────────────────────────────────────


_SYSTEM_PROMPT = (
    "你是一位专业的财经播客撰稿人，擅长把表格和数字密集的研报内容改写成"
    "适合「耳朵听」的口语化叙述。你的读者是闭眼听音频的投资者。"
)


def _build_table_prompt(table_text: str) -> str:
    return f"""请把下面这个 Markdown 表格改写成 1-3 句话的口语化叙述，供语音播报。

严格要求：
1. 只许使用表格中真实出现的数字，禁止计算、估算、四舍五入或编造任何数字。
2. 抓住关键趋势：首尾值、极值、拐点。不必逐行罗列，重点说清"从什么变到什么"。
3. 用表头语义解释数字含义（例如"净利润"而非裸数）。
4. 中文口语，自然流畅，避免书面腔。
5. 禁止使用任何 markdown 标记（**、|、# 等），输出纯文本。
6. 1-3 句话，不要分段，不要加引言（如"该表格显示"）。

表格内容：
{table_text}
"""


def _build_dense_prompt(text: str) -> str:
    return f"""请把下面这段数字密集的研报文字改写成 1-3 句话的口语化叙述，供语音播报。

严格要求：
1. 只许使用原文真实出现的数字，禁止计算、估算或编造。
2. 保留最关键的 2-3 个数字，其余可弱化为定性描述（如"大幅下滑""微增"）。
3. 中文口语，自然流畅。
4. 禁止使用任何 markdown 标记，输出纯文本。
5. 1-3 句话，不要分段，不要加引言。

原文：
{text}
"""


def is_number_dense(text: str) -> bool:
    """判断一段文本是否数字密集（≥3 个百分比，或 ≥3 个大数）。"""
    if len(_NUM_DENSE_RE.findall(text)) >= 3:
        return True
    if len(_BIG_NUM_RE.findall(text)) >= 3:
        return True
    return False


def llm_narrate(content: str, kind: str, provider) -> str | None:
    """用 LLM 把表格/数字密集段叙述化。失败返回 None（调用方降级）。"""
    try:
        from stockhot.advisor.exceptions import LLMUnavailableError
    except Exception:
        LLMUnavailableError = Exception  # noqa: N806

    if kind == "table":
        prompt = _build_table_prompt(content)
        max_tokens = 300
    else:  # dense text
        prompt = _build_dense_prompt(content)
        max_tokens = 300

    try:
        resp = provider.complete(prompt=prompt, system=_SYSTEM_PROMPT, max_tokens=max_tokens, temperature=0.3)
        text = resp.content.strip()
        if not text:
            return None
        return text
    except Exception as exc:  # LLMUnavailableError 或任何网络异常
        print(f"  [LLM 降级] {kind} 叙述化失败：{type(exc).__name__}，改用规则", file=sys.stderr)
        return None


def _verify_numbers(source: str, output: str | None) -> bool:
    """校验 output 中的数字是否都来自 source（防 LLM 编造数字）。

    返回 True 表示通过（output 的数字集合是 source 的子集）。
    output 为 None/空时返回 False（视为不可信，触发降级）。

    比较按绝对值：口语化常把 "-15.56%" 说成"下降 15.56%"，
    去掉负号是合理的语言转换，不应判为编造。
    """
    if not output:
        return False
    src_nums = {abs(float(n)) for n in re.findall(r"-?\d+(?:\.\d+)?", source)}
    out_nums = {abs(float(n)) for n in re.findall(r"-?\d+(?:\.\d+)?", output)}
    # output 中出现 source 没有的数字 → 可能是编造
    fabricated = out_nums - src_nums
    return not fabricated


# ── 组装：块序列 → 语音文本 ──────────────────────────────────────────────────


def blocks_to_speech(
    blocks: list[tuple[str, str]],
    use_llm: bool,
    provider=None,
) -> str:
    """把块序列组装为最终语音文本。

    - heading：保留为段落分隔（去掉 # 号，空行隔开）
    - table：LLM 叙述化（或规则降级）
    - text：数字密集时 LLM 重写，否则原样保留
    """
    out_lines: list[str] = []
    llm_count = 0

    for kind, content in blocks:
        if kind == "heading":
            title = content.lstrip("#").strip()
            if title:
                out_lines.append("")  # 段落分隔
                out_lines.append(title)
                out_lines.append("")
            continue

        if kind == "table":
            narrated = None
            if use_llm and provider is not None:
                llm_count += 1
                print(f"  [LLM {llm_count}] 表格叙述化...", file=sys.stderr, flush=True)
                narrated = llm_narrate(content, "table", provider)
                if narrated and not _verify_numbers(content, narrated):
                    print("  [LLM 校验] 检测到疑似编造数字，降级规则叙述", file=sys.stderr)
                    narrated = None
            if narrated is None:
                narrated = rule_narrate_table(content)
            if narrated:
                out_lines.append(narrated)
                out_lines.append("")
            continue

        # text
        if not content:
            if out_lines and out_lines[-1] != "":
                out_lines.append("")
            continue
        if use_llm and provider is not None and is_number_dense(content):
            llm_count += 1
            print(f"  [LLM {llm_count}] 数字密集段叙述化...", file=sys.stderr, flush=True)
            narrated = llm_narrate(content, "dense", provider)
            if narrated and _verify_numbers(content, narrated):
                out_lines.append(narrated)
                out_lines.append("")
                continue
            # 数字校验失败或 LLM 失败 → 原样保留
        out_lines.append(content)

    # 压缩多余空行
    text = "\n".join(out_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── provider 工厂 ───────────────────────────────────────────────────────────

# 推理模型（reasoning model）会把 token 花在 reasoning_content 上，导致改写
# 任务的 content 为空。语音化是确定性改写，无需推理，自动切到非推理模型。
_REASONING_MODELS = {"glm-5.2", "glm-5.1", "glm-4.5"}
_NON_REASONING_FALLBACK = "glm-4-flash"

# Coding Plan 专用端点（智谱订阅套餐）。按量付费端点 paas/v4 会对 Coding
# Plan 用户报 1113（余额不足），需切到 coding 端点才走套餐额度。
_CODING_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"
_PAYG_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"


def _probe_provider(provider) -> str | None:
    """对 provider 做一次极小探针调用，返回错误关键词或 None（成功）。

    用 max_tokens=8 的单字探针，区分「配置 OK」vs「端点/余额/网络问题」。
    """
    try:
        provider.complete(prompt="1", system="", max_tokens=8, temperature=0)
        return None
    except Exception as exc:
        msg = str(exc)
        # 1113 = 余额不足/端点不对（Coding Plan 用户用了按量付费端点）
        if "1113" in msg or "余额不足" in msg:
            return "endpoint"
        return "other"


def _get_llm_provider():
    """获取 LLM provider，自动处理端点与模型选择。失败返回 None（触发规则降级）。

    自动处理（让用户只需配 LLM_API_KEY 即可）：
    1. 推理模型 → 自动切非推理模型（glm-4-flash），避免 content 为空
    2. 按量付费端点报 1113 → 自动切 Coding Plan 端点
    """
    try:
        from stockhot.advisor.llm_provider import get_provider, GLMProvider
    except Exception as exc:
        print(f"  [LLM 不可用] 导入失败 {type(exc).__name__}: {exc}，全程使用规则降级", file=sys.stderr)
        return None

    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if not api_key:
        print("  [LLM 不可用] LLM_API_KEY 未配置，全程使用规则降级", file=sys.stderr)
        return None

    configured_model = os.environ.get("LLM_MODEL", "").strip() or None
    configured_base = os.environ.get("LLM_BASE_URL", "").strip() or None

    # 决定实际使用的模型：推理模型 → 非推理 fallback
    use_model = configured_model
    if configured_model in _REASONING_MODELS:
        print(f"  [LLM] 检测到推理模型 {configured_model}，改写任务自动切换为 {_NON_REASONING_FALLBACK}", file=sys.stderr)
        use_model = _NON_REASONING_FALLBACK

    # 尝试顺序：(base_url, model) 组合，先用户配置，再自动兜底
    candidates = []
    if configured_base:
        candidates.append((configured_base, use_model))
    # 智谱默认两个端点都加入候选（按量付费在前，coding 在后作兜底）
    if configured_base != _PAYG_BASE_URL:
        candidates.append((_PAYG_BASE_URL, use_model))
    candidates.append((_CODING_BASE_URL, use_model))

    last_err = None
    for base_url, model in candidates:
        try:
            provider = GLMProvider(api_key=api_key, base_url=base_url, model=model)
        except Exception as exc:
            last_err = exc
            continue
        err = _probe_provider(provider)
        if err is None:
            if base_url != configured_base:
                print(f"  [LLM] 自动选用端点 {base_url}", file=sys.stderr)
            print(f"  [LLM] 就绪：model={provider.model}", file=sys.stderr)
            return provider
        if err == "endpoint":
            # 1113：这个端点不对，尝试下一个
            continue
        # other 错误（网络/auth）也尝试下一个候选
        last_err = RuntimeError(f"{base_url}: {err}")

    print(f"  [LLM 不可用] 所有端点均失败，全程使用规则降级（最后错误：{last_err}）", file=sys.stderr)
    return None


# ── CLI ─────────────────────────────────────────────────────────────────────


def _resolve_output(input_path: Path, args_output: str | None) -> str:
    """决定输出路径：--output > 镜像 docs_speech/ > 同目录 .txt。"""
    if args_output:
        return args_output
    input_str = str(input_path)
    if input_str.startswith("docs/") or "/docs/" in input_str:
        replaced = input_str.replace("docs/", "docs_speech/", 1) if input_str.startswith("docs/") else input_str.replace(
            "/docs/", "/docs_speech/", 1
        )
        return replaced.replace(".md", ".txt")
    return str(input_path.with_suffix(".txt"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="将 markdown 研报转为适合朗读的口语化纯文本")
    parser.add_argument("input", help="Markdown 研报文件路径")
    parser.add_argument("--no-llm", action="store_true", help="禁用 LLM，全程规则降级（离线/省成本）")
    parser.add_argument("--output", default=None, help="输出路径（默认镜像到 docs_speech/）")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"错误：文件不存在 {input_path}")
        return 1

    output_path = _resolve_output(input_path, args.output)

    # 读取并切分
    md_text = input_path.read_text(encoding="utf-8")
    blocks = _split_into_blocks(md_text.split("\n"))

    # 统计
    n_tables = sum(1 for k, _ in blocks if k == "table")
    n_dense = 0
    for k, c in blocks:
        if k == "text" and is_number_dense(c):
            n_dense += 1

    print(f"输入：{input_path}")
    print(f"切分：{len(blocks)} 块，其中表格 {n_tables} 个、数字密集段 {n_dense} 个")

    # LLM provider
    provider = None
    if not args.no_llm:
        provider = _get_llm_provider()
        if provider is None:
            print("（LLM 不可用，使用规则降级）")
        else:
            print(f"LLM：{type(provider).__name__} / model={provider.model}")
    else:
        print("模式：纯规则（--no-llm）")

    # 转换
    speech_text = blocks_to_speech(blocks, use_llm=provider is not None, provider=provider)

    # 写出
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(speech_text, encoding="utf-8")

    char_count = len(speech_text)
    est_minutes = char_count / 300  # 中文 TTS 约 300 字/分钟
    print(f"\n✅ 完成：{char_count} 字，预计朗读 ~{est_minutes:.0f} 分钟")
    print(f"   {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
