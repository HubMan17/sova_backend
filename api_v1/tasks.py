from datetime import datetime, timedelta
from django.utils import timezone
from django.db.models import Q

from datetime import datetime, timezone as dt_tz

from api_v1.urils.notify_format import ArmProgress, build_arm_report_message

from celery import shared_task
from app.models import ArmReport, Board
from api_v1.urils.notify import tg_send, tg_send_route_map
from api_v1.urils.route_map import total_distance_m
from api_v1.urils.route_query import (
    detect_latest_session,
    get_route_points_by_session,
    get_recent_route_points,
    get_last_n_points,
)
import logging
from django.conf import settings

import os
import requests

from celery import shared_task
from django.utils import timezone as tz
from django.db import transaction


logger = logging.getLogger(__name__)


# === ГЛОБАЛЬНЫЕ ПОРОГИ (можно вынести в settings.py при желании) =============
# Пороговые значения — пока глобалки (как и просили)
ARM_LIMIT_COUNT = int(os.getenv("ARM_LIMIT_COUNT", "10"))     # например, 10
ARM_LIMIT_TIME_S = float(os.getenv("ARM_LIMIT_TIME_S", "350"))  # 5м50с = 350с
QSTAB_LIMIT_TIME_S = float(os.getenv("QSTAB_LIMIT_TIME_S", "30"))


# ---------- helpers ----------
NBSP = "\u00A0"

def _fmt_hhmmss_ddmmyyyy(dt: datetime) -> str:
    # 21:08:57 · 06.09.2025
    return dt.strftime(f"%H:%M:%S{NBSP}·{NBSP}%d.%m.%Y")

def _fmt_sec_human(sec) -> str:
    try:
        s = max(0, int(round(float(sec))))
    except Exception:
        s = 0
    m, ss = divmod(s, 60)
    h, m  = divmod(m, 60)
    if h:  return f"{h}ч{NBSP}{m}м{NBSP}{ss}с"
    if m:  return f"{m}м{NBSP}{ss}с"
    return f"{ss}с"

def _left(used, limit_):
    try:
        return max(0, int(round(float(limit_) - float(used))))
    except Exception:
        return 0

def _calc_left(used: float, limit_: float) -> int:
    return max(0, int(round(limit_ - used)))

def _fmt_dur(sec: float | int) -> str:
    s = max(0, int(sec or 0))
    h, m = divmod(s, 3600)
    m, s = divmod(m, 60)
    if h:
        return f"{h}мч {m}м {s}с"
    if m:
        return f"{m}м {s}с"
    return f"{s}с"

def _tg_env():
    """
    Поддерживаем оба формата ENV:
      1) BOT_TOKEN / TG_CHAT_ID / TG_THREAD_ID
      2) TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / TELEGRAM_THREAD_ID
    """
    token = (
        os.getenv("BOT_TOKEN")
        or os.getenv("TELEGRAM_BOT_TOKEN")
        or ""
    )
    chat_id = (
        os.getenv("TG_CHAT_ID")
        or os.getenv("TELEGRAM_CHAT_ID")
        or ""
    )
    thread_id = (
        os.getenv("TG_THREAD_ID")
        or os.getenv("TELEGRAM_THREAD_ID")
        or ""
    )
    return token.strip(), chat_id.strip(), thread_id.strip()

# def tg_send_text(text: str, parse_mode: str | None = None) -> dict | None:
#     token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
#     chat  = getattr(settings, "TELEGRAM_CHAT_ID", 0)
#     if not token or not chat:
#         return None
#     url = f"https://api.telegram.org/bot{token}/sendMessage"
#     payload = {
#         "chat_id": chat,
#         "text": text,
#         "disable_web_page_preview": True,
#     }
#     if parse_mode:
#         payload["parse_mode"] = parse_mode
#     try:
#         r = requests.post(url, data=payload, timeout=10)
#         return r.json()
#     except Exception:
#         return None

def _progress_line(title: str, pct: float, used: str, limit: str, spare: str) -> str:
    # пример:  • ARM-count: 100%  (12/10, запас 0 шт)
    return f"  • {title}: {int(round(pct))}%  ({used} / {limit}, запас {spare})"

