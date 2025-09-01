import os, json, requests
from django.conf import settings

TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN") or getattr(settings, "TELEGRAM_BOT_TOKEN", None)
TELEGRAM_CHAT_ID   = os.getenv("TG_CHAT_ID")   or getattr(settings, "TELEGRAM_CHAT_ID", None)
TELEGRAM_THREAD_ID = os.getenv("TG_THREAD_ID") or getattr(settings, "TELEGRAM_THREAD_ID", None)

def tg_send(text: str, parse_mode: str = "HTML", thread_id: int | None = None):
    if not TELEGRAM_BOT_TOKEN:
        print("[tg_send] ERROR: TELEGRAM_BOT_TOKEN is empty")
        return False
    if not TELEGRAM_CHAT_ID:
        print("[tg_send] ERROR: TELEGRAM_CHAT_ID is empty")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": int(TELEGRAM_CHAT_ID),
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    tid = thread_id or TELEGRAM_THREAD_ID
    if tid:
        try:
            data["message_thread_id"] = int(tid)
        except Exception:
            pass

    try:
        r = requests.post(url, data=data, timeout=15)
        ok = (r.status_code == 200 and r.json().get("ok"))
        if not ok:
            print(f"[tg_send] FAIL {r.status_code} {r.text}")
        return ok
    except Exception as e:
        print(f"[tg_send] EXC {e}")
        return False