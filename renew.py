"""
ZenodeHost activity confirmation renew script.

Usage:
  python renew.py              # Standard: open browser, renew, save state
  python renew.py --check      # Check renewal status only
  python renew.py --api-only   # Headless: just call the API, save state
  python renew.py --schedule   # Loop: renew every 6 days
"""

import json
import os
import sys
import time
import base64
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from cloakbrowser import launch

DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_PATH = os.path.join(DIR, "storage_state.json")

SERVER_ID = "8871e223-bc6d-4626-81c5-715c92fd1f1b"
APP_URL = "https://zenode.fr/app#home"


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def send_tg(msg):
    """Send Telegram notification via bot. Configure env: TG_BOT_TOKEN, TG_CHAT_ID."""
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id:
        return  # 未配置，跳过
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
        }).encode()
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception as e:
        log(f"TG notify failed: {e}")


def load_storage():
    """Load storage state from local file, or STORAGE_STATE_JSON env var (base64 fallback)."""
    if os.path.exists(STORAGE_PATH):
        with open(STORAGE_PATH, "r", encoding="utf-8") as f:
            log(f"Loaded storage state from {STORAGE_PATH}")
            return json.load(f)
    env_raw = os.environ.get("STORAGE_STATE_JSON")
    if env_raw:
        try:
            decoded = base64.b64decode(env_raw).decode("utf-8")
            log("Loaded storage state from STORAGE_STATE_JSON env var (fallback)")
            return json.loads(decoded)
        except Exception as e:
            log(f"Failed to decode STORAGE_STATE_JSON env var: {e}")
    log("WARNING: No storage state found (file or env var)")
    return {"cookies": [], "origins": []}


def save_storage(context):
    storage = context.storage_state()
    with open(STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(storage, f, indent=2, ensure_ascii=False)

    for c in storage.get("cookies", []):
        if c["name"] == "connect.sid":
            exp = c.get("expires", "N/A")
            if isinstance(exp, (int, float)):
                exp_dt = datetime.fromtimestamp(exp)
                remaining = exp_dt - datetime.now()
                log(f"Cookie session expires: {exp_dt.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"(remaining {remaining.days}d {remaining.seconds//3600}h)")
            break

    log(f"Storage state saved ({len(storage.get('cookies', []))} cookies)")


def check_status(page):
    return page.evaluate("""async () => {
        const r = await fetch('/api/hosting/servers');
        const d = await r.json();
        return (d.servers || []).map(s => ({
            name: s.name,
            state: s.activity_confirmation?.state,
            confirmed_at: s.activity_confirmation?.confirmed_at,
            expires_at: s.activity_confirmation?.expires_at
        }));
    }""")


def confirm_activity(page):
    return page.evaluate(f"""async () => {{
        const r = await fetch('/api/hosting/servers/{SERVER_ID}/confirm-activity', {{
            method: 'POST',
            credentials: 'include',
            headers: {{ 'Content-Type': 'application/json' }}
        }});
        const data = await r.json();
        return {{ ok: r.ok, status: r.status, data: data }};
    }}""")


def setup_browser(headless=True):
    browser = launch(headless=headless)
    context = browser.new_context(storage_state=load_storage())
    page = context.new_page()
    return browser, context, page


def check_only():
    log("Checking renewal status...")
    browser, context, page = setup_browser(headless=True)

    try:
        page.goto(APP_URL, wait_until="load")
        page.wait_for_timeout(3000)

        statuses = check_status(page)
        for s in statuses:
            log(f"[{s['name']}] state: {s['state']}")
            log(f"  confirmed: {s['confirmed_at']}")
            log(f"  expires:   {s['expires_at']}")
            if s['expires_at']:
                exp = datetime.fromisoformat(s['expires_at'].replace('Z', '+00:00'))
                remaining = exp - datetime.now(timezone.utc)
                log(f"  remaining: {remaining.days}d {remaining.seconds//3600}h")

        return statuses
    finally:
        browser.close()


def renew(headless=True):
    log("Starting browser...")
    browser, context, page = setup_browser(headless=headless)

    try:
        log("Visiting zenode.fr to establish session...")
        page.goto(APP_URL, wait_until="load")
        page.wait_for_timeout(3000)

        statuses = check_status(page)
        for s in statuses:
            log(f"[{s['name']}] state: {s['state']}, expires: {s['expires_at']}")

        log("Calling confirm-activity API...")
        result = confirm_activity(page)

        if result["ok"] and result["data"].get("ok"):
            ac = result["data"]["activity_confirmation"]
            log("Renew SUCCESS")
            log(f"  new confirmed: {ac['confirmed_at']}")
            log(f"  new expires:   {ac['expires_at']}")
            log(f"  days remaining: {ac['days_remaining']}d")
            send_tg(
                f"<b>ZenodeHost Renew OK</b>\n"
                f"Server: Doom\n"
                f"Expires: {ac['expires_at'][:10]} {ac['expires_at'][11:16]} UTC\n"
                f"Remaining: {ac['days_remaining']} days"
            )
        else:
            error = result["data"].get("error", str(result))
            log(f"Renew FAILED: {error}")
            send_tg(f"<b>ZenodeHost Renew FAILED</b>\nServer: Doom\nError: {error}")
            return False

        log("Saving storage state...")
        save_storage(context)
        return True

    except Exception as e:
        log(f"Error: {e}")
        send_tg(f"<b>ZenodeHost Renew ERROR</b>\nServer: Doom\nError: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        browser.close()


def schedule_loop():
    interval_days = 6
    interval_seconds = interval_days * 24 * 3600

    log(f"Starting scheduled renewal loop (every {interval_days} days)")
    log("Press Ctrl+C to stop")

    while True:
        log("=" * 40)
        success = renew(headless=True)
        next_run = datetime.now() + timedelta(seconds=interval_seconds)
        log(f"Next renewal: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        log("=" * 40)

        if not success:
            log("Renew failed, retrying in 5 minutes...")
            time.sleep(300)
            continue

        time.sleep(interval_seconds)


def api_only():
    log("API-only mode: loading storage state and calling renew API...")
    browser, context, page = setup_browser(headless=True)

    try:
        page.goto(APP_URL, wait_until="load")
        page.wait_for_timeout(2000)

        log("Calling renew API...")
        result = confirm_activity(page)

        if result["ok"] and result["data"].get("ok"):
            ac = result["data"]["activity_confirmation"]
            log(f"Renew SUCCESS. Expires: {ac['expires_at']}")
            send_tg(
                f"<b>ZenodeHost Renew OK</b>\n"
                f"Server: Doom\n"
                f"Expires: {ac['expires_at'][:10]} {ac['expires_at'][11:16]} UTC\n"
                f"Remaining: {ac['days_remaining']} days"
            )
        else:
            error = result["data"].get("error", str(result))
            log(f"Renew FAILED: {error}")
            send_tg(f"<b>ZenodeHost Renew FAILED</b>\nServer: Doom\nError: {error}")

        save_storage(context)
        return result["ok"] and result["data"].get("ok")

    except Exception as e:
        log(f"Error: {e}")
        send_tg(f"<b>ZenodeHost Renew ERROR</b>\nServer: Doom\nError: {e}")
        return False
    finally:
        browser.close()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""

    if mode == "--check":
        check_only()
    elif mode == "--api-only":
        api_only()
    elif mode == "--schedule":
        schedule_loop()
    else:
        log("Standard mode (headless=False)")
        renew(headless=False)
