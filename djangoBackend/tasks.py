from datetime import datetime, timezone, timedelta
from celery import shared_task
from django.db.models import Q
from app.models import Board
from api_v1.urils.notify import tg_send

@shared_task
def check_offline_boards(timeout_minutes: int = 3):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=timeout_minutes)

    qs = Board.objects.filter(is_online=True).filter(
        Q(last_telemetry_at__isnull=True) | Q(last_telemetry_at__lt=cutoff)
    )

    cnt = 0
    for b in qs:
        b.is_online = False
        b.save(update_fields=["is_online"])
        when = (b.last_telemetry_at or cutoff).astimezone().strftime("%d.%m.%Y %H:%M:%S")
        tg_send(f"🔴 <b>Борт #{b.boat_number}</b> офлайн\n• Последняя телеметрия: <code>{when}</code>")
        cnt += 1
    return cnt