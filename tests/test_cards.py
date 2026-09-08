import json
import unittest

from src.channels.feishu import cards


class ConfirmCardTests(unittest.TestCase):
    def test_form_contains_only_empty_completable_fields(self) -> None:
        card = cards.confirm_card(
            "rec_1",
            {
                "merchant": "",
                "goods": "咖啡",
                "category": "餐饮",
                "amount": "",
                "confidence": 0.8,
            },
        )

        self.assertEqual(card["schema"], "2.0")
        self.assertFalse(
            any(element["tag"] == "note" for element in card["body"]["elements"])
        )
        form = next(
            element for element in card["body"]["elements"]
            if element["tag"] == "form"
        )
        field_layout = form["elements"][0]
        inputs = [column["elements"][0] for column in field_layout["columns"]]
        self.assertEqual([element["name"] for element in inputs], ["merchant", "amount"])
        self.assertTrue(all(element["required"] is False for element in inputs))
        self.assertEqual(field_layout["tag"], "column_set")
        self.assertEqual(len(field_layout["columns"]), 2)

        submit = next(
            element for element in form["elements"]
            if element.get("action_type") == "form_submit"
        )
        self.assertEqual(submit["tag"], "button")
        self.assertEqual(submit["action_type"], "form_submit")
        self.assertEqual(
            submit["value"],
            {"action": "complete_missing", "record_id": "rec_1"},
        )

    def test_no_form_when_completable_fields_are_present(self) -> None:
        card = cards.confirm_card(
            "rec_1",
            {
                "merchant": "咖啡店",
                "goods": "咖啡",
                "category": "其他",
                "amount": "25",
                "confidence": 0.8,
            },
        )

        self.assertFalse(
            any(element["tag"] == "form" for element in card["body"]["elements"])
        )

    def test_confirm_remains_available_alongside_form(self) -> None:
        card = cards.confirm_card(
            "rec_1",
            {
                "merchant": "",
                "goods": "",
                "category": "其他",
                "amount": "",
                "confidence": 0,
            },
        )

        buttons = [
            button
            for element in card["body"]["elements"]
            if element["tag"] == "column_set"
            for column in element["columns"]
            for button in column["elements"]
        ]
        self.assertTrue(
            any(
                button.get("value", {}).get("action") == "confirm"
                for button in buttons
            )
        )

    def test_goods_input_uses_card_v2_form_structure(self) -> None:
        card = cards.confirm_card(
            "rec_goods",
            {
                "merchant": "ALDI奥乐齐",
                "goods": "",
                "category": "超市生鲜",
                "amount": "58.51",
                "confidence": 0.9,
            },
        )

        form = next(
            element for element in card["body"]["elements"]
            if element["tag"] == "form"
        )
        field_layout = form["elements"][0]
        inputs = [column["elements"][0] for column in field_layout["columns"]]
        self.assertEqual([element["name"] for element in inputs], ["goods"])
        self.assertEqual(inputs[0]["label"]["content"], "商品")
        self.assertEqual(inputs[0]["label_position"], "left")
        self.assertEqual(inputs[0]["width"], "default")
        self.assertEqual(field_layout["columns"][0]["weight"], 1)
        self.assertFalse(any(element["tag"] == "action" for element in form["elements"]))
        self.assertEqual(
            [element["tag"] for element in form["elements"]],
            ["column_set", "button"],
        )
        self.assertEqual(form["elements"][1]["text"]["content"], "提交修改")
        self.assertEqual(form["elements"][1]["width"], "fill")


class CardLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tx = {
            "merchant": "ALDI奥乐齐",
            "goods": "测试",
            "category": "超市生鲜",
            "amount": "58.51",
            "confidence": 0.9,
        }

    def test_every_lifecycle_state_can_replace_a_v2_card(self) -> None:
        states = {
            "pending": cards.pending_card("快捷方式"),
            "typing": cards.typing_card("识别中", "快捷方式"),
            "confirm": cards.confirm_card("rec_1", self.tx),
            "completed": cards.completion_submitted_card(self.tx),
            "confirmed": cards.confirmed_card("rec_1", self.tx),
            "cancelled": cards.cancelled_card(self.tx),
            "invalidated": cards.invalidated_card(self.tx),
            "no_modification": cards.no_modification_card("快捷方式"),
            "not_transaction": cards.not_transaction_card("非交易截图"),
            "error": cards.error_card("处理失败", "快捷方式"),
        }

        def tags(value):
            if isinstance(value, dict):
                yield value.get("tag")
                for child in value.values():
                    yield from tags(child)
            elif isinstance(value, list):
                for child in value:
                    yield from tags(child)

        for name, card in states.items():
            with self.subTest(state=name):
                self.assertEqual(card.get("schema"), "2.0")
                self.assertNotIn("elements", card)
                self.assertTrue(card["body"]["elements"])
                self.assertTrue(card["config"]["update_multi"])
                component_tags = set(tags(card))
                self.assertFalse(component_tags & {"note", "action"})
                if name != "confirm":
                    self.assertFalse(component_tags & {"form", "input", "button"})

    def test_completion_replaces_form_with_receipt_and_keeps_context(self) -> None:
        before = cards.confirm_card("rec_1", {**self.tx, "goods": ""})
        after = cards.completion_submitted_card(self.tx, "快捷方式", "img_1")

        self.assertEqual(after.get("schema"), before["schema"])
        self.assertEqual(after["header"]["title"]["content"], "✍️ 已补全 · 快捷方式")
        elements = after["body"]["elements"]
        self.assertIn({
            "tag": "img", "img_key": "img_1",
            "alt": {"tag": "plain_text", "content": "订单截图"},
        }, elements)
        self.assertIn("测试", json.dumps(elements, ensure_ascii=False))
        self.assertIn("新卡片中确认", elements[-1]["text"]["content"])

    def test_confirmed_card_keeps_month_total_and_record_fallback(self) -> None:
        for total, expected in [(100.5, "¥100.50"), (None, "rec_1")]:
            with self.subTest(total=total):
                card = cards.confirmed_card("rec_1", self.tx, month_total=total)
                self.assertIn(expected, card["body"]["elements"][-1]["text"]["content"])


if __name__ == "__main__":
    unittest.main()