@shared_task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 2})
def send_arm_report_notification(self, rpt_id: int) -> int:
    from app.models import ArmReport
    rpt = ArmReport.objects.get(id=rpt_id)

    ts_str = (rpt.ts.astimezone(timezone.get_current_timezone())
              .strftime("%H:%M:%S %d.%m.%Y")) if rpt.ts else "-"

    board_label = f"#{getattr(rpt, 'boat_number', None) or getattr(rpt, 'boat', None) or '-'}"

    pr = ArmProgress(
        arms=int(rpt.arms or 0),
        arm_sec=float(rpt.arm_sec or 0.0),
        qstab_sec=float(rpt.qstab_sec or 0.0),
    )

    msg = build_arm_report_message(ts_str=ts_str, board_label=board_label, progress=pr)

    # <- отправляем именно в тему ARM_REPORT_TOPIC_ID
    thread_id = getattr(settings, "ARM_REPORT_TOPIC_ID", None)
    res = tg_send(msg, thread_id=thread_id, parse_mode="HTML")

    return 1 if (res and res.get("ok")) else 0

# 
#   Make online and telem report
# 
def _fmt_timedelta(td) -> str:
    total = int(td.total_seconds()) if td else 0
    if total < 0: total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h: return f"{h}h {m:02d}m"
    if m: return f"{m}m {s:02d}s"
    return f"{s}s"


