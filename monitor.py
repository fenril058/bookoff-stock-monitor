#!/usr/bin/env python3
"""Low-frequency BOOKOFF stock monitor for GitHub Actions.

The monitor performs one public product-page request per configured item, detects
an availability signal, and sends a Discord notification whenever an item is
AVAILABLE. It intentionally keeps no persistent state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_ITEMS_PATH = Path("items.json")
REQUEST_TIMEOUT_SECONDS = 20
MAX_FETCH_ATTEMPTS = 2
MAX_ITEMS = 10
BETWEEN_ITEMS_DELAY_SECONDS = 1.0
USER_AGENT = "bookoff-stock-monitor/1.0 (personal low-frequency availability checker)"


class StockStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    url: str


@dataclass(frozen=True)
class Detection:
    status: StockStatus
    reason: str


class PageParser(HTMLParser):
    """Extract visible text and JSON-LD blocks without third-party packages."""

    HIDDEN_TAGS = {"script", "style", "noscript", "svg", "template", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self._in_json_ld = False
        self._json_buffer: list[str] = []
        self.visible_chunks: list[str] = []
        self.json_ld_blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        attrs_dict = {key.lower(): (value or "") for key, value in attrs}
        if lowered == "script" and attrs_dict.get("type", "").lower() == "application/ld+json":
            self._in_json_ld = True
            self._json_buffer = []
            return
        if lowered in self.HIDDEN_TAGS:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "script" and self._in_json_ld:
            self.json_ld_blocks.append("".join(self._json_buffer).strip())
            self._in_json_ld = False
            self._json_buffer = []
            return
        if lowered in self.HIDDEN_TAGS and self._hidden_depth > 0:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._json_buffer.append(data)
        elif self._hidden_depth == 0:
            self.visible_chunks.append(data)

    @property
    def visible_text(self) -> str:
        return normalize_text(" ".join(self.visible_chunks))


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", value).strip()


def walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def type_contains_product(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() == "product"
    if isinstance(value, list):
        return any(isinstance(entry, str) and entry.lower() == "product" for entry in value)
    return False


def status_from_availability(value: Any) -> StockStatus | None:
    if not isinstance(value, str):
        return None
    lowered = value.lower()
    if "instock" in lowered or "limitedavailability" in lowered:
        return StockStatus.AVAILABLE
    if "outofstock" in lowered or "soldout" in lowered or "discontinued" in lowered:
        return StockStatus.OUT_OF_STOCK
    return None


def detect_from_json_ld(blocks: list[str], expected_name: str) -> Detection | None:
    expected = normalize_text(expected_name)
    products: list[dict[str, Any]] = []

    for block in blocks:
        if not block:
            continue
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        products.extend(node for node in walk_json(parsed) if type_contains_product(node.get("@type")))

    if not products:
        return None

    matching = [
        product
        for product in products
        if expected in normalize_text(str(product.get("name", "")))
        or normalize_text(str(product.get("name", ""))) in expected
    ]
    candidates = matching or (products if len(products) == 1 else [])

    for product in candidates:
        for node in walk_json(product.get("offers", {})):
            detected = status_from_availability(node.get("availability"))
            if detected:
                return Detection(detected, "JSON-LD availability")
    return None


def detection_windows(text: str, item_name: str) -> list[str]:
    name = normalize_text(item_name)
    starts = [match.start() for match in re.finditer(re.escape(name), text)]
    return [text[max(0, start - 200) : start + 2200] for start in starts]


def detect_stock(html: str, item_name: str) -> Detection:
    parser = PageParser()
    parser.feed(html)

    structured = detect_from_json_ld(parser.json_ld_blocks, item_name)
    if structured:
        return structured

    text = parser.visible_text
    anti_bot_signals = (
        "access denied",
        "captcha",
        "ロボットではないことを確認",
        "一時的にアクセスできません",
    )
    if any(signal in text.lower() for signal in anti_bot_signals):
        return Detection(StockStatus.UNKNOWN, "access-block or challenge page detected")

    normalized_name = normalize_text(item_name)
    if normalized_name not in text:
        return Detection(StockStatus.UNKNOWN, "expected product name was not found")

    windows = detection_windows(text, normalized_name)
    available_signals = ("カートに入れる", "カートへ入れる", "ショッピングカートに入れる")
    unavailable_signals = ("在庫なし", "在庫切れ", "品切れ")

    if any(signal in window for window in windows for signal in available_signals):
        return Detection(StockStatus.AVAILABLE, "purchase button text")
    if any(signal in window for window in windows for signal in unavailable_signals):
        return Detection(StockStatus.OUT_OF_STOCK, "out-of-stock text")

    return Detection(StockStatus.UNKNOWN, "no recognized availability signal")


def load_items(path: Path) -> list[Item]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("items.json must contain a non-empty JSON array")
    if len(raw) > MAX_ITEMS:
        raise ValueError(f"At most {MAX_ITEMS} items are allowed to limit site load")

    items: list[Item] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"items.json entry {index} must be an object")
        item = Item(
            id=str(entry.get("id", "")).strip(),
            name=str(entry.get("name", "")).strip(),
            url=str(entry.get("url", "")).strip(),
        )
        if not item.id or not item.name or not item.url:
            raise ValueError(f"items.json entry {index} requires id, name, and url")
        if item.id in seen_ids:
            raise ValueError(f"Duplicate item id: {item.id}")
        if not item.url.startswith("https://shopping.bookoff.co.jp/"):
            raise ValueError(f"Only BOOKOFF Online Store HTTPS URLs are allowed: {item.url}")
        seen_ids.add(item.id)
        items.append(item)
    return items



def fetch_html(item: Item) -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.5",
        "Cache-Control": "no-cache",
    }

    last_error: Exception | None = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        request = Request(item.url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise RuntimeError(f"Unexpected HTTP status {status} for {item.url}")
                content_type = response.headers.get("Content-Type", "")
                if "text/html" not in content_type.lower():
                    raise RuntimeError(f"Unexpected Content-Type {content_type!r} for {item.url}")
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except HTTPError as exc:
            last_error = RuntimeError(f"HTTP {exc.code} while fetching {item.url}")
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not retryable or attempt == MAX_FETCH_ATTEMPTS:
                break
        except (URLError, TimeoutError, OSError) as exc:
            last_error = RuntimeError(f"Network error while fetching {item.url}: {type(exc).__name__}")
            if attempt == MAX_FETCH_ATTEMPTS:
                break
        if attempt < MAX_FETCH_ATTEMPTS:
            time.sleep(2.0 * attempt)

    raise last_error or RuntimeError(f"Failed to fetch {item.url}")


def post_discord(webhook_url: str, content: str) -> None:
    payload = json.dumps(
        {
            "content": content,
            "allowed_mentions": {"parse": []},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", 204)
            if status not in (200, 204):
                raise RuntimeError(f"Discord returned HTTP {status}")
    except HTTPError as exc:
        raise RuntimeError(f"Discord webhook returned HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Discord webhook network error: {type(exc).__name__}") from None


def format_notification(items: list[Item]) -> str:
    lines = ["📚 **BOOKOFFで在庫ありを検知しました**", ""]
    for item in items:
        lines.extend([f"**{item.name}**", item.url, ""])
    lines.append("在庫は変動します。リンクを開いて手動で購入してください。")
    return "\n".join(lines)


def run_monitor(items_path: Path, webhook_url: str) -> int:
    items = load_items(items_path)
    available_items: list[Item] = []

    for index, item in enumerate(items):
        html = fetch_html(item)
        detection = detect_stock(html, item.name)
        print(f"{item.id}: {detection.status} ({detection.reason})")
        if detection.status is StockStatus.UNKNOWN:
            raise RuntimeError(f"Could not determine stock status for {item.name}: {detection.reason}")

        if detection.status is StockStatus.AVAILABLE:
            available_items.append(item)

        if index + 1 < len(items):
            time.sleep(BETWEEN_ITEMS_DELAY_SECONDS)

    if available_items:
        post_discord(webhook_url, format_notification(available_items))
        print(f"Discord notification sent for {len(available_items)} item(s)")
    else:
        print("No available items; no notification sent")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=Path, default=DEFAULT_ITEMS_PATH)
    parser.add_argument("--test-notification", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL is not configured", file=sys.stderr)
        return 2

    try:
        if args.test_notification:
            post_discord(
                webhook_url,
                "✅ BOOKOFF在庫監視のテスト通知です。GitHub Actionsから正常に送信されました。",
            )
            print("Test notification sent")
            return 0
        return run_monitor(args.items, webhook_url)
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
