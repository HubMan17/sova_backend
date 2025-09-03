import os
import math
from typing import List, Tuple, Iterable

Point = Tuple[float, float]  # (lat, lon)

def _downsample(points: List[Point], max_points: int = 90) -> List[Point]:
    n = len(points)
    if n <= max_points:
        return points
    step = math.ceil(n / (max_points - 2))
    return [points[0]] + points[1:-1:step] + [points[-1]]

def _bbox(points: Iterable[Point], pad: float = 0.002):
    """Возвращает (min_lon, min_lat, max_lon, max_lat) с небольшой «подушкой»."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    return (min_lon - pad, min_lat - pad, max_lon + pad, max_lat + pad)

def build_yandex_route_url_v1(
    points: List[Point],
    width: int = 650,
    height: int = 450,
    line_color: str = "8822DDC0",  # ARGB, как в примере Яндекс (фиолетовый с альфой)
    line_width: int = 5,
    add_mid_markers: bool = True,
    mid_every: int = 1,  # ставить метку на каждом mid_every-том узле после старта и до финиша
) -> str:
    """
    Yandex Static Maps v1:
      - полилиния: pl=c:<ARGB>,w:<width>,lon1,lat1,lon2,lat2,...
      - метки:     pt=lon,lat,style~lon,lat,style...
      - рамка:     bbox=min_lon,min_lat~max_lon,max_lat
    Старт — зелёная метка, финиш — красная, промежуточные — синие (опционально).
    """
    if not points:
        raise ValueError("No points provided")

    pts = _downsample(points, max_points=90)

    # polyline
    coords = ",".join(f"{lon:.6f},{lat:.6f}" for (lat, lon) in pts)
    pl = f"pl=c:{line_color},w:{int(line_width)},{coords}"

    # markers: start (green), end (red), mids (blue)
    start = pts[0]
    end = pts[-1]

    marker_parts = [
        f"{start[1]:.6f},{start[0]:.6f},pm2gnm",
        f"{end[1]:.6f},{end[0]:.6f},pm2rdm",
    ]

    if add_mid_markers and len(pts) > 2 and mid_every > 0:
        for i in range(1, len(pts) - 1, mid_every):
            lat, lon = pts[i]
            marker_parts.append(f"{lon:.6f},{lat:.6f},pm2blm")  # blue mid marker

    pt = "pt=" + "~".join(marker_parts)

    # bbox
    min_lon, min_lat, max_lon, max_lat = _bbox(pts)
    bbox = f"bbox={min_lon:.6f},{min_lat:.6f}~{max_lon:.6f},{max_lat:.6f}"

    # final url (v1 endpoint)
    url = (
        "https://static-maps.yandex.ru/v1?"
        f"{pl}&{pt}&{bbox}&size={width},{height}&l=map"
    )
    apikey = os.getenv("YANDEX_MAPS_APIKEY") or os.getenv("YANDEX_STATIC_APIKEY")
    if apikey:
        url += f"&apikey={apikey}"   # <-- ключ обязателен для линий/стилей на /v1
    return url
# ------------------ distance helpers ------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние по сфере (метры)."""
    R = 6371000.0
    from math import radians, sin, cos, atan2, sqrt
    φ1, φ2 = radians(lat1), radians(lat2)
    dφ = φ2 - φ1
    dλ = radians(lon2 - lon1)
    a = sin(dφ/2)**2 + cos(φ1) * cos(φ2) * sin(dλ/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

def total_distance_m(points: List[Point]) -> float:
    """Сумма расстояний между последовательными точками (метры)."""
    if len(points) < 2:
        return 0.0
    dist = 0.0
    for (la1, lo1), (la2, lo2) in zip(points, points[1:]):
        dist += haversine_m(la1, lo1, la2, lo2)
    return dist