def _track_link(board_id: int, sess: str | None) -> str:
    base = getattr(settings, "PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    if sess:
        return f"{base}/api/v1/track/board/{board_id}/session/{sess}/"
    return f"{base}/api/v1/track/board/{board_id}/last/"

@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def check_offline_boards(self, inactive_minutes: int = 3, prolonged_minutes: int = 10) -> int:
    """
    Stage 1 (inactive_minutes, e.g. 3m): mark offline + send 'telemetry stopped' ONCE.
    Stage 2 (prolonged_minutes, e.g. 10m): send extended report ONCE: route map (by session) + stats.
    """
    now = timezone.now()
    thr1 = now - timedelta(minutes=inactive_minutes)
    thr2 = now - timedelta(minutes=prolonged_minutes)

    # candidates: were online or already have offline_since, but telemetry is stale/None
    candidates = Board.objects.filter(
        Q(is_online=True) | Q(offline_since__isnull=False)
    ).filter(
        Q(last_telemetry_at__lte=thr1) | Q(last_telemetry_at__isnull=True)
    )

    transitioned = 0

    for board in candidates.iterator():
        last_ts = board.last_telemetry_at
        off_since = board.offline_since

        # -------- Stage 1: first warning (3m) --------
        need_stage1 = (
            ((last_ts is None) or (last_ts <= thr1)) and
            (board.last_offline_notified_at is None or
             (off_since and board.last_offline_notified_at < off_since))
        )
        if need_stage1:
            if board.is_online:
                board.is_online = False
                board.offline_since = off_since or now  # фиксация момента офлайна
                off_since = board.offline_since

            msg = (
                f"🟠 <b>Борт #{board.boat_number}</b>\n"
                f"📡 Телеметрия перестала поступать ≥ {inactive_minutes} мин назад.\n"
                f"Причина: борт выключен или потеряна связь."
            )
            try:
                thread_id = getattr(settings, "TELEGRAM_THREAD_ID", None)
                tg_send(msg, parse_mode="HTML", thread_id=thread_id)
            except Exception:
                logger.exception("tg_send Stage1 failed for board %s", board.id)
            board.last_offline_notified_at = now
            board.save(update_fields=["is_online", "offline_since", "last_offline_notified_at"])
            transitioned += 1

        # -------- Stage 2: extended report (10m) --------
        offline_long_enough = (off_since and (off_since <= thr2))
        last_ts_old_enough  = ((last_ts is None) or (last_ts <= thr2))

        p_notif = board.prolonged_offline_notified_at
        stage2_guard_ok = (
            p_notif is None
            or (off_since and p_notif < off_since)
            or (last_ts and p_notif < last_ts)
        )

        need_stage2 = (offline_long_enough or last_ts_old_enough) and stage2_guard_ok

        logger.info(
            "[Stage2 check] board=%s need=%s | last_ts=%s off_since=%s thr2=%s "
            "p_notif=%s guard_ok=%s flags(is_online=%s,last_offline_notified_at=%s)",
            board.id, need_stage2,
            last_ts.isoformat() if last_ts else None,
            off_since.isoformat() if off_since else None,
            thr2.isoformat(),
            p_notif.isoformat() if p_notif else None,
            stage2_guard_ok,
            board.is_online,
            board.last_offline_notified_at.isoformat() if board.last_offline_notified_at else None,
        )

        if need_stage2:
            start_ts = board.online_since or (last_ts or now)
            end_ts = last_ts or now

            # 1) сначала пытаемся строго по сессии
            sess = getattr(board, "current_sess", None) or detect_latest_session(board.id, start_ts, end_ts)
            points = get_route_points_by_session(board.id, sess, max_points=200) if sess else []

            if len(points) < 2:
                points = get_recent_route_points(board.id, board.online_since, end_ts, max_points=200)

            if len(points) < 2:
                points = get_last_n_points(board.id, n=200)

            logger.info("[Stage2] board=%s route points collected: %d (sess=%s)", board.id, len(points), sess)


            silence_td = (now - end_ts) if end_ts else timedelta(0)
            flight_td = (end_ts - start_ts) if (start_ts and end_ts and end_ts >= start_ts) else timedelta(0)
            distance_m = total_distance_m(points) if len(points) >= 2 else 0.0
            distance_km = distance_m / 1000.0

            start_str = start_ts.strftime("%Y-%m-%d %H:%M:%S") if start_ts else "—"
            last_str = end_ts.strftime("%Y-%m-%d %H:%M:%S") if end_ts else "—"

            caption = (
                f"🔴 Борт #{board.boat_number} так и не вышел на связь\n"
                f"🧭 Сессия: {sess or '—'}\n"
                f"🕒 Начало активности: {start_str}\n"
                f"📴 Последняя телеметрия: {last_str} (тишина { _fmt_timedelta(silence_td) })\n"
                f"⏱ Время активности: {_fmt_timedelta(flight_td)}\n"
                f"📏 Пройденная дистанция: {distance_km:.2f} км"
            )

            track_url = _track_link(board.id, sess if len(points) >= 2 else None)
            caption = (
                f"🔴 Борт #{board.boat_number} так и не вышел на связь\n"
                f"🧭 Сессия: {sess or '—'}\n"
                f"🕒 Начало активности: {start_str}\n"
                f"📴 Последняя телеметрия: {last_str} (тишина { _fmt_timedelta(silence_td) })\n"
                f"⏱ Время активности: {_fmt_timedelta(flight_td)}\n"
                f"📏 Пройденная дистанция: {distance_km:.2f} км\n"
                f"🗺 <a href=\"{track_url}\">Открыть интерактивную карту</a>"
            )

            try:
                if len(points) >= 2:
                    logger.info("[Stage2] board=%s sending route map (points=%d)", board.id, len(points))
                    send_resp = tg_send_route_map(points, caption=caption)
                else:
                    logger.info("[Stage2] board=%s sending text only (no route)", board.id)
                    if points:
                        lat, lon = points[-1]
                        caption += f"\n📍 Последняя точка: {lat:.6f}, {lon:.6f}"
                        
                    thread_id = getattr(settings, "TELEGRAM_THREAD_ID", None)
                    send_resp = tg_send(caption, parse_mode="HTML", thread_id=thread_id)

                if not send_resp.get("ok"):
                    logger.error("[Stage2] board=%s send failed: %s", board.id, send_resp)
                else:
                    logger.info("[Stage2] board=%s send ok: %s", board.id, send_resp.get("result", {}).get("message_id"))
            except Exception:
                logger.exception("sending Stage2 report failed for board %s", board.id)
                tg_send(caption, parse_mode="HTML")

            board.prolonged_offline_notified_at = now
            board.is_online = False
            if not board.offline_since:
                board.offline_since = now
            board.save(update_fields=["prolonged_offline_notified_at", "is_online", "offline_since"])

    return transitioned
