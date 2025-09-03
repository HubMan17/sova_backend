from datetime import timedelta
from django.utils import timezone
from celery import shared_task
from django.db.models import Q
from app.models import Board
from api_v1.urils.notify import tg_send  # поправь путь, если notify лежит в другом месте

@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def check_offline_boards(self, inactive_minutes: int = 3) -> int:
    now = timezone.now()
    threshold = now - timedelta(minutes=inactive_minutes)

    candidates = Board.objects.filter(
        is_online=True
    ).filter(
        Q(last_telemetry_at__lt=threshold) | Q(last_telemetry_at__isnull=True)
    )

    transitioned = 0
    for board in candidates.iterator():
        if not board.is_online:
            continue
        board.is_online = False
        board.offline_since = now

        # антиспам: уведомляем один раз на оффлайн-сессию
        if not board.last_offline_notified_at or (board.offline_since and board.last_offline_notified_at < board.offline_since):
            ts_str = now.strftime("%Y-%m-%d %H:%M:%S")
            msg = (
                f"🔴 <b>Борт #{board.boat_number} офлайн</b>\n"
                f"⏱ <b>Последняя телеметрия ≥</b> {inactive_minutes} мин назад\n"
                f"📅 <b>Зафиксировано:</b> {ts_str}"
            )
            try:
                tg_send(msg)
            except Exception:
                pass
            board.last_offline_notified_at = now

        board.save(update_fields=["is_online", "offline_since", "last_offline_notified_at"])
        transitioned += 1

    return transitioned