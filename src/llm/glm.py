import base64
import io
import json
import logging
import re
import time
from typing import Any, Callable

from openai import OpenAI
from PIL import Image, ImageOps

from src.config import CONFIG
from src.prompts import load_prompt

logger = logging.getLogger(__name__)

_client = OpenAI(api_key=CONFIG.llm_api_key, base_url=CONFIG.llm_base_url)

# LLM upload knobs: only the LLM call gets shrunk; Bitable still stores the original.
_LLM_MAX_EDGE = 1600     # longest side in pixels
_LLM_JPEG_Q = 85
_LLM_SKIP_BYTES = 300_000  # don't bother re-encoding when image is already small


def _shrink_for_llm(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)  # respect EXIF rotation
    w0, h0 = img.size
    needs_resize = max(w0, h0) > _LLM_MAX_EDGE

    if not needs_resize and len(image_bytes) < _LLM_SKIP_BYTES:
        logger.info("image %dx%d %d bytes; keeping as-is for LLM", w0, h0, len(image_bytes))
        return image_bytes

    if needs_resize:
        if w0 >= h0:
            new_size = (_LLM_MAX_EDGE, int(h0 * _LLM_MAX_EDGE / w0))
        else:
            new_size = (int(w0 * _LLM_MAX_EDGE / h0), _LLM_MAX_EDGE)
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=_LLM_JPEG_Q, optimize=True)
    out = buf.getvalue()
    logger.info(
        "shrunk for LLM: %dx%d %d bytes -> %dx%d %d bytes (%.0f%% of original)",
        w0, h0, len(image_bytes),
        img.size[0], img.size[1], len(out),
        100 * len(out) / len(image_bytes),
    )
    return out


def _detect_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _chat_completions(
    messages: list,
    on_text: "Callable[[str], None] | None" = None,
) -> str:
    """Shared OpenAI chat-completions invoker. Streams if on_text is given."""
    start = time.monotonic()
    streaming = on_text is not None
    if not streaming:
        resp = _client.chat.completions.create(model=CONFIG.llm_model, messages=messages)
        out = resp.choices[0].message.content or ""
    else:
        stream = _client.chat.completions.create(
            model=CONFIG.llm_model, messages=messages, stream=True,
        )
        out = ""
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ""
            if delta:
                out += delta
                on_text(out)
    logger.info(
        "%s returned in %.2fs (output=%d chars, stream=%s)",
        CONFIG.llm_model, time.monotonic() - start, len(out), streaming,
    )
    return out


def call_vision(
    prompt: str,
    image_bytes: bytes,
    on_text: "Callable[[str], None] | None" = None,
) -> str:
    """Vision call: prompt + image. Streams when on_text is provided."""
    image_bytes = _shrink_for_llm(image_bytes)
    mime = _detect_mime(image_bytes)
    b64 = base64.b64encode(image_bytes).decode("ascii")
    logger.info(
        "calling %s vision (image=%d bytes, mime=%s, prompt=%d chars, stream=%s)",
        CONFIG.llm_model, len(image_bytes), mime, len(prompt), on_text is not None,
    )
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ],
    }]
    return _chat_completions(messages, on_text=on_text)


def call_text(
    prompt: str,
    on_text: "Callable[[str], None] | None" = None,
) -> str:
    """Text-only call (used by the modify flow)."""
    logger.info(
        "calling %s text (prompt=%d chars, stream=%s)",
        CONFIG.llm_model, len(prompt), on_text is not None,
    )
    messages = [{"role": "user", "content": prompt}]
    return _chat_completions(messages, on_text=on_text)


# Model may wrap output in ```json ... ``` despite instructions; strip it.
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_json(raw: str) -> dict[str, Any]:
    s = _FENCE_RE.sub("", raw.strip()).strip()
    return json.loads(s)


def recognize_transaction(image_bytes: bytes) -> dict[str, Any]:
    return extract_json(call_vision(load_prompt("recognize"), image_bytes))
