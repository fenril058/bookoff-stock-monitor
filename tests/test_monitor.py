import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor import StockStatus, detect_stock, run_monitor


class DetectStockTests(unittest.TestCase):
    def test_json_ld_in_stock(self) -> None:
        html = """
        <html><body>
          <h1>ねこくま、めしくま</h1>
          <script type="application/ld+json">
          {"@type":"Product","name":"ねこくま、めしくま","offers":{"availability":"https://schema.org/InStock"}}
          </script>
        </body></html>
        """
        detection = detect_stock(html, "ねこくま、めしくま")
        self.assertEqual(detection.status, StockStatus.AVAILABLE)

    def test_visible_out_of_stock(self) -> None:
        html = """
        <html><body><main>
          <h1>ねこくま、めしくま 角川文庫</h1>
          <div>495円</div><div>在庫なし</div>
        </main></body></html>
        """
        detection = detect_stock(html, "ねこくま、めしくま")
        self.assertEqual(detection.status, StockStatus.OUT_OF_STOCK)

    def test_purchase_button_takes_priority_near_product(self) -> None:
        html = """
        <html><body><main>
          <h1>ねこくま、めしくま</h1>
          <button>カートに入れる</button>
          <section>別の商品 在庫なし</section>
        </main></body></html>
        """
        detection = detect_stock(html, "ねこくま、めしくま")
        self.assertEqual(detection.status, StockStatus.AVAILABLE)

    def test_missing_product_name_is_unknown(self) -> None:
        html = "<html><body><p>在庫なし</p></body></html>"
        detection = detect_stock(html, "ねこくま、めしくま")
        self.assertEqual(detection.status, StockStatus.UNKNOWN)

    def test_access_challenge_is_unknown(self) -> None:
        html = "<html><body><h1>ねこくま、めしくま</h1><p>Access Denied</p></body></html>"
        detection = detect_stock(html, "ねこくま、めしくま")
        self.assertEqual(detection.status, StockStatus.UNKNOWN)


class StateTransitionTests(unittest.TestCase):
    def test_notifies_only_on_transition_to_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            items_path = root / "items.json"
            state_path = root / "state.json"
            items_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "0019040704",
                            "name": "ねこくま、めしくま",
                            "url": "https://shopping.bookoff.co.jp/used/0019040704",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            state_path.write_text('{"items": {}}', encoding="utf-8")

            out_html = "<html><body><h1>ねこくま、めしくま</h1><p>在庫なし</p></body></html>"
            available_html = "<html><body><h1>ねこくま、めしくま</h1><button>カートに入れる</button></body></html>"

            with patch("monitor.fetch_html", return_value=out_html), patch("monitor.post_discord") as post:
                run_monitor(items_path, state_path, "https://example.invalid/webhook")
                post.assert_not_called()

            with patch("monitor.fetch_html", return_value=available_html), patch("monitor.post_discord") as post:
                run_monitor(items_path, state_path, "https://example.invalid/webhook")
                post.assert_called_once()

            with patch("monitor.fetch_html", return_value=available_html), patch("monitor.post_discord") as post:
                run_monitor(items_path, state_path, "https://example.invalid/webhook")
                post.assert_not_called()

            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["items"]["0019040704"]["status"], "AVAILABLE")
            self.assertIn("last_notified_at", saved["items"]["0019040704"])


if __name__ == "__main__":
    unittest.main()
