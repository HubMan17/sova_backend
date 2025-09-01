# telemetry_utils.py (или рядом с APIView)
from django.utils import timezone
from datetime import datetime
from app.models import Board
from .notify import tg_send

def _power_on_criteria(p: dict) -> bool:
    # Считаем «включился», если есть явные признаки активности:
    if p.get("arm") in (1, True):               # взведён
        return True
    if p.get("mode"):                           # режим известен
        return True
    if p.get("gps"):                            # есть GPS фиксация ("3D"/"2D")
        return True
    if p.get("volt") is not None:
        try:
            return float(p["volt"]) > 10.0
        except Exception:
            pass
    if any(p.get(k) is not None for k in ("gs", "airspd", "yaw", "hdg")):
        return True
    if p.get("lat") is not None and p.get("lon") is not None:
        return True
    return False

def maybe_mark_power_on(board: Board, payload: dict, ts):
    if ts is None:
        ts = timezone.now()

    board.last_telemetry_at = ts

    # обновляем быстрые поля независимо от статуса
    if payload.get("mode"): board.last_mode = payload["mode"]
    if payload.get("volt") is not None:
        try: board.last_volt = float(payload["volt"])
        except Exception: pass

    if board.is_online:
        board.save(update_fields=["last_telemetry_at", "last_mode", "last_volt"])
        return False

    if _power_on_criteria(payload):
        board.is_online = True
        board.online_since = ts
        board.save(update_fields=["is_online", "online_since", "last_telemetry_at", "last_mode", "last_volt"])
        # тут можете дернуть уведомление в ТГ
        from .notify import tg_send
        tg_send(f"🟢 Борт #{board.boat_number} включился …")
        return True

    board.save(update_fields=["last_telemetry_at", "last_mode", "last_volt"])
    return False
