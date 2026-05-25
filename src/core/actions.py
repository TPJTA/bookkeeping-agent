import json
import logging
from typing import Any, Callable, Optional

from pydantic import ValidationError

from src.core.schema import ModifyResult
from src.llm.glm import call_text, extract_json
from src.prompts import load_prompt
from src.storage.bitable import RecordNotFoundError, bitable_client

logger = logging.getLogger(__name__)

MAX_MODIFY_RETRIES = 2


def confirm(record_id: str) -> None:
    """Mark a pending record as 已确认. Called when user clicks the confirm button."""
    logger.info("action=confirm record_id=%s", record_id)
    bitable_client.mark_confirmed(record_id)


def cancel(record_id: str) -> dict[str, Any]:
    """Delete a pending bookkeeping candidate from Bitable."""
    logger.info("action=cancel record_id=%s", record_id)
    try:
        fields = bitable_client.get_record(record_id)
    except RecordNotFoundError:
        logger.info("cancel: record already missing record_id=%s", record_id)
        return {"type": "already_cancelled", "record_id": record_id}

    tx = bitable_client.transaction_from_fields(fields)
    if bitable_client.fields_are_confirmed(fields):
        logger.info("cancel: record already confirmed record_id=%s", record_id)
        return {
            "type": "confirmed",
            "record_id": record_id,
            "transaction": tx,
        }

    try:
        bitable_client.delete_record(record_id)
    except RecordNotFoundError:
        logger.info("cancel: record disappeared during delete record_id=%s", record_id)

    logger.info("cancel: deleted record_id=%s", record_id)
    return {
        "type": "cancelled",
        "record_id": record_id,
        "transaction": tx,
    }


def get_record(record_id: str) -> dict:
    """Raw bitable fields, for callers that need to inspect status etc."""
    return bitable_client.get_record(record_id)


def get_transaction(record_id: str) -> dict:
    """Transaction-shaped read of a record."""
    return bitable_client.get_transaction(record_id)


def is_confirmed(record_id: str) -> bool:
    return bitable_client.is_confirmed(record_id)


def modify(
    record_id: str,
    user_text: str,
    on_progress: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Apply a natural-language modification to a record.

    Returns one of:
      {"type": "transaction_pending", "record_id": rid, "transaction": {...}}
      {"type": "no_modification"}
      {"type": "error", "text": "..."}
    """
    logger.info("action=modify record_id=%s user_text=%r", record_id, user_text)
    current_tx = bitable_client.get_transaction(record_id)
    base_prompt = load_prompt(
        "modify",
        current_json=json.dumps(current_tx, ensure_ascii=False),
        user_text=user_text,
    )

    last_err: str | None = None
    merged: ModifyResult | None = None
    for attempt in range(MAX_MODIFY_RETRIES + 1):
        prompt = base_prompt if not last_err else (
            base_prompt
            + f"\n\n## 上次输出错误\n{last_err}\n请严格按 schema 重新输出 JSON。"
        )
        raw = call_text(prompt, on_text=on_progress)
        try:
            parsed = extract_json(raw)
            merged = ModifyResult.model_validate(parsed)
            last_err = None
            break
        except (ValueError, ValidationError) as e:
            last_err = str(e)
            logger.warning("modify validate FAIL attempt=%d err=%s", attempt + 1, e)

    if merged is None:
        return {"type": "error", "text": f"修改解析失败:{last_err}"}

    if not merged.is_modification:
        logger.info("modify: model says not a modification intent")
        return {"type": "no_modification"}

    new_tx = merged.transaction.model_dump()
    bitable_client.update_transaction(record_id, new_tx)
    logger.info("modify: updated record_id=%s", record_id)
    return {
        "type": "transaction_pending",
        "record_id": record_id,
        "transaction": new_tx,
    }
