import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError
from cloakbrowser import launch

DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_PATH = os.path.join(DIR, "storage_state.json")
LOG_PATH = os.path.join(DIR, "renew.log")

# Telegram 配置（从环境变量读取）
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def send_tg(message: str):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        data = json.dumps({
            "chat_id": TG_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }).encode()
        req = Request(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=10) as resp:
            pass
        log("📨 Telegram 通知已发送")
    except URLError as e:
        log(f"⚠️ Telegram 发送失败: {e}")


def parse_remaining(text: str) -> float | None:
    h = re.search(r"(\d+)\s*h", text)
    m = re.search(r"(\d+)\s*m(?:in)?", text)
    if not h:
        return None
    return round(int(h.group(1)) + (int(m.group(1)) if m else 0) / 60, 2)


def get_remaining(page) -> float | None:
    body = page.inner_text("body")
    m = re.search(r"(\d+h\s*\d+m\s*remaining)", body)
    if m:
        return parse_remaining(m.group(1))
    return None


def renew(page) -> bool:
    try:
        btn = page.query_selector('button:has-text("Add 24 Hours")')
        if not btn:
            log("⚠️ 未找到 +Add 24 Hours 按钮")
            return False
        disabled = btn.get_attribute("disabled")
        if disabled is not None:
            log("⏳ Add 24 Hours 按钮不可用（剩余 >24h）")
            return False
        btn.click()
        log("✅ 成功点击 +Add 24 Hours")
        page.wait_for_timeout(3000)
        return True
    except Exception as e:
        log(f"❌ 点击续期按钮时出错: {e}")
        return False


def save_storage(context):
    state = context.storage_state()
    with open(STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    log(f"💾 storage_state.json 已保存 (cookies: {len(state['cookies'])})")


def build_tg_message(lines: list) -> str:
    """将运行摘要拼成 Telegram HTML 消息"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = f"<b>🔄 Acore Hosting 自动续期</b>\n{now}\n" + "─" * 20 + "\n"
    for line in lines:
        msg += line + "\n"
    return msg


def main():
    log("=" * 50)
    log("🚀 Acore Hosting 自动化续期脚本")

    summary = []  # 收集摘要行，最后发 TG

    if not os.path.exists(STORAGE_PATH):
        msg = "❌ 找不到 storage_state.json"
        log(msg)
        summary.append(msg)
        send_tg(build_tg_message(summary))
        return

    # GitHub Actions 环境自动 headless，否则可传 --headless 或默认有头
    headless = os.environ.get("GITHUB_ACTIONS") == "true" or "--headless" in sys.argv
    log(f"{'🖥️ 无头模式' if headless else '🪟 有头模式'}")

    browser = launch(headless=headless)
    context = browser.new_context(storage_state=STORAGE_PATH)
    page = context.new_page()

    renewed = False
    remaining = None
    error = None

    try:
        log("🌐 打开 dashboard...")
        page.goto("https://zero.acorehosting.com/dashboard", wait_until="networkidle")
        page.wait_for_timeout(3000)

        current_url = page.url
        if "login" in current_url.lower() or "auth" in current_url.lower():
            msg = "❌ 会话已过期"
            log(msg)
            summary.append(msg)
            browser.close()
            send_tg(build_tg_message(summary))
            return

        log(f"✅ 登录有效 — {current_url}")
        summary.append("✅ 登录有效")

        remaining = get_remaining(page)
        if remaining is None:
            log("⚠️ 无法解析剩余时间")
            summary.append("⚠️ 无法解析剩余时间")
            save_storage(context)
            browser.close()
            send_tg(build_tg_message(summary))
            return

        log(f"⏰ 剩余时间: {remaining:.1f} 小时")
        summary.append(f"⏰ 剩余时间: {remaining:.1f} 小时")

        if remaining <= 24:
            log("🔧 剩余 <24h，尝试点击续期")
            summary.append("🔧 尝试续期...")
            clicked = renew(page)
            if clicked:
                renewed = True
                page.wait_for_timeout(2000)
                new_remaining = get_remaining(page)
                if new_remaining:
                    log(f"⏰ 续期后剩余时间: {new_remaining:.1f} 小时")
                    summary.append(f"✅ 续期成功！剩余 {new_remaining:.1f}h")
        else:
            log(f"⏳ 剩余 {remaining:.1f}h > 24h，无需续期")
            summary.append(f"⏳ 无需续期（{remaining:.1f}h > 24h）")

        save_storage(context)
        summary.append("💾 storage_state 已更新")
        log("✅ 脚本执行完成")
        summary.append("✅ 执行完成")

    except Exception as e:
        error = str(e)
        log(f"❌ 脚本异常: {e}")
        summary.append(f"❌ 异常: {e}")
    finally:
        browser.close()

    # 发送 Telegram 通知
    send_tg(build_tg_message(summary))


if __name__ == "__main__":
    main()
