import logging
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from src.core.schema import Transaction
from src.core.state import BookkeepingState
from src.llm.glm import call_vision, extract_json
from src.prompts import load_prompt
from src.storage.bitable import bitable_client

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


def node_analyze(state: BookkeepingState) -> dict[str, Any]:
    retry = state.get("retries", 0)
    logger.info("node=analyze retry=%d", retry)
    prompt = load_prompt("recognize")
    if err := state.get("validation_error"):
        prompt += (
            f"\n\n## 上次输出的错误\n"
            f"你上次返回的内容无法通过校验:{err}\n"
            f"请严格按 schema 重新输出 JSON。"
        )
    raw = call_vision(prompt, state["image_bytes"], on_text=state.get("on_progress"))
    return {"raw_output": raw}


def node_validate(state: BookkeepingState) -> dict[str, Any]:
    try:
        parsed = extract_json(state.get("raw_output") or "")
        tx = Transaction.model_validate(parsed)
    except (ValueError, ValidationError) as e:
        retries = state.get("retries", 0) + 1
        logger.warning("node=validate FAIL retry=%d err=%s", retries, e)
        return {"validation_error": str(e), "retries": retries}
    logger.info(
        "node=validate OK is_tx=%s category=%s amount=%s confidence=%.2f",
        tx.is_transaction, tx.category, tx.amount, tx.confidence,
    )
    return {
        "transaction": tx.model_dump(),
        "is_transaction": tx.is_transaction,
        "validation_error": None,
    }


def route_after_validate(
    state: BookkeepingState,
) -> Literal["analyze", "write", "not_tx", "error"]:
    if state.get("validation_error"):
        if state.get("retries", 0) > MAX_RETRIES:
            return "error"
        return "analyze"
    return "write" if state.get("is_transaction") else "not_tx"


def node_write(state: BookkeepingState) -> dict[str, Any]:
    rid = bitable_client.create_record(
        transaction=state["transaction"],
        image_bytes=state["image_bytes"],
        user_open_id=state.get("user_id") or None,
        source=state.get("source") or None,
        confirmed=False,
    )
    logger.info("node=write record_id=%s status=待确认", rid)
    return {"record_id": rid}


# Replies are channel-neutral structured payloads. The channel adapter
# decides how to render them (text / card / etc.).
def node_build_confirm_reply(state: BookkeepingState) -> dict[str, Any]:
    return {
        "reply": {
            "type": "transaction_pending",
            "record_id": state["record_id"],
            "transaction": state["transaction"],
            "source": state.get("source"),
        }
    }


def node_build_not_tx_reply(state: BookkeepingState) -> dict[str, Any]:
    return {
        "reply": {
            "type": "not_transaction",
            "text": "这看起来不是一张交易截图,没有识别到支付信息。",
        }
    }


def node_build_error_reply(state: BookkeepingState) -> dict[str, Any]:
    err = state.get("validation_error") or state.get("error") or "未知错误"
    return {
        "reply": {
            "type": "error",
            "text": f"识别失败(已重试 {state.get('retries', 0)} 次):{err}",
        }
    }


def build_graph():
    g = StateGraph(BookkeepingState)
    g.add_node("analyze", node_analyze)
    g.add_node("validate", node_validate)
    g.add_node("write", node_write)
    g.add_node("build_confirm_reply", node_build_confirm_reply)
    g.add_node("build_not_tx_reply", node_build_not_tx_reply)
    g.add_node("build_error_reply", node_build_error_reply)

    g.add_edge(START, "analyze")
    g.add_edge("analyze", "validate")
    g.add_conditional_edges(
        "validate",
        route_after_validate,
        {
            "analyze": "analyze",
            "write": "write",
            "not_tx": "build_not_tx_reply",
            "error": "build_error_reply",
        },
    )
    g.add_edge("write", "build_confirm_reply")
    g.add_edge("build_confirm_reply", END)
    g.add_edge("build_not_tx_reply", END)
    g.add_edge("build_error_reply", END)
    return g.compile()


GRAPH = build_graph()
