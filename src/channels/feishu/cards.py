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


def pending_card() -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🔍 识别中…"},
            "template": "blue",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md",
             "content": "正在分析订单截图,请稍候(预计 5-15 秒)"}}
        ],
    }


def typing_card(text: str) -> dict[str, Any]:
    """Show the streaming model output as it arrives (typewriter effect)."""
    body = text.strip() if text else "(等待模型响应…)"
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🔍 识别中… ✍️"},
            "template": "blue",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md",
             "content": f"```\n{body}\n```"}}
        ],
    }


def confirm_card(record_id: str, transaction: dict[str, Any]) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📋 待确认"},
            "template": "yellow",
        },
        "elements": [
            _fields_block(transaction),
            _goods_block(transaction),
            {"tag": "hr"},
            {"tag": "note", "elements": [{"tag": "lark_md",
             "content": "如需修改,直接回复此消息(例:「金额改成 50」「类别是交通」)"}]},
            {"tag": "action", "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "✅ 确认"},
                    "type": "primary",
                    "value": {"action": "confirm", "record_id": record_id},
                }
            ]},
        ],
    }


def confirmed_card(record_id: str, transaction: dict[str, Any]) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "✅ 已记账"},
            "template": "green",
        },
        "elements": [
            _fields_block(transaction),
            _goods_block(transaction),
            {"tag": "note", "elements": [{"tag": "lark_md",
             "content": f"record_id: `{record_id}`"}]},
        ],
    }


def invalidated_card(transaction: dict[str, Any]) -> dict[str, Any]:
    """Old card after the record was modified by the user. Shows previous
    values, marked as superseded."""
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🚫 已失效"},
            "template": "grey",
        },
        "elements": [
            _fields_block(transaction),
            _goods_block(transaction),
            {"tag": "note", "elements": [{"tag": "lark_md",
             "content": "此版本已被新版本替代,请查看下方新卡片。"}]},
        ],
    }


def no_modification_card() -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "❓ 未识别修改"},
            "template": "grey",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md",
             "content": "没听懂这条消息的修改意图,可以更具体一点(例:「金额改成 50」「类别改为交通」)。原记录未变更。"}},
        ],
    }


def not_transaction_card(text: str) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "❓ 非交易截图"},
            "template": "grey",
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": text}}],
    }


def error_card(text: str) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "❌ 处理失败"},
            "template": "red",
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": text}}],
    }
