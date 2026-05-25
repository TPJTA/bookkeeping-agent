import json
import io
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    GetMessageResourceRequest,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from src.config import CONFIG


class FeishuClient:
    def __init__(self) -> None:
        self._client = (
            lark.Client.builder()
            .app_id(CONFIG.lark_app_id)
            .app_secret(CONFIG.lark_app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )

    def download_image(self, message_id: str, image_key: str) -> bytes:
        req = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(image_key)
            .type("image")
            .build()
        )
        resp = self._client.im.v1.message_resource.get(req)
        if not resp.success():
            raise RuntimeError(
                f"download_image failed: code={resp.code} msg={resp.msg} "
                f"log_id={getattr(resp, 'get_log_id', lambda: '?')()}"
            )
        return resp.file.read()

    def send_text(self, chat_id: str, text: str) -> None:
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        resp = self._client.im.v1.message.create(req)
        if not resp.success():
            raise RuntimeError(
                f"send_text failed: code={resp.code} msg={resp.msg}"
            )

    def send_card(self, chat_id: str, card: dict[str, Any]) -> str:
        """Send an interactive card to a chat. Returns the new message_id."""
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        resp = self._client.im.v1.message.create(req)
        if not resp.success():
            raise RuntimeError(f"send_card failed: code={resp.code} msg={resp.msg}")
        return resp.data.message_id

    def upload_image(self, image_bytes: bytes) -> str:
        """Upload an image for use in Feishu messages/cards. Returns image_key."""
        body = (
            CreateImageRequestBody.builder()
            .image_type("message")
            .image(io.BytesIO(image_bytes))
            .build()
        )
        req = CreateImageRequest.builder().request_body(body).build()
        resp = self._client.im.v1.image.create(req)
        if not resp.success():
            raise RuntimeError(
                f"upload_image failed: code={resp.code} msg={resp.msg}"
            )
        return resp.data.image_key

    def reply_text(self, message_id: str, text: str) -> None:
        body = (
            ReplyMessageRequestBody.builder()
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .msg_type("text")
            .reply_in_thread(False)
            .build()
        )
        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        resp = self._client.im.v1.message.reply(req)
        if not resp.success():
            raise RuntimeError(
                f"reply_text failed: code={resp.code} msg={resp.msg}"
            )

    def reply_card(self, message_id: str, card: dict[str, Any]) -> str:
        """Reply to a message with an interactive card. Returns the new card's message_id."""
        body = (
            ReplyMessageRequestBody.builder()
            .content(json.dumps(card, ensure_ascii=False))
            .msg_type("interactive")
            .reply_in_thread(False)
            .build()
        )
        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        resp = self._client.im.v1.message.reply(req)
        if not resp.success():
            raise RuntimeError(f"reply_card failed: code={resp.code} msg={resp.msg}")
        return resp.data.message_id

    def update_card(self, card_message_id: str, card: dict[str, Any]) -> None:
        body = (
            PatchMessageRequestBody.builder()
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        req = (
            PatchMessageRequest.builder()
            .message_id(card_message_id)
            .request_body(body)
            .build()
        )
        resp = self._client.im.v1.message.patch(req)
        if not resp.success():
            raise RuntimeError(f"update_card failed: code={resp.code} msg={resp.msg}")
