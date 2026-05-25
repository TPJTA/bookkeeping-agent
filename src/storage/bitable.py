import io
import logging
import time
from typing import Any

import lark_oapi as lark
from lark_oapi.api.bitable.v1 import (
    AppTableRecord,
    CreateAppTableRecordRequest,
    GetAppTableRecordRequest,
    ListAppTableFieldRequest,
    UpdateAppTableRecordRequest,
)
from lark_oapi.api.drive.v1 import UploadAllMediaRequest, UploadAllMediaRequestBody
from lark_oapi.api.wiki.v2 import GetNodeSpaceRequest

from src.config import CONFIG

# Field names — must match the column names in the actual Bitable.
# Adjust here if your table uses different names.
F_MERCHANT = "商户"
F_GOODS = "商品"
F_CATEGORY = "类别"
F_AMOUNT = "金额"
F_STATUS = "状态"
F_CONFIDENCE = "置信度"
F_CREATED_AT = "录入时间"
F_CONFIRMED_AT = "确认时间"
F_USER = "用户"
F_SCREENSHOT = "截图"
F_SOURCE = "来源"

STATUS_PENDING = "待确认"
STATUS_CONFIRMED = "已确认"
SOURCE_FEISHU = "飞书"
SOURCE_SHORTCUT = "快捷方式"


logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


# Bitable returns text/select fields as either plain strings or rich-text segment
# lists ([{type, text}, ...]). Normalize to a plain string.
def _str_field(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in v
        )
    return str(v)


class BitableClient:
    def __init__(self) -> None:
        self._client = (
            lark.Client.builder()
            .app_id(CONFIG.lark_app_id)
            .app_secret(CONFIG.lark_app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )
        self._table_id = CONFIG.bitable_table_id
        self._app_token = self._resolve_app_token(CONFIG.bitable_app_token)
        logger.info(
            "resolved app_token=%s table_id=%s",
            self._app_token, self._table_id,
        )

    # Wiki nodes hosting a Bitable return obj_token = real app_token.
    # If the input is already an obj_token (i.e., the URL was /base/ not /wiki/),
    # the wiki API rejects it and we fall back to using the token as-is.
    def _resolve_app_token(self, token: str) -> str:
        req = GetNodeSpaceRequest.builder().token(token).obj_type("wiki").build()
        try:
            resp = self._client.wiki.v2.space.get_node(req)
        except Exception as e:
            logger.warning("wiki resolve raised (%s); using token as raw obj_token", e)
            return token
        if not resp.success():
            logger.warning(
                "wiki resolve failed (code=%s msg=%s); using token as raw obj_token",
                resp.code, resp.msg,
            )
            return token
        return resp.data.node.obj_token

    def list_fields(self) -> list[dict[str, Any]]:
        req = (
            ListAppTableFieldRequest.builder()
            .app_token(self._app_token)
            .table_id(self._table_id)
            .build()
        )
        resp = self._client.bitable.v1.app_table_field.list(req)
        if not resp.success():
            raise RuntimeError(
                f"list_fields failed: code={resp.code} msg={resp.msg}"
            )
        return [
            {"name": f.field_name, "type": f.type, "id": f.field_id}
            for f in resp.data.items
        ]

    def _upload_attachment(self, image_bytes: bytes, file_name: str) -> str:
        # lark-oapi SDK requires a file-like object here, not raw bytes
        body = (
            UploadAllMediaRequestBody.builder()
            .file_name(file_name)
            .parent_type("bitable_image")
            .parent_node(self._app_token)
            .size(len(image_bytes))
            .file(io.BytesIO(image_bytes))
            .build()
        )
        req = UploadAllMediaRequest.builder().request_body(body).build()
        resp = self._client.drive.v1.media.upload_all(req)
        if not resp.success():
            raise RuntimeError(
                f"upload_attachment failed: code={resp.code} msg={resp.msg}"
            )
        return resp.data.file_token

    def create_record(
        self,
        transaction: dict[str, Any],
        image_bytes: bytes,
        user_open_id: str | None = None,
        source: str | None = None,
        confirmed: bool = False,
    ) -> str:
        file_token = self._upload_attachment(
            image_bytes, f"screenshot_{_now_ms()}.png"
        )

        amount_raw = transaction.get("amount", "")
        try:
            amount = float(amount_raw) if amount_raw not in (None, "") else None
        except (TypeError, ValueError):
            amount = None

        fields: dict[str, Any] = {
            F_MERCHANT: transaction.get("merchant", ""),
            F_GOODS: transaction.get("goods", ""),
            F_CATEGORY: transaction.get("category", "其他"),
            F_STATUS: STATUS_CONFIRMED if confirmed else STATUS_PENDING,
            F_CONFIDENCE: float(transaction.get("confidence", 0.0)),
            F_CREATED_AT: _now_ms(),
            F_SCREENSHOT: [{"file_token": file_token}],
        }
        if source:
            fields[F_SOURCE] = source
        if confirmed:
            fields[F_CONFIRMED_AT] = _now_ms()
        if amount is not None:
            fields[F_AMOUNT] = amount
        if user_open_id:
            # 人员 field expects list of {id, id_type}
            fields[F_USER] = [{"id": user_open_id, "id_type": "open_id"}]

        record = AppTableRecord.builder().fields(fields).build()
        req = (
            CreateAppTableRecordRequest.builder()
            .app_token(self._app_token)
            .table_id(self._table_id)
            .request_body(record)
            .build()
        )
        resp = self._client.bitable.v1.app_table_record.create(req)
        if not resp.success():
            raise RuntimeError(
                f"create_pending failed: code={resp.code} msg={resp.msg}"
            )
        return resp.data.record.record_id

    def mark_confirmed(self, record_id: str) -> None:
        self._update_fields(
            record_id,
            {F_STATUS: STATUS_CONFIRMED, F_CONFIRMED_AT: _now_ms()},
        )

    def update_fields(self, record_id: str, fields: dict[str, Any]) -> None:
        self._update_fields(record_id, fields)

    def get_record(self, record_id: str) -> dict[str, Any]:
        req = (
            GetAppTableRecordRequest.builder()
            .app_token(self._app_token)
            .table_id(self._table_id)
            .record_id(record_id)
            .build()
        )
        resp = self._client.bitable.v1.app_table_record.get(req)
        if not resp.success():
            raise RuntimeError(
                f"get_record failed: code={resp.code} msg={resp.msg}"
            )
        return resp.data.record.fields

    def get_transaction(self, record_id: str) -> dict[str, Any]:
        """Read a record and return it shaped like our Transaction dict."""
        fields = self.get_record(record_id)
        amount = fields.get(F_AMOUNT)
        return {
            "is_transaction": True,
            "merchant": _str_field(fields.get(F_MERCHANT)),
            "goods": _str_field(fields.get(F_GOODS)),
            "category": _str_field(fields.get(F_CATEGORY)) or "其他",
            "amount": "" if amount is None else str(amount),
            "confidence": float(fields.get(F_CONFIDENCE) or 0),
        }

    def update_transaction(self, record_id: str, transaction: dict[str, Any]) -> None:
        """Update the editable transaction fields. Does not touch status / time / attachment."""
        amount_raw = transaction.get("amount", "")
        try:
            amount = float(amount_raw) if amount_raw not in (None, "") else None
        except (TypeError, ValueError):
            amount = None

        fields: dict[str, Any] = {
            F_MERCHANT: transaction.get("merchant", ""),
            F_GOODS: transaction.get("goods", ""),
            F_CATEGORY: transaction.get("category", "其他"),
            F_CONFIDENCE: float(transaction.get("confidence", 0)),
        }
        if amount is not None:
            fields[F_AMOUNT] = amount
        self._update_fields(record_id, fields)

    def is_confirmed(self, record_id: str) -> bool:
        fields = self.get_record(record_id)
        return _str_field(fields.get(F_STATUS)) == STATUS_CONFIRMED

    def _update_fields(self, record_id: str, fields: dict[str, Any]) -> None:
        record = AppTableRecord.builder().fields(fields).build()
        req = (
            UpdateAppTableRecordRequest.builder()
            .app_token(self._app_token)
            .table_id(self._table_id)
            .record_id(record_id)
            .request_body(record)
            .build()
        )
        resp = self._client.bitable.v1.app_table_record.update(req)
        if not resp.success():
            raise RuntimeError(
                f"update_fields failed: code={resp.code} msg={resp.msg}"
            )


# Module-level singleton. Constructed once at first import; wiki token
# resolution runs once.
bitable_client = BitableClient()
