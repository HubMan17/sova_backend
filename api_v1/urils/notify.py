import json
import os
import re
from django.conf import settings
import requests
import logging
from .route_map import build_yandex_route_url_v1
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")   or os.getenv("TG_CHAT_ID", "")
TELEGRAM_THREAD_ID = os.getenv("TELEGRAM_THREAD_ID") or os.getenv("TG_THREAD_ID", "")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _has_creds() -> bool:
    ok = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
    if not ok:
        logger.error("Telegram credentials are missing (token or chat id)")
    return ok

def _normalize_thread_id(x):
    try:
        return int(x) if str(x).strip() else None
    except Exception:
        return None


def _json_or_text(resp):
    ctype = resp.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        try:
            return resp.json()
        except Exception:
            return {"ok": False, "status_code": resp.status_code, "text": resp.text}
    return {"ok": False, "status_code": resp.status_code, "text": resp.text}

def _post(method: str, payload: dict, timeout: int = 10) -> dict:
    url = f"{BASE_URL}/{method}"
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        try:
            data = r.json()
        except Exception:
            data = {"ok": False, "status_code": r.status_code, "raw": r.text}
        print(f"[tg] POST {method} → {r.status_code} | payload={json.dumps(payload, ensure_ascii=False)} | resp={json.dumps(data, ensure_ascii=False)}")
        return data
    except Exception as e:
        print(f"[tg] ERROR request: {e!r}")
        return {"ok": False, "error": repr(e)}

def tg_send(text: str, parse_mode: str = "HTML", thread_id: int | str | None = None) -> dict:
    if not _has_creds():
        return {"ok": False, "error": "missing creds"}

    # приоритет: явный thread_id аргумент > ENV
    tid = _normalize_thread_id(thread_id) if thread_id is not None else _normalize_thread_id(TELEGRAM_THREAD_ID)

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if tid is not None:
        payload["message_thread_id"] = tid

    try:
        r = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=10)
        data = _json_or_text(r)

        # если проблема именно с топиком — повторим без message_thread_id
        if not data.get("ok"):
            desc = (data.get("description") or "").lower()
            if tid is not None and ("thread" in desc or "topic" in desc or "message_thread_id" in desc):
                payload.pop("message_thread_id", None)
                r2 = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=10)
                data2 = _json_or_text(r2)
                if not data2.get("ok"):
                    logger.error("tg_send retry(no thread) error: %s", data2)
                return data2

        if not data.get("ok"):
            logger.error("tg_send error: %s", data)
        return data
    except Exception as e:
        logger.exception("tg_send exception")
        return {"ok": False, "error": str(e)}

# def tg_send(text: str, parse_mode: str = "HTML") -> dict:
#     if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
#         logger.error("Telegram credentials are missing")
#         return {"ok": False, "error": "Telegram credentials are missing"}
#     payload = {
#         "chat_id": TELEGRAM_CHAT_ID,
#         "text": text,
#         "parse_mode": parse_mode,
#         "disable_web_page_preview": True,
#     }
#     if TELEGRAM_THREAD_ID:
#         payload["message_thread_id"] = int(TELEGRAM_THREAD_ID)
#     try:
#         r = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=10)
#         data = _json_or_text(r)
#         if not data.get("ok"):
#             logger.error("tg_send error: %s", data)
#         return data
#     except Exception as e:
#         logger.exception("tg_send exception")
#         return {"ok": False, "error": str(e)}

def tg_send_location(lat: float, lon: float) -> dict:
    if not _has_creds():
        return {"ok": False, "error": "missing creds"}
    payload = {"chat_id": TELEGRAM_CHAT_ID, "latitude": float(lat), "longitude": float(lon)}
    tid = _normalize_thread_id(TELEGRAM_THREAD_ID)
    if tid is not None:
        payload["message_thread_id"] = tid
    try:
        r = requests.post(f"{BASE_URL}/sendLocation", json=payload, timeout=10)
        data = _json_or_text(r)
        if not data.get("ok"):
            logger.error("tg_send_location error: %s", data)
        return data
    except Exception as e:
        logger.exception("tg_send_location exception")
        return {"ok": False, "error": str(e)}

# ---- coords normalization helpers ----
_float_re = re.compile(r"[-+]?\d+(?:\.\d+)?")

def parse_first_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", ".")
    m = _float_re.search(s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None

def normalize_coords(lat, lon):
    flat = parse_first_float(lat)
    flon = parse_first_float(lon)
    if flat is None or flon is None:
        return None, None
    if not (-90 <= flat <= 90 and -180 <= flon <= 180):
        return None, None
    return round(flat, 7), round(flon, 7)


def build_yandex_interactive_url(points):
    if not points:
        return "https://yandex.ru/maps/"
    start = points[0]; end = points[-1]
    center = f"{end[1]:.6f},{end[0]:.6f}"
    # добавим оба маркера; (интерактивной линии у Яндекс-карт «как есть» нет)
    pt = f"{start[1]:.6f},{start[0]:.6f},pm2gnm~{end[1]:.6f},{end[0]:.6f},pm2rdm"
    return f"https://yandex.ru/maps/?ll={center}&pt={quote_plus(pt)}&z=12"

def _fetch_static_map_png(points) -> bytes | None:
    """
    Скачиваем картинку статической карты (PNG) у Яндекса.
    Возвращаем bytes или None.
    """
    url = build_yandex_route_url_v1(points)  # включает apikey, bbox, pl, pt
    try:
        r = requests.get(url, timeout=20)
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and ct.startswith("image/"):
            return r.content
        logger.error("Yandex static map GET failed: code=%s ct=%s text=%s",
                     r.status_code, ct, r.text[:300])
    except Exception:
        logger.exception("Yandex static map GET exception")
    return None

def tg_send_route_map(points, caption: str) -> dict:
    """
    1) Пытаемся сами скачать PNG статической карты и отправить его как файл (sendPhoto files=...).
    2) Если не вышло — отправляем текст + интерактивную ссылку (надежный фоллбэк).
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram credentials are missing")
        return {"ok": False, "error": "Telegram credentials are missing"}

    # сначала загрузим картинку на бэкенде
    png = _fetch_static_map_png(points)
    if png:
        files = {
            "photo": ("route.png", png, "image/png"),
        }
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": caption,
        }
        if TELEGRAM_THREAD_ID:
            data["message_thread_id"] = int(TELEGRAM_THREAD_ID)
        try:
            r = requests.post(f"{BASE_URL}/sendPhoto", data=data, files=files, timeout=20)
            resp = r.json() if r.headers.get("content-type","").startswith("application/json") else {"ok": False, "text": r.text}
            if resp.get("ok"):
                return resp
            logger.error("sendPhoto(file) failed: %s", resp)
        except Exception:
            logger.exception("sendPhoto(file) exception")

    # фоллбэк — текст + интерактивная ссылка
    link = build_yandex_interactive_url(points)
    thread_id = getattr(settings, "TELEGRAM_THREAD_ID", None)
    return tg_send(caption + "\n🗺 " + link, thread_id=thread_id)