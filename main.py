"""
Snitch.co.in Stock Monitor Bot
Uses a real browser (Playwright) to handle JS-rendered pages.

Setup:
  pip install playwright requests
  python -m playwright install chromium

Usage:
  python snitch_bot.py
  python snitch_bot.py --config my_config.json
"""

import sys
import time
import json
import logging
import requests
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ─────────────────────────────────────────────
# LOAD CONFIG
# ─────────────────────────────────────────────

CONFIG_FILE = Path(sys.argv[2] if "--config" in sys.argv else "config.json")

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"❌  Config file not found: {CONFIG_FILE}")
        print("    Create a config.json next to this script and fill in your details.")
        sys.exit(1)
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

cfg                    = load_config()
TELEGRAM_BOT_TOKEN     = cfg["telegram"]["bot_token"]
TELEGRAM_CHAT_ID       = cfg["telegram"]["chat_id"]
WATCHLIST              = cfg["watchlist"]
CHECK_INTERVAL_SECONDS = cfg.get("settings", {}).get("check_interval_minutes", 5) * 60

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("snitch_monitor.log"),
    ],
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        log.info("Telegram message sent.")
        return True
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False

# ─────────────────────────────────────────────
# BROWSER-BASED SCRAPER
# Snitch renders sizes as: span.flex-col > div (one per size).
# Sold out  → div classes contain "line-through" AND "opacity-50"
# Available → div classes contain "border-black" (no line-through)
# ─────────────────────────────────────────────

# Confirmed selector from Snitch DOM inspection
SIZE_ITEM_SELECTOR = "div.flex.flex-row.justify-center.flex-wrap.my-2 span.flex-col > div"

def get_available_sizes(page, url: str) -> list:
    """
    Navigate to the product page and return all available (not sold-out) sizes.
    """
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except PWTimeout:
        log.error(f"Timed out loading: {url}")
        return []

    # Wait until size boxes are rendered
    try:
        page.wait_for_selector(SIZE_ITEM_SELECTOR, timeout=15000)
    except PWTimeout:
        log.error("Size selector never appeared — page may still be loading or URL is wrong.")
        return []

    available = []
    sold_out  = []

    for el in page.query_selector_all(SIZE_ITEM_SELECTOR):
        classes = el.get_attribute("class") or ""
        label   = el.inner_text().strip()
        if not label:
            continue
        if "line-through" in classes and "opacity-50" in classes:
            sold_out.append(label)
        else:
            available.append(label)

    log.info(f"Available: {available}  |  Sold out: {sold_out}")
    return available


def check_size_available(page, url: str, target_size: str):
    all_sizes = get_available_sizes(page, url)
    normalized = target_size.strip().upper()
    is_available = any(s.strip().upper() == normalized for s in all_sizes)
    return is_available, all_sizes

# ─────────────────────────────────────────────
# MAIN MONITOR LOOP
# ─────────────────────────────────────────────

alerted = set()

def monitor_once(page):
    global cfg, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WATCHLIST, CHECK_INTERVAL_SECONDS
    cfg                    = load_config()
    TELEGRAM_BOT_TOKEN     = cfg["telegram"]["bot_token"]
    TELEGRAM_CHAT_ID       = cfg["telegram"]["chat_id"]
    WATCHLIST              = cfg["watchlist"]
    CHECK_INTERVAL_SECONDS = cfg.get("settings", {}).get("check_interval_minutes", 5) * 60

    for item in WATCHLIST:
        url  = item["url"]
        size = item["size"]
        name = item.get("name", url)
        key  = f"{url}::{size}"

        log.info(f"Checking: {name} | Size: {size}")
        is_available, all_sizes = check_size_available(page, url, size)

        if is_available:
            if key not in alerted:
                msg = (
                    f"🟢 <b>In Stock!</b>\n\n"
                    f"<b>{name}</b>\n"
                    f"Size <b>{size}</b> is now available on Snitch!\n\n"
                    f"👉 <a href=\"{url}\">Buy now</a>\n\n"
                    f"<i>Available sizes: {', '.join(all_sizes)}</i>"
                )
                send_telegram(msg)
                alerted.add(key)
                log.info(f"✅ AVAILABLE — alert sent for {name} size {size}")
            else:
                log.info(f"Already alerted for {name} size {size}, skipping.")
        else:
            if key in alerted:
                alerted.discard(key)
                log.info(f"Back out of stock: {name} size {size}. Will re-alert when available.")
            else:
                log.info(
                    f"❌ Not available — {name} size {size}. "
                    f"(Available: {', '.join(all_sizes) or 'none detected'})"
                )


def main():
    log.info("=" * 50)
    log.info("Snitch Stock Monitor started (browser mode)")
    log.info(f"Watching {len(WATCHLIST)} product(s). Checking every {CHECK_INTERVAL_SECONDS}s.")
    log.info("=" * 50)

    send_telegram(
        f"🤖 <b>Snitch Monitor started!</b>\n"
        f"Watching <b>{len(WATCHLIST)}</b> product(s) for your sizes.\n"
        f"Checking every <b>{CHECK_INTERVAL_SECONDS // 60} min</b>."
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-IN",
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()

        while True:
            try:
                monitor_once(page)
            except Exception as e:
                log.error(f"Unexpected error: {e}")
            log.info(f"Sleeping {CHECK_INTERVAL_SECONDS}s until next check...\n")
            time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()