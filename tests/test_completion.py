import importlib
import os
import unittest
from unittest.mock import Mock, patch


class CompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Import-time clients must never contact Feishu or use real credentials.
        env = {
            "LARK_APP_ID": "test_app",
            "LARK_APP_SECRET": "test_secret",
            "BITABLE_APP_TOKEN": "test_base",
            "BITABLE_TABLE_ID": "test_table",
            "MODAL_API_KEY": "test_key",
            "MODAL_APP_BASE": "https://example.invalid/v1",
            "MODAL_APP_NAME": "test_model",
        }
        with (
            patch.dict(os.environ, env),
            patch("lark_oapi.Client.builder"),
            patch("openai.OpenAI"),
        ):
            cls.app = importlib.import_module("src.channels.feishu.app")

    def setUp(self) -> None:
        self.client = Mock()
        self.actions = Mock()
        self.actions.is_confirmed.return_value = False
        for name, value in [("_feishu", self.client), ("core_actions", self.actions)]:
            patcher = patch.object(self.app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app.remember_card("card_1", "rec_1", "快捷方式", "img_1")
        self.addCleanup(self.app._forget_card, "card_1")
        self.tx = {"merchant": "商店", "goods": "测试", "amount": "58.51"}

    def test_updates_same_card_before_and_after_completing(self) -> None:
        def complete(record_id, values):
            self.assertEqual((record_id, values), ("rec_1", {"goods": "测试"}))
            self.client.update_card.assert_called_once()
            message_id, card = self.client.update_card.call_args.args
            self.assertEqual(message_id, "card_1")
            self.assertIn("识别中", card["header"]["title"]["content"])
            return self.tx

        self.actions.complete_missing_fields.side_effect = complete
        self.app._process_completion("rec_1", "card_1", {"goods": "测试"})

        self.assertEqual(self.client.update_card.call_count, 2)
        message_id, card = self.client.update_card.call_args.args
        self.assertEqual(message_id, "card_1")
        self.assertEqual(card, self.app.cards.confirm_card(
            "rec_1", self.tx, "快捷方式", "img_1",
        ))
        self.client.reply_card.assert_not_called()
        self.client.send_card.assert_not_called()
        self.assertEqual(self.app._lookup_card_context("card_1"), {
            "record_id": "rec_1", "source": "快捷方式", "image_key": "img_1",
        })

    def test_invalid_input_restores_pending_form(self) -> None:
        self.actions.complete_missing_fields.side_effect = ValueError("金额必须是数字")
        self.actions.get_transaction.return_value = {**self.tx, "amount": ""}

        with self.assertLogs(self.app.logger, level="ERROR"):
            self.app._process_completion("rec_1", "card_1", {"amount": "abc"})

        message_id, card = self.client.update_card.call_args.args
        self.assertEqual(message_id, "card_1")
        self.assertIn("待确认", card["header"]["title"]["content"])
        self.assertTrue(any(el["tag"] == "form" for el in card["body"]["elements"]))
        self.client.reply_text.assert_called_once_with("card_1", "补全失败:金额必须是数字")
        self.client.reply_card.assert_not_called()

    def test_read_failure_exits_processing_state(self) -> None:
        self.actions.complete_missing_fields.side_effect = RuntimeError("服务不可用")
        self.actions.get_transaction.side_effect = RuntimeError("服务不可用")

        with self.assertLogs(self.app.logger, level="ERROR"):
            self.app._process_completion("rec_1", "card_1", {"goods": "测试"})

        message_id, card = self.client.update_card.call_args.args
        self.assertEqual(message_id, "card_1")
        self.assertIn("处理失败", card["header"]["title"]["content"])
        self.client.reply_card.assert_not_called()


if __name__ == "__main__":
    unittest.main()
