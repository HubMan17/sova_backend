from typing import List, Tuple, Optional
from django.db.models import Q
from app.models import Telemetry
from django.utils import timezone
from datetime import timedelta

Point = Tuple[float, float]  # (lat, lon)

def detect_latest_session(board_id: int, start_ts, end_ts) -> Optional[str]:
    """
    Detect last session id within [start_ts, end_ts] for the board.
    """
    return (Telemetry.objects
            .filter(board_id=board_id, ts__gte=start_ts, ts__lte=end_ts)
            .exclude(Q(sess__isnull=True) | Q(sess__exact=""))
            .order_by("-ts")
            .values_list("sess", flat=True)
            .first())

def get_route_points_by_session(board_id: int, sess: str, max_points: int = 200) -> List[Point]:
    """
    Return ordered (lat, lon) points for given (board_id, sess), downsampled to max_points.
    """
    qs = (Telemetry.objects
          .filter(board_id=board_id, sess=sess)
          .exclude(Q(lat__isnull=True) | Q(lon__isnull=True))
          .order_by("ts")
          .values_list("lat", "lon"))
    pts = list(qs)
    n = len(pts)
    if n <= max_points:
        return pts
    step = max(1, n // max_points)
    return [pts[0]] + pts[1:-1:step] + [pts[-1]]

def get_recent_route_points(
    board_id: int,
    since_ts,
    until_ts,
    max_points: int = 200,
) -> List[Point]:
    """
    Точки за окно времени [since_ts, until_ts] для борта.
    Если since_ts пуст — берём за последние 30 минут.
    """
    if until_ts is None:
        until_ts = timezone.now()
    if since_ts is None:
        since_ts = until_ts - timedelta(minutes=30)

    qs = (
        Telemetry.objects
        .filter(board_id=board_id, ts__gte=since_ts, ts__lte=until_ts)
        .exclude(Q(lat__isnull=True) | Q(lon__isnull=True))
        .order_by("ts")
        .values_list("lat", "lon")
    )
    pts = list(qs)
    n = len(pts)
    if n <= max_points:
        return pts
    step = max(1, n // max_points)
    return [pts[0]] + pts[1:-1:step] + [pts[-1]]

def get_last_n_points(board_id: int, n: int = 200) -> List[Point]:
    """
    Просто последние n точек (с координатами) без сессии/окон.
    """
    qs = (
        Telemetry.objects
        .filter(board_id=board_id)
        .exclude(Q(lat__isnull=True) | Q(lon__isnull=True))
        .order_by("-ts")
        .values_list("lat", "lon")[:n]
    )
    pts = list(qs)[::-1]  # в хронологию
    return pts