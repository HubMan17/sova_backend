from datetime import timedelta
from django.utils import timezone
from django.db.models import Q
from celery import shared_task
from app.models import Board
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

logger = logging.getLogger(__name__)

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
                tg_send(msg)
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
                    send_resp = tg_send(caption)

                if not send_resp.get("ok"):
                    logger.error("[Stage2] board=%s send failed: %s", board.id, send_resp)
                else:
                    logger.info("[Stage2] board=%s send ok: %s", board.id, send_resp.get("result", {}).get("message_id"))
            except Exception:
                logger.exception("sending Stage2 report failed for board %s", board.id)
                tg_send(caption)

            board.prolonged_offline_notified_at = now
            board.is_online = False
            if not board.offline_since:
                board.offline_since = now
            board.save(update_fields=["prolonged_offline_notified_at", "is_online", "offline_since"])

    return transitioned
