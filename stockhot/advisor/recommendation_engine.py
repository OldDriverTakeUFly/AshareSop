"""Recommendation engine — LLM-backed advice with hallucination guard.

Pipeline: AggregatedSignals + ArbitrationResult → prompt template
selection → LLM call → JSON parse → hallucination guard → persist.

The LLM NEVER arbitrates conflicts (T5's hardcoded rules do that).
The LLM NEVER fabricates data on failure — LLMUnavailableError
produces action="NO_ACTION", confidence="LOW".
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from stockhot.advisor.conflict_resolver import ArbitrationResult
from stockhot.advisor.exceptions import LLMUnavailableError
from stockhot.advisor.llm_provider import LLMProvider, LLMResponse, get_provider
from stockhot.advisor.prompts.registry import PromptRegistry, PromptTemplate, default_registry
import stockhot.advisor.prompts.templates  # noqa: F401 — registers templates
from stockhot.advisor.signal_aggregator import AggregatedSignals
from stockhot.storage.database import get_connection

T_TRADE_SUPPORT_PCT = 0.02
T_TRADE_VOLUME_RATIO = 1.2
T_TRADE_MAX_POSITION_PCT = 0.20


class IdempotencyError(Exception):
    pass


@dataclass
class Recommendation:
    code: str
    recommendation_type: str
    action: str
    confidence: str
    entry_zone: tuple[float, float] | None = None
    stop_loss: float | None = None
    target: float | None = None
    reasoning: str = ""
    prompt_version: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_name: str = ""


def _check_t_trade_conditions(
    aggregated: AggregatedSignals,
    holding: dict | None,
) -> bool:
    if holding is None:
        return False

    current_price = aggregated.realtime_price.get("current_price")
    if not current_price or current_price <= 0:
        return False

    details = aggregated.technical.details
    support_list = details.get("support_levels", [])
    if not support_list:
        return False

    nearest_support = min(support_list, key=lambda s: abs(s - current_price))
    if nearest_support <= 0:
        return False
    pct_to_support = abs(current_price - nearest_support) / current_price
    if pct_to_support > T_TRADE_SUPPORT_PCT:
        return False

    volume_ratio = details.get("volume_ratio", 0.0)
    if volume_ratio <= T_TRADE_VOLUME_RATIO:
        return False

    return True


def _map_action_to_type(
    arbitration: ArbitrationResult,
    aggregated: AggregatedSignals,
    holding: dict | None,
) -> str | None:
    if holding is not None and _check_t_trade_conditions(aggregated, holding):
        return "t_trade"

    action = arbitration.action
    has_holding = holding is not None

    if action == "EXIT":
        return "clear"
    if action == "TRIM":
        return "adjust"
    if action == "BUY":
        return "build"
    if action == "HOLD":
        if has_holding:
            return "adjust"
        return None

    return None


def _build_context(
    code: str,
    aggregated: AggregatedSignals,
    arbitration: ArbitrationResult,
    holding: dict | None,
) -> dict:
    rp = aggregated.realtime_price
    tech = aggregated.technical
    davis = aggregated.davis

    current_price = rp.get("current_price") or "N/A"
    tech_score = tech.value if tech.value != "" else "N/A"
    tech_state = tech.details.get("state", "N/A")
    davis_score = davis.value if davis.value != "" else "N/A"
    davis_pct = davis.details.get("percentile_rank", "N/A")

    details = tech.details
    support_levels = details.get("support_levels", [])
    resistance_levels = details.get("resistance_levels", [])
    volume_ratio = details.get("volume_ratio", "N/A")

    triggered = [s["signal_type"] for s in aggregated.sell_signals if s.get("triggered")]
    triggered_str = ", ".join(triggered) if triggered else "none"

    position_pct = holding.get("position_pct", "N/A") if holding else "N/A"
    avg_cost = holding.get("avg_cost", "N/A") if holding else "N/A"
    stop_loss_hard = holding.get("stop_loss_hard", "N/A") if holding else "N/A"

    unrealized_pnl = "N/A"
    if holding and avg_cost != "N/A" and current_price != "N/A":
        try:
            unrealized_pnl = round(
                (float(current_price) - float(avg_cost)) / float(avg_cost) * 100, 2
            )
        except (ValueError, TypeError, ZeroDivisionError):
            pass

    return {
        "code": code,
        "current_price": current_price,
        "technical_score": tech_score,
        "technical_state": tech_state,
        "davis_score": davis_score,
        "davis_percentile": davis_pct,
        "support_levels": support_levels or "N/A",
        "resistance_levels": resistance_levels or "N/A",
        "volume_ratio": volume_ratio,
        "position_pct": position_pct,
        "signals": triggered_str,
        "avg_cost": avg_cost,
        "unrealized_pnl_pct": unrealized_pnl,
        "triggered_signals": triggered_str,
        "stop_loss_hard": stop_loss_hard,
        "thesis_status": arbitration.scenario,
        "recent_volume_trend": details.get("volume_trend", "N/A"),
    }


_TEMPLATE_TYPE_MAP = {
    "build": "build_position",
    "adjust": "adjust_position",
    "clear": "clear_position",
    "t_trade": "t_trade",
}


def _hallucination_check(
    parsed: dict,
    current_price: float | None,
) -> str | None:
    if current_price is None or current_price <= 0:
        return None

    upper_reasonable = current_price * 2
    target_ceiling = current_price * 10

    entry = parsed.get("entry_zone") or parsed.get("intraday_buy_zone")
    if entry and isinstance(entry, (list, tuple)) and len(entry) == 2:
        low, high = float(entry[0]), float(entry[1])
        if low < 0 or high > upper_reasonable:
            return "entry_zone outside reasonable range"

    sl = parsed.get("stop_loss")
    if sl is not None and isinstance(sl, (int, float)):
        if sl <= 0 or sl > upper_reasonable:
            return "stop_loss outside reasonable range"

    tgt = parsed.get("target")
    if tgt is not None and isinstance(tgt, (int, float)):
        if tgt <= 0 or tgt > target_ceiling:
            return "target outside reasonable range"

    return None


_T_TRADE_DISCLAIMER = "⚠️ 做T建议仅供参考，基于日线推断，风险较高。"


def generate_recommendation(
    code: str,
    aggregated: AggregatedSignals,
    arbitration: ArbitrationResult,
    prompt_registry: PromptRegistry,
    provider: LLMProvider | None = None,
    holding: dict | None = None,
) -> Recommendation:
    rec_type = _map_action_to_type(arbitration, aggregated, holding)

    if rec_type is None:
        return Recommendation(
            code=code,
            recommendation_type="none",
            action="NO_ACTION",
            confidence="LOW",
            reasoning="No actionable signal for candidate stock with HOLD arbitration",
        )

    template_name = _TEMPLATE_TYPE_MAP[rec_type]
    template = prompt_registry.get(template_name)

    context = _build_context(code, aggregated, arbitration, holding)

    try:
        user_prompt = template.user_template.format(**context)
    except KeyError:
        user_prompt = template.user_template

    current_price = aggregated.realtime_price.get("current_price")
    current_price_f = float(current_price) if current_price else None

    if provider is None:
        try:
            provider = get_provider()
        except (EnvironmentError, ValueError):
            return _llm_unavailable_rec(code, rec_type, template)

    try:
        response: LLMResponse = provider.complete(
            prompt=user_prompt,
            system=template.system,
            max_tokens=800,
            temperature=0.3,
        )
    except LLMUnavailableError:
        return _llm_unavailable_rec(code, rec_type, template)

    try:
        parsed = json.loads(response.content)
    except (json.JSONDecodeError, TypeError):
        parsed = {}

    action = parsed.get("action", "hold")
    confidence = parsed.get("confidence", parsed.get("urgency", "MEDIUM"))
    reasoning = parsed.get("reasoning", parsed.get("disclaimer", ""))

    entry = parsed.get("entry_zone") or parsed.get("intraday_buy_zone")
    entry_zone = None
    if entry and isinstance(entry, (list, tuple)) and len(entry) == 2:
        entry_zone = (float(entry[0]), float(entry[1]))

    stop_loss = parsed.get("stop_loss")
    if stop_loss is not None and isinstance(stop_loss, (int, float)):
        stop_loss = float(stop_loss)
    else:
        stop_loss = None

    target = parsed.get("target")
    if target is not None and isinstance(target, (int, float)):
        target = float(target)
    else:
        target = None

    warning = _hallucination_check(parsed, current_price_f)
    if warning:
        confidence = "LOW"
        reasoning = f"[HALLUCINATION GUARD: {warning}] {reasoning}"

    if rec_type == "t_trade":
        confidence = "LOW"
        reasoning = f"{reasoning} {_T_TRADE_DISCLAIMER}".strip()

    return Recommendation(
        code=code,
        recommendation_type=rec_type,
        action=action,
        confidence=confidence,
        entry_zone=entry_zone,
        stop_loss=stop_loss,
        target=target,
        reasoning=reasoning,
        prompt_version=template.version,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        model_name=response.model,
    )


def _llm_unavailable_rec(code: str, rec_type: str, template: PromptTemplate) -> Recommendation:
    return Recommendation(
        code=code,
        recommendation_type=rec_type,
        action="NO_ACTION",
        confidence="LOW",
        reasoning="LLM unavailable — no recommendation generated",
        prompt_version=template.version,
    )


def persist_recommendation(rec: Recommendation, trade_date: str) -> int:
    reasoning_json = json.dumps(
        {
            "reasoning": rec.reasoning,
            "entry_zone": list(rec.entry_zone) if rec.entry_zone else None,
            "stop_loss": rec.stop_loss,
            "target": rec.target,
        },
        ensure_ascii=False,
    )

    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO advisor_runs
               (trade_date, stock_code, recommendation_type, action, confidence,
                reasoning_json, prompt_version, prompt_tokens, completion_tokens,
                model_name, data_age_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade_date,
                rec.code,
                rec.recommendation_type,
                rec.action,
                rec.confidence,
                reasoning_json,
                rec.prompt_version,
                rec.prompt_tokens,
                rec.completion_tokens,
                rec.model_name,
                json.dumps({}),
            ),
        )
        conn.commit()
        rowid = cursor.lastrowid or 0
        return rowid
    except Exception as exc:
        conn.rollback()
        if "UNIQUE" in str(exc):
            raise IdempotencyError(
                f"Recommendation already exists for {trade_date}/{rec.code}/{rec.recommendation_type}"
            ) from exc
        raise
    finally:
        conn.close()


def _delete_existing(trade_date: str, code: str, rec_type: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM advisor_runs WHERE trade_date=? AND stock_code=? AND recommendation_type=?",
            (trade_date, code, rec_type),
        )
        conn.commit()
    finally:
        conn.close()


def run_for_stock(
    code: str,
    trade_date: str,
    holding: dict | None = None,
    force: bool = False,
    provider: LLMProvider | None = None,
) -> Recommendation:
    from stockhot.advisor.signal_aggregator import aggregate_signals
    from stockhot.advisor.conflict_resolver import arbitrate

    aggregated = aggregate_signals(code, holding)
    arbitration = arbitrate(aggregated)

    rec = generate_recommendation(
        code,
        aggregated,
        arbitration,
        default_registry,
        provider=provider,
        holding=holding,
    )

    if rec.recommendation_type == "none":
        return rec

    if force:
        _delete_existing(trade_date, code, rec.recommendation_type)

    try:
        persist_recommendation(rec, trade_date)
    except IdempotencyError:
        if not force:
            pass

    return rec
