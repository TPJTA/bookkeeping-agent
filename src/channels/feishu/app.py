import json
import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from src.channels.feishu import cards
from src.channels.feishu.client import FeishuClient
from src.config import CONFIG
from src.core import actions as core_actions
from src.core.graph import GRAPH
from src.core.state import BookkeepingState

logger = logging.getLogger(__name__)

# downloads/ at project root: src/channels/feishu/app.py -> parents[3]
DOWNLOADS_DIR = Path(__file__).resolve().parents[3] / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

_feishu = FeishuClient()

# Process-local idempotency: Lark can re-deliver an event if our handler
# doesn't ack fast enough. A bounded LRU set is enough for a single-process
# MVP; for multi-instance deployment this would move to a shared store.
_SEEN_MAX = 1024
_seen_ids: OrderedDict[str, None] = OrderedDict()
_seen_lock = Lock()

# card_msg_id -> record_id, so when user replies to a confirm card we know
# which record to modify. Bounded LRU; on miss the modify reply is rejected.
_CARD_MAP_MAX = 256
_card_to_record: OrderedDict[str, str] = OrderedDict()
_card_map_lock = Lock()


def _claim_message(msg_id: str) -> bool:
    """Return True if this msg_id is new (claim it); False if already seen."""
    with _seen_lock:
        if msg_id in _seen_ids:
            return False
        _seen_ids[msg_id] = None
        while len(_seen_ids) > _SEEN_MAX:
            _seen_ids.popitem(last=False)
        return True


def _remember_card(card_msg_id: str, record_id: str) -> None:
    with _card_map_lock:
        _card_to_record[card_msg_id] = record_id
        _card_to_record.move_to_end(card_msg_id)
        while len(_card_to_record) > _CARD_MAP_MAX:
            _card_to_record.popitem(last=False)


def _forget_card(card_msg_id: str) -> None:
    with _card_map_lock:
        _card_to_record.pop(card_msg_id, None)


def _lookup_card_record(card_msg_id: str) -> str | None:
    with _card_map_lock:
        rid = _card_to_record.get(card_msg_id)
        if rid:
            _card_to_record.move_to_end(card_msg_id)
        return rid


def _on_message(event: P2ImMessageReceiveV1) -> None:
    """Synchronous handler: dedupe, parse, then dispatch heavy work to a thread
    so the WebSocket handler returns quickly and Lark doesn't re-deliver."""
    if not event.event or not event.event.message:
        logger.warning("event has no message payload, skip")
        return
    msg = event.event.message

    msg_id = msg.message_id or ""
    chat_id = msg.chat_id or ""
    if not msg_id or not chat_id:
        logger.warning("skip incomplete event: msg_id=%r chat_id=%r", msg_id, chat_id)
        return

    if not _claim_message(msg_id):
        logger.info("skip duplicate message_id=%s", msg_id)
        return

    sender = event.event.sender
    open_id = ""
    if sender and sender.sender_id and sender.sender_id.open_id:
        open_id = sender.sender_id.open_id

    if msg.message_type == "image":
        content = json.loads(msg.content or "{}")
        image_key = content.get("image_key", "")
        if not image_key:
            logger.warning("skip image without image_key")
            return
        logger.info("recv image msg_id=%s chat_id=%s open_id=%s", msg_id, chat_id, open_id)
        threading.Thread(
            target=_process_image,
            args=(msg_id, image_key, open_id),
            daemon=True,
        ).start()
        return

    if msg.message_type == "text":
        parent_id = msg.parent_id or ""
        if not parent_id:
            logger.info("skip text: not a reply (no parent_id)")
            return
        record_id = _lookup_card_record(parent_id)
        if not record_id:
            logger.info("skip text: parent_id=%s not in card->record map", parent_id)
            return
        content = json.loads(msg.content or "{}")
        user_text = (content.get("text") or "").strip()
        if not user_text:
            logger.info("skip text: empty content")
            return
        logger.info("recv modify-reply msg_id=%s parent_id=%s record_id=%s text=%r",
                    msg_id, parent_id, record_id, user_text)
        threading.Thread(
            target=_process_modify,
            args=(msg_id, parent_id, record_id, user_text),
            daemon=True,
        ).start()
        return

    logger.info("skip unsupported message type=%s", msg.message_type)


