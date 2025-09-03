from django.utils import timezone
from app.models import Board
from .notify import tg_send, tg_send_location, normalize_coords

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

def _fmt_power_on_message(board: Board, payload: dict, ts) -> str:
    mode = payload.get("mode", "—")
    arm = "Да" if payload.get("arm") in (1, True) else "Нет"
    volt = "—"
    if payload.get("volt") is not None:
        try: volt = f"{float(payload['volt']):.1f} В"
        except Exception: pass
    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
    lat, lon = normalize_coords(payload.get("lat"), payload.get("lon"))
    coords_str = f"{lat}, {lon}" if lat is not None and lon is not None else "неизвестно"
    sess = payload.get("sess") or payload.get("session") or payload.get("sess_id") or "—"
    return (
        f"🟢 <b>Борт #{board.boat_number} включился</b>\n"
        f"📅 <b>Время:</b> {ts_str}\n"
        f"⚙️ <b>Режим:</b> {mode}\n"
        f"🔒 <b>Arm:</b> {arm}\n"
        f"🔋 <b>Напряжение:</b> {volt}\n"
        f"🧭 <b>Сессия:</b> {sess}\n"
        f"📍 <b>Положение:</b> {coords_str}"
    )

def _maybe_report_first_position_after_power_on(board: Board, payload: dict, ts) -> bool:
    lat, lon = normalize_coords(payload.get("lat"), payload.get("lon"))
    if lat is None or lon is None:
        return False
    # already reported for current online run?
    if board.last_pos_reported_at and board.online_since and board.last_pos_reported_at >= board.online_since:
        return False
    ts_str = (ts or timezone.now()).strftime("%Y-%m-%d %H:%M:%S")
    msg = (
        f"📡 <b>Борт #{board.boat_number} обнаружен на координатах</b>\n"
        f"📅 <b>Время:</b> {ts_str}\n"
        f"🌍 <b>Координаты:</b> {lat}, {lon}"
    )
    tg_send(msg)
    tg_send_location(lat, lon)
    board.last_lat = lat
    board.last_lon = lon
    board.last_pos_reported_at = ts or timezone.now()
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

        tg_send(_fmt_power_on_message(board, payload, ts))
        if lat is not None and lon is not None:
            tg_send_location(lat, lon)
            board.last_pos_reported_at = ts
            board.save(update_fields=["last_pos_reported_at"])
        return True

    # criteria failed -> save basics
    board.save(update_fields=["last_telemetry_at", "last_mode", "last_volt"])
    return False
