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

        form = next(
            element for element in card["elements"] if element["tag"] == "form"
        )
        inputs = [element for element in form["elements"] if element["tag"] == "input"]
        self.assertEqual([element["name"] for element in inputs], ["merchant", "amount"])
        self.assertTrue(all(element["required"] is False for element in inputs))

        submit = next(
            action
            for element in form["elements"]
            if element["tag"] == "action"
            for action in element["actions"]
        )
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

        self.assertFalse(any(element["tag"] == "form" for element in card["elements"]))

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

        actions = [
            action
            for element in card["elements"]
            if element["tag"] == "action"
            for action in element["actions"]
        ]
        self.assertTrue(
            any(
                action.get("value", {}).get("action") == "confirm"
                for action in actions
            )
        )


if __name__ == "__main__":
    unittest.main()
