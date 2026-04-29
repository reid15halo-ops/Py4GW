"""
Kamadan Trade Chat Price Fetcher — Fetches current market prices from kamadan.gwtoolbox.com
Run periodically to update the valuable items database.

Usage: python price_fetcher.py [--update-loot-manager]

Fetches latest trade messages, extracts prices, and optionally updates
the AutoLootManager's valuable items lists.
"""
import json
import os
import re
import sys
import time
from collections import defaultdict
from urllib.request import urlopen, Request
from urllib.error import URLError

KAMADAN_API = "https://kamadan.gwtoolbox.com/m"
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Settings", "market_prices.json")

# Price unit conversions (approximate)
# 1 Armbraces (a) ≈ 100k gold ≈ 8 ecto
# 1 ecto (e) ≈ 12-13k gold
ECTO_TO_GOLD = 12500
ARMBRACES_TO_GOLD = 100000


def fetch_messages():
    """Fetch latest trade messages from Kamadan API."""
    try:
        req = Request(KAMADAN_API, headers={"User-Agent": "Py4GW-PriceFetcher/1.0"})
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"Error fetching: {e}")
        return []


def parse_price(price_str):
    """Parse price string like '5e', '100k', '30a' to gold value."""
    price_str = price_str.strip().lower()

    # Remove commas
    price_str = price_str.replace(",", "")

    match = re.match(r"(\d+\.?\d*)\s*(e|ecto|a|arm|k|gold|g|plat|p)?", price_str)
    if not match:
        return 0

    value = float(match.group(1))
    unit = match.group(2) or "g"

    if unit in ("e", "ecto"):
        return int(value * ECTO_TO_GOLD)
    elif unit in ("a", "arm"):
        return int(value * ARMBRACES_TO_GOLD)
    elif unit in ("k",):
        return int(value * 1000)
    elif unit in ("p", "plat"):
        return int(value * 1000)
    else:
        return int(value)


def extract_items_from_message(msg):
    """Extract item names and prices from a trade message."""
    text = msg.get("m", "")
    sender = msg.get("s", "")
    timestamp = msg.get("t", 0)

    items = []

    # WTS patterns
    wts_match = re.match(r"(?:WTS|wts|Wts)\s+(.+)", text, re.IGNORECASE)
    if not wts_match:
        return items

    content = wts_match.group(1)

    # Split by common delimiters
    parts = re.split(r"[|/]", content)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Try to extract price (number + unit at end)
        price_match = re.search(r"(\d+\.?\d*)\s*(e|ecto|a|arm|k|gold)\s*$", part, re.IGNORECASE)
        if price_match:
            price = parse_price(price_match.group(0))
            name = part[:price_match.start()].strip().rstrip("-").strip()
            if name and price > 0:
                items.append({
                    "name": name,
                    "price_gold": price,
                    "price_raw": price_match.group(0).strip(),
                    "seller": sender,
                    "timestamp": timestamp,
                })

    return items


def aggregate_prices(all_items):
    """Aggregate prices by item name, computing avg/min/max."""
    by_name = defaultdict(list)

    for item in all_items:
        # Normalize name
        name = item["name"].strip()
        # Remove quantity prefixes like "5x", "x3"
        name = re.sub(r"^\d+x\s*", "", name, flags=re.IGNORECASE)
        name = re.sub(r"^x\d+\s*", "", name, flags=re.IGNORECASE)
        name = name.strip()

        if len(name) < 3:
            continue

        by_name[name].append(item["price_gold"])

    result = {}
    for name, prices in sorted(by_name.items()):
        if len(prices) == 0:
            continue
        result[name] = {
            "avg": int(sum(prices) / len(prices)),
            "min": min(prices),
            "max": max(prices),
            "count": len(prices),
        }

    return result


def identify_valuable_items(prices):
    """Identify items worth more than 1 ecto (12.5k gold)."""
    valuable = {}
    for name, data in prices.items():
        if data["avg"] >= ECTO_TO_GOLD:
            valuable[name] = data
    return valuable


def main():
    print("Fetching Kamadan trade data...")
    messages = fetch_messages()
    print(f"Got {len(messages)} messages")

    if not messages:
        print("No messages fetched. Try again later.")
        return

    # Extract all items with prices
    all_items = []
    for msg in messages:
        items = extract_items_from_message(msg)
        all_items.extend(items)

    print(f"Extracted {len(all_items)} items with prices")

    # Aggregate
    prices = aggregate_prices(all_items)
    valuable = identify_valuable_items(prices)

    # Show top items
    print(f"\n=== Top Valuable Items (>{ECTO_TO_GOLD} gold avg) ===")
    sorted_items = sorted(valuable.items(), key=lambda x: x[1]["avg"], reverse=True)
    for name, data in sorted_items[:30]:
        ecto_price = data["avg"] / ECTO_TO_GOLD
        print(f"  {name}: ~{ecto_price:.1f}e ({data['count']} listings)")

    # Save prices
    output = {
        "fetched_at": int(time.time()),
        "message_count": len(messages),
        "item_count": len(all_items),
        "ecto_rate": ECTO_TO_GOLD,
        "prices": prices,
        "valuable": valuable,
    }

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nPrices saved to: {OUTPUT_FILE}")
    print(f"Total items tracked: {len(prices)}")
    print(f"Valuable items (>1e): {len(valuable)}")

    # Update AutoLootManager if requested
    if "--update-loot-manager" in sys.argv:
        update_loot_manager(valuable)


def update_loot_manager(valuable):
    """Update AutoLootManager's VALUABLE_SKIN_KEYWORDS with current market data."""
    print("\nUpdating AutoLootManager valuable items...")

    # Extract keywords from valuable item names
    new_keywords = set()
    for name in valuable.keys():
        # Clean name and extract meaningful keywords
        clean = name.lower().strip()
        # Remove common prefixes
        for prefix in ["q9 ", "q10 ", "q11 ", "q12 ", "q13 ", "os ", "insc "]:
            clean = clean.replace(prefix, "")
        clean = clean.strip()
        if len(clean) > 3:
            new_keywords.add(clean)

    if new_keywords:
        print(f"  Found {len(new_keywords)} valuable item keywords from market data:")
        for kw in sorted(new_keywords)[:20]:
            print(f"    + {kw}")

    print("  (Manual review recommended before adding to AutoLootManager)")


if __name__ == "__main__":
    main()
