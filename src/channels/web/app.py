import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status

from src.channels.feishu import cards
from src.channels.feishu.app import _StreamUpdater, _safe_update, remember_card, reply_to_card
from src.channels.feishu.client import FeishuClient
from src.core.graph import GRAPH
from src.core.state import BookkeepingState
from src.storage.bitable import SOURCE_SHORTCUT

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}

DOWNLOADS_DIR = Path(__file__).resolve().parents[3] / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Bookkeeping Agent Webhook")
_feishu = FeishuClient()


@app.post("/webhook/order-screenshot", status_code=status.HTTP_202_ACCEPTED)
async def order_screenshot(
    image: Annotated[UploadFile, File()],
    web_review_chat_id: Annotated[str, Form(alias="WEB_REVIEW_CHAT_ID")],
) -> dict[str, str | bool]:
    web_review_chat_id = web_review_chat_id.strip()
    if not web_review_chat_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="参数错误",
        )
    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="unsupported image type",
        )

    image_bytes = await image.read(MAX_IMAGE_BYTES + 1)
    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="empty image",
        )
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="image too large",
        )

    request_id = _new_request_id()
    logger.info(
        "accepted web screenshot request_id=%s chat_id=%s content_type=%s size=%d filename=%r",
        request_id,
        web_review_chat_id,
        image.content_type,
        len(image_bytes),
        image.filename,
    )
    threading.Thread(
        target=_process_web_image,
        args=(request_id, image_bytes, web_review_chat_id),
        daemon=True,
    ).start()
    return {"ok": True, "request_id": request_id, "status": "accepted"}


def _new_request_id() -> str:
    return f"web_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"


def _process_web_image(
    request_id: str,
    image_bytes: bytes,
    web_review_chat_id: str,
) -> None:
    card_msg_id = ""
    try:
        card_msg_id = _feishu.send_card(
            web_review_chat_id,
            cards.pending_card(SOURCE_SHORTCUT),
        )
        logger.info("sent web pending card request_id=%s card_msg_id=%s", request_id, card_msg_id)
    except Exception:
        logger.exception("web: failed to send pending card request_id=%s", request_id)
        return

    _save_image(request_id, image_bytes)

    image_key = None
    try:
        image_key = _feishu.upload_image(image_bytes)
        logger.info("web: uploaded card image request_id=%s image_key=%s", request_id, image_key)
    except Exception:
        logger.warning(
            "web: card image upload failed request_id=%s; continuing without screenshot",
            request_id,
            exc_info=True,
        )

    updater = _StreamUpdater(card_msg_id, source=SOURCE_SHORTCUT)
    initial: BookkeepingState = {
        "image_bytes": image_bytes,
        "request_id": request_id,
        "source": SOURCE_SHORTCUT,
        "retries": 0,
        "on_progress": updater,
    }
    try:
        result = GRAPH.invoke(initial)
    except Exception as e:
        logger.exception("web: graph failed request_id=%s", request_id)
        _safe_update(card_msg_id, cards.error_card(f"处理失败:{e}", SOURCE_SHORTCUT))
        return

    logger.info(
        "web graph done request_id=%s record_id=%s retries=%d",
        request_id,
        result.get("record_id"),
        result.get("retries", 0),
    )

    reply = result.get("reply") or {}
    _safe_update(card_msg_id, reply_to_card(reply, SOURCE_SHORTCUT, image_key))

    record_id = result.get("record_id")
    if record_id and reply.get("type") == "transaction_pending":
        remember_card(
            card_msg_id,
            record_id,
            source=SOURCE_SHORTCUT,
            image_key=image_key,
        )
        logger.info(
            "web: remembered card_msg_id=%s -> record_id=%s request_id=%s",
            card_msg_id,
            record_id,
            request_id,
        )


def _save_image(request_id: str, image_bytes: bytes) -> None:
    try:
        out = DOWNLOADS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{request_id}.png"
        out.write_bytes(image_bytes)
        logger.info("web: saved %s (%d bytes)", out.relative_to(DOWNLOADS_DIR.parent), len(image_bytes))
    except Exception:
        logger.warning("web: failed to save image request_id=%s", request_id, exc_info=True)
