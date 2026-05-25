"""Feishu interactive-card JSON builders.

Card schemas are channel-specific, so all card construction lives in the
feishu channel layer. core/ only emits structured `reply` payloads; this
module turns them into card JSON.
"""
from typing import Any


def _fmt_amount(amount: str) -> str:
    if not amount:
        return "-"
    try:
        return f"¥{float(amount):.2f}"
    except (TypeError, ValueError):
        return amount


def _fields_block(tx: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": "div",
        "fields": [
            {"is_short": True, "text": {"tag": "lark_md",
             "content": f"**商户**\n{tx.get('merchant') or '-'}"}},
            {"is_short": True, "text": {"tag": "lark_md",
             "content": f"**金额**\n{_fmt_amount(tx.get('amount', ''))}"}},
            {"is_short": True, "text": {"tag": "lark_md",
             "content": f"**类别**\n{tx.get('category') or '-'}"}},
            {"is_short": True, "text": {"tag": "lark_md",
             "content": f"**置信度**\n{float(tx.get('confidence', 0)) * 100:.0f}%"}},
        ],
    }


def _goods_block(tx: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"**商品**:{tx.get('goods') or '-'}"},
    }


def _source_note(source: str | None) -> dict[str, Any] | None:
    if not source:
        return None
    return {
        "tag": "note",
        "elements": [{"tag": "lark_md", "content": f"来源:{source}"}],
    }


def _screenshot_block(image_key: str | None) -> dict[str, Any] | None:
    if not image_key:
        return None
    return {
        "tag": "img",
        "img_key": image_key,
        "alt": {"tag": "plain_text", "content": "订单截图"},
    }


def _without_none(elements: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    return [el for el in elements if el is not None]


def pending_card(source: str | None = None) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": f"🔍 识别中 · {source}" if source else "🔍 识别中…"},
            "template": "blue",
        },
        "elements": _without_none([
            {"tag": "div", "text": {"tag": "lark_md",
             "content": "正在分析订单截图,请稍候(预计 5-15 秒)"}},
            _source_note(source),
        ]),
    }


def typing_card(text: str, source: str | None = None) -> dict[str, Any]:
    """Show the streaming model output as it arrives (typewriter effect)."""
    body = text.strip() if text else "(等待模型响应…)"
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": f"🔍 识别中 · {source} ✍️" if source else "🔍 识别中… ✍️"},
            "template": "blue",
        },
        "elements": _without_none([
            {"tag": "div", "text": {"tag": "lark_md",
             "content": f"```\n{body}\n```"}},
            _source_note(source),
        ]),
    }


def confirm_card(
    record_id: str,
    transaction: dict[str, Any],
    source: str | None = None,
    image_key: str | None = None,
) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": f"📋 待确认 · {source}" if source else "📋 待确认"},
            "template": "yellow",
        },
        "elements": _without_none([
            _fields_block(transaction),
            _goods_block(transaction),
            _screenshot_block(image_key),
            {"tag": "hr"},
            {"tag": "note", "elements": [{"tag": "lark_md",
             "content": "如需修改,直接回复此消息(例:「金额改成 50」「类别是交通」)"}]},
            {"tag": "action", "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "✅ 确认"},
                    "type": "primary",
                    "value": {"action": "confirm", "record_id": record_id},
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "撤销"},
                    "type": "danger",
                    "value": {
                        "action": "cancel",
                        "record_id": record_id,
                        "transaction": transaction,
                    },
                }
            ]},
        ]),
    }


def confirmed_card(
    record_id: str,
    transaction: dict[str, Any],
    source: str | None = None,
    image_key: str | None = None,
) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": f"✅ 已记账 · {source}" if source else "✅ 已记账"},
            "template": "green",
        },
        "elements": _without_none([
            _fields_block(transaction),
            _goods_block(transaction),
            _screenshot_block(image_key),
            {"tag": "note", "elements": [{"tag": "lark_md",
             "content": f"record_id: `{record_id}`"}]},
        ]),
    }


def cancelled_card(
    transaction: dict[str, Any],
    source: str | None = None,
    image_key: str | None = None,
) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": f"已撤销 · {source}" if source else "已撤销"},
            "template": "grey",
        },
        "elements": _without_none([
            _fields_block(transaction),
            _goods_block(transaction),
            _screenshot_block(image_key),
            {"tag": "note", "elements": [{"tag": "lark_md",
             "content": "该待确认记录已撤销,多维表格中的候选记录已删除。"}]},
        ]),
    }


def invalidated_card(
    transaction: dict[str, Any],
    source: str | None = None,
    image_key: str | None = None,
) -> dict[str, Any]:
    """Old card after the record was modified by the user. Shows previous
    values, marked as superseded."""
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": f"🚫 已失效 · {source}" if source else "🚫 已失效"},
            "template": "grey",
        },
        "elements": _without_none([
            _fields_block(transaction),
            _goods_block(transaction),
            _screenshot_block(image_key),
            {"tag": "note", "elements": [{"tag": "lark_md",
             "content": "此版本已被新版本替代,请查看下方新卡片。"}]},
        ]),
    }


def no_modification_card(source: str | None = None) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": f"❓ 未识别修改 · {source}" if source else "❓ 未识别修改"},
            "template": "grey",
        },
        "elements": _without_none([
            {"tag": "div", "text": {"tag": "lark_md",
             "content": "没听懂这条消息的修改意图,可以更具体一点(例:「金额改成 50」「类别改为交通」)。原记录未变更。"}},
            _source_note(source),
        ]),
    }


def not_transaction_card(
    text: str,
    source: str | None = None,
    image_key: str | None = None,
) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": f"❓ 非交易截图 · {source}" if source else "❓ 非交易截图"},
            "template": "grey",
        },
        "elements": _without_none([
            {"tag": "div", "text": {"tag": "lark_md", "content": text}},
            _screenshot_block(image_key),
        ]),
    }


def error_card(text: str, source: str | None = None) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": f"❌ 处理失败 · {source}" if source else "❌ 处理失败"},
            "template": "red",
        },
        "elements": _without_none([
            {"tag": "div", "text": {"tag": "lark_md", "content": text}},
            _source_note(source),
        ]),
    }