def _process_image(msg_id: str, image_key: str, open_id: str) -> None:
    """Heavy path: send placeholder card, download, run graph (streams into the
    card), then update card to the final result."""
    try:
        card_msg_id = _feishu.reply_card(msg_id, cards.pending_card())
        logger.info("sent pending card card_msg_id=%s", card_msg_id)
    except Exception:
        logger.exception("failed to send pending card; aborting")
        return

    try:
        data = _feishu.download_image(msg_id, image_key)
    except Exception as e:
        logger.exception("download failed")
        _safe_update(card_msg_id, cards.error_card(f"图片下载失败:{e}"))
        return

    out = DOWNLOADS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{msg_id}.png"
    out.write_bytes(data)
    logger.info("saved %s (%d bytes)", out.relative_to(DOWNLOADS_DIR.parent), len(data))

    updater = _StreamUpdater(card_msg_id)
    initial: BookkeepingState = {
        "image_bytes": data,
        "user_id": open_id,
        "request_id": msg_id,
        "retries": 0,
        "on_progress": updater,
    }
    try:
        result = GRAPH.invoke(initial)
    except Exception as e:
        logger.exception("graph failed")
        _safe_update(card_msg_id, cards.error_card(f"处理失败:{e}"))
        return

    logger.info(
        "graph done record_id=%s retries=%d",
        result.get("record_id"), result.get("retries", 0),
    )

    reply = result.get("reply") or {}
    card = _reply_to_card(reply)
    _safe_update(card_msg_id, card)

    # Remember card -> record so user's reply (modify) can locate the record.
    record_id = result.get("record_id")
    if record_id and reply.get("type") == "transaction_pending":
        _remember_card(card_msg_id, record_id)
        logger.info("remembered card_msg_id=%s -> record_id=%s", card_msg_id, record_id)


def _process_modify(
    reply_msg_id: str, old_card_msg_id: str, record_id: str, user_text: str,
) -> None:
    """User replied to a 待确认 card with a natural-language modification.

    Flow: keep the old card untouched until the modify succeeds; send a NEW
    card as a reply to the user's text message and stream into that one. On
    success, mark the old card invalidated and switch the card->record map to
    the new card.
    """
    # Status guard: confirmed records can no longer be modified via chat
    try:
        if core_actions.is_confirmed(record_id):
            _safe_reply_to(reply_msg_id, "该记录已确认,请去多维表格中自行修改。")
            return
        # Snapshot pre-modify values for use on the invalidated card later.
        old_tx = core_actions.get_transaction(record_id)
    except Exception as e:
        logger.exception("modify: status / read failed")
        _safe_reply_to(reply_msg_id, f"读取记录失败:{e}")
        return

    # Send a NEW pending card as a reply to the user's modification message.
    try:
        new_card_msg_id = _feishu.reply_card(reply_msg_id, cards.pending_card())
        logger.info("sent new pending card card_msg_id=%s", new_card_msg_id)
    except Exception:
        logger.exception("failed to send new pending card")
        _safe_reply_to(reply_msg_id, "处理失败:无法发送新卡片")
        return

    # Stream model output into the NEW card
    updater = _StreamUpdater(new_card_msg_id)
    try:
        result = core_actions.modify(record_id, user_text, on_progress=updater)
    except Exception as e:
        logger.exception("modify failed")
        _safe_update(new_card_msg_id, cards.error_card(f"修改失败:{e}"))
        return

    rtype = result.get("type")
    logger.info("modify done type=%s record_id=%s", rtype, record_id)

    if rtype == "transaction_pending":
        # Modification applied → finalize new card, invalidate old card, swap map.
        _safe_update(new_card_msg_id, cards.confirm_card(record_id, result["transaction"]))
        _safe_update(old_card_msg_id, cards.invalidated_card(old_tx))
        _forget_card(old_card_msg_id)
        _remember_card(new_card_msg_id, record_id)
        return

    if rtype == "no_modification":
        # No real change → drop the new card to an info state, leave old card untouched.
        _safe_update(new_card_msg_id, cards.no_modification_card())
        return

    # parse / validation error
    _safe_update(new_card_msg_id, cards.error_card(result.get("text", "未知错误")))


