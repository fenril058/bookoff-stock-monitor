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


class MonitorNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.items_path = Path(self.temporary_directory.name) / "items.json"
        self.items_path.write_text(
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

    def test_out_of_stock_does_not_notify(self) -> None:
        html = "<html><body><h1>ねこくま、めしくま</h1><p>在庫なし</p></body></html>"

        with patch("monitor.fetch_html", return_value=html), patch("monitor.post_discord") as post:
            run_monitor(self.items_path, "https://example.invalid/webhook")

        post.assert_not_called()

    def test_available_notifies_on_every_run(self) -> None:
        html = "<html><body><h1>ねこくま、めしくま</h1><button>カートに入れる</button></body></html>"

        with patch("monitor.fetch_html", return_value=html), patch("monitor.post_discord") as post:
            run_monitor(self.items_path, "https://example.invalid/webhook")
            run_monitor(self.items_path, "https://example.invalid/webhook")

        self.assertEqual(post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
