from django.utils.html import escape
from django.conf import settings
from django.utils import timezone
from app.models import Board
from .notify import tg_send, tg_send_location, normalize_coords

from django.utils import timezone as tz

def _power_on_criteria(p: dict) -> bool:
    if p.get("arm") in (1, True): return True
    if p.get("mode"): return True
    if p.get("gps"): return True
    if p.get("volt") is not None:
        try: return float(p["volt"]) > 10.0
        except Exception: pass
    if any(p.get(k) is not None for k in ("gs", "airspd", "yaw", "hdg")): return True
    if p.get("lat") is not None and p.get("lon") is not None: return True
    return False

def _track_link(boat_number: int, sess: str | None) -> str:
    """
    Публичная ссылка трека по НОМЕРУ БОРТА.
    Если sess есть — ведём на конкретную сессию, иначе — на last.
    """
    base = getattr(settings, "PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    if sess:
        return f"{base}/api/v1/track/board/{boat_number}/session/{sess}/"
    return f"{base}/api/v1/track/board/{boat_number}/last/"

def _to_local(dt):
    """Приводим datetime к локальной зоне (settings.TIME_ZONE) для отображения."""
    if not dt:
        return None
    if tz.is_naive(dt):
        dt = tz.make_aware(dt, tz=tz.get_default_timezone())
    return tz.localtime(dt)

def _fmt_power_on_message(board: Board, payload: dict, ts) -> str:
    """
    Формирует текст уведомления «борт включился».
    Ссылку строим по НОМЕРУ БОРТА: если есть sess -> /session/{sess}/, иначе -> /last/.
    """
    mode = payload.get("mode") or "—"
    arm = "Да" if payload.get("arm") in (1, True) else "Нет"

    # напряжение
    volt = "—"
    if payload.get("volt") is not None:
        try:
            volt = f"{float(payload['volt']):.1f} В"
        except Exception:
            pass

    # время → локально (Europe/Moscow)
    dt_local = _to_local(ts or tz.now())
    ts_str = dt_local.strftime("%H:%M:%S %d.%m.%Y") if dt_local else "—"

    # координаты (для текста)
    lat, lon = normalize_coords(payload.get("lat"), payload.get("lon"))
    coords_str = f"{lat}, {lon}" if (lat is not None and lon is not None) else "неизвестно"

    # сессия (как пришла из телеметрии)
    sess = payload.get("sess") or payload.get("session") or payload.get("sess_id")
    sess_str = escape(sess) if sess else "—"

    # ссылка трека ТОЛЬКО по номеру борта
    track_url = _track_link(board.boat_number, sess if sess else None)

    return (
        f"🟢 <b>Борт #{board.boat_number} включился</b>\n"
        f"📅 <b>Время:</b> {ts_str}\n"
        f"⚙️ <b>Режим:</b> {escape(mode)}\n"
        f"🔒 <b>Arm:</b> {arm}\n"
        f"🔋 <b>Напряжение:</b> {volt}\n"
        f"🧭 <b>Сессия:</b> {sess_str}\n"
        f"📍 <b>Положение:</b> {coords_str}\n"
        f"🗺 <a href=\"{track_url}\">Открыть интерактивную карту</a>"
    )

def _maybe_report_first_position_after_power_on(board: Board, payload: dict, ts) -> bool:
    lat, lon = normalize_coords(payload.get("lat"), payload.get("lon"))
    if lat is None or lon is None:
        return False
    # already reported for current online run?
    if board.last_pos_reported_at and board.online_since and board.last_pos_reported_at >= board.online_since:
        return False

    # время → локально (Europe/Moscow)
    dt_local = _to_local(ts or tz.now())
    ts_str = dt_local.strftime("%H:%M:%S %d.%m.%Y") if dt_local else "—"

    msg = (
        f"📡 <b>Борт #{board.boat_number} обнаружен на координатах</b>\n"
        f"📅 <b>Время:</b> {ts_str}\n"
        f"🌍 <b>Координаты:</b> {lat}, {lon}"
    )
    thread_id = getattr(settings, "TELEGRAM_THREAD_ID", None)
    tg_send(msg, thread_id=thread_id, parse_mode="HTML")
    tg_send_location(lat, lon)

    board.last_lat = lat
    board.last_lon = lon
    # сохраняем «момент отправки» как aware (Django сам хранит в UTC при USE_TZ=True)
    board.last_pos_reported_at = ts or tz.now()
    board.save(update_fields=["last_lat", "last_lon", "last_pos_reported_at"])
    return True

def maybe_mark_power_on(board: Board, payload: dict, ts):
    if ts is None:
        ts = timezone.now()

    # fast fields
    board.last_telemetry_at = ts
    if payload.get("mode"): board.last_mode = payload["mode"]
    if payload.get("volt") is not None:
        try: board.last_volt = float(payload["volt"])
        except Exception: pass

    # if already online -> update + maybe first position
    if board.is_online:
        fields = ["last_telemetry_at", "last_mode", "last_volt"]
        # refresh current session if present
        sess_val = payload.get("sess") or payload.get("session") or payload.get("sess_id")
        if hasattr(board, "current_sess") and sess_val:
            board.current_sess = str(sess_val)
            fields.append("current_sess")
        board.save(update_fields=fields)
        _maybe_report_first_position_after_power_on(board, payload, ts)
        return False

    # was offline -> check power-on criteria
    if _power_on_criteria(payload):
        lat, lon = normalize_coords(payload.get("lat"), payload.get("lon"))
        sess_val = payload.get("sess") or payload.get("session") or payload.get("sess_id")

        board.is_online = True
        board.online_since = ts
        board.offline_since = None
        board.last_offline_notified_at = None
        board.prolonged_offline_notified_at = None

        board.last_lat = lat
        board.last_lon = lon
        board.last_pos_reported_at = None

        fields = [
            "is_online", "online_since", "offline_since",
            "last_offline_notified_at", "prolonged_offline_notified_at",
            "last_telemetry_at", "last_mode", "last_volt",
            "last_lat", "last_lon", "last_pos_reported_at",
        ]
        if hasattr(board, "current_sess") and sess_val:
            board.current_sess = str(sess_val)
            fields.append("current_sess")

        board.save(update_fields=fields)

        thread_id = getattr(settings, "TELEGRAM_THREAD_ID", None)
        tg_send(_fmt_power_on_message(board, payload, ts), thread_id=thread_id, parse_mode="HTML")
        if lat is not None and lon is not None:
            tg_send_location(lat, lon)
            board.last_pos_reported_at = ts
            board.save(update_fields=["last_pos_reported_at"])
        return True

    # criteria failed -> save basics
    board.save(update_fields=["last_telemetry_at", "last_mode", "last_volt"])
    return False
