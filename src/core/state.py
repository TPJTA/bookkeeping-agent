from typing import Any, Callable, Optional, TypedDict


class BookkeepingState(TypedDict, total=False):
    # —— input (channel adapter fills these) ——
    image_bytes: bytes
    user_id: str            # open_id of sender (may be empty)
    request_id: str         # e.g. message_id, for traceability
    source: str             # e.g. 飞书 / 快捷方式
    # Optional streaming hook: graph nodes call this with the accumulating
    # model output during analyze. Channel adapter wires it to a throttled
    # card-update closure. None = no streaming.
    on_progress: Optional[Callable[[str], None]]

    # —— intermediate ——
    raw_output: Optional[str]            # GLM raw text
    transaction: Optional[dict[str, Any]]  # parsed + validated
    is_transaction: bool
    retries: int
    validation_error: Optional[str]

    # —— output (channel reads these) ——
    record_id: Optional[str]
    reply: Optional[dict[str, Any]]       # {"type": "text", "text": "..."}
    error: Optional[str]