class _StreamUpdater:
    """Throttled card-patching callback. Called by the LLM streaming loop with
    the accumulating text; pushes a typing_card update at most once per `interval`."""

    def __init__(self, card_msg_id: str, interval: float = 0.4) -> None:
        self.card_msg_id = card_msg_id
        self.interval = interval
        self.last_update = 0.0

    def __call__(self, text: str) -> None:
        now = time.monotonic()
        if now - self.last_update < self.interval:
            return
        try:
            _feishu.update_card(self.card_msg_id, cards.typing_card(text))
            self.last_update = now
        except Exception:
            logger.warning("typing card update failed (continuing stream)")


def _reply_to_card(reply: dict[str, Any]) -> dict[str, Any]:
    rtype = reply.get("type")
    if rtype == "transaction_pending":
        return cards.confirm_card(reply["record_id"], reply["transaction"])
    if rtype == "not_transaction":
        return cards.not_transaction_card(reply.get("text", ""))
    if rtype == "error":
        return cards.error_card(reply.get("text", ""))
    return cards.error_card(f"未知 reply 类型:{rtype}")


def _on_card_action(req: Any) -> Any:
    """Handle clicks on card buttons (the only one for now: 「确认」)."""
    try:
        # Dump full request once so we know the exact attribute paths on this SDK version
        try:
            logger.info("card action raw=%s", lark.JSON.marshal(req))
        except Exception:
            logger.info("card action req=%r", req)

        action = req.event.action
        value = action.value or {}
        card_msg_id = req.event.context.open_message_id
        logger.info("card action value=%s card_msg_id=%s", value, card_msg_id)

        if value.get("action") != "confirm":
            logger.info("ignore unknown card action: %s", value)
            return

        record_id = value.get("record_id")
        if not record_id:
            logger.warning("confirm action missing record_id")
            return

        threading.Thread(
            target=_process_confirm,
            args=(record_id, card_msg_id),
            daemon=True,
        ).start()
    except Exception:
        logger.exception("card action handler crashed")


def _process_confirm(record_id: str, card_msg_id: str) -> None:
    logger.info("process_confirm record_id=%s card_msg_id=%s", record_id, card_msg_id)
    try:
        core_actions.confirm(record_id)
        logger.info("bitable mark_confirmed OK record_id=%s", record_id)
    except Exception as e:
        logger.exception("step=confirm failed")
        _safe_update(card_msg_id, cards.error_card(f"确认失败:{e}"))
        return

    try:
        fields = core_actions.get_record(record_id)
        tx = {
            "merchant": fields.get("商户", ""),
            "goods": fields.get("商品", ""),
            "category": fields.get("类别", ""),
            "amount": str(fields.get("金额", "")),
            "confidence": float(fields.get("置信度") or 0),
        }
        _safe_update(card_msg_id, cards.confirmed_card(record_id, tx))
        logger.info("card updated to confirmed_card")
    except Exception:
        logger.exception("step=post-confirm card update failed")


def _safe_update(card_message_id: str, card: dict[str, Any]) -> None:
    try:
        _feishu.update_card(card_message_id, card)
    except Exception:
        logger.exception("update_card failed")


def _safe_reply_to(message_id: str, text: str) -> None:
    try:
        _feishu.reply_text(message_id, text)
    except Exception:
        logger.exception("reply_text failed")


def main() -> None:
    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(_on_message)
        .register_p2_card_action_trigger(_on_card_action)
        .build()
    )
    ws = lark.ws.Client(
        CONFIG.lark_app_id,
        CONFIG.lark_app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )
    logger.info("starting feishu ws long connection...")
    ws.start()


if __name__ == "__main__":
    main()
