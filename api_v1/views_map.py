# api_v1/views_map.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Dict, Tuple
import json

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Max, Min, Count
from django.http import HttpResponse, JsonResponse, Http404
from django.utils import timezone as tz
from django.utils.encoding import smart_str
from django.utils.html import escape
from django.utils.text import slugify
from django.views.decorators.http import require_GET

from app.models import Board, Telemetry  # Telemetry: board_id, sess, ts, lat, lon, alt, hdg, mode, volt

# ========================= НАСТРОЙКИ / КОНСТАНТЫ ==============================

# имена полей Telemetry (при необходимости переименуй)
SESS_FIELD = "sess"
TS_FIELD   = "ts"
LAT_FIELD  = "lat"
LON_FIELD  = "lon"
ALT_FIELD  = "alt_m"
HDG_FIELD  = "hdg"
MODE_FIELD = "mode"
VOLT_FIELD = "volt"

# статусы по «возрасту» последней точки (в секундах)
LOST_SECS   = 60      # ≥ 1 мин — "Связь потеряна"
FINISH_SECS = 180     # ≥ 3 мин — "Полёт завершён"

# оформление
POLYLINE_COLOR  = "#8E44AD"
POLYLINE_WEIGHT = 5

# CDN
LEAFLET_CSS  = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
LEAFLET_JS   = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
CLUSTER_CSS  = "https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"
CLUSTER_CSS_D= "https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"
CLUSTER_JS   = "https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"

# ============================== УТИЛИТЫ =======================================

def _resolve_fields() -> Tuple[str, str, str]:
    """возвращаем имена полей: session, ts, coords (lat,lon)"""
    return SESS_FIELD, TS_FIELD, f"{LAT_FIELD},{LON_FIELD}"

def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")

def _fmt_duration_human(sec: int) -> str:
    sec = max(0, int(sec or 0))
    h, m = divmod(sec, 3600)
    m, s = divmod(m, 60)
    if h:
        return f"{h}ч {m}м"
    if m:
        return f"{m}м {s}с"
    return f"{s}с"

def _telemetry_qs(board_id: int, sess: str):
    return (
        Telemetry.objects
        .filter(board_id=board_id, **{SESS_FIELD: sess})
        .order_by(TS_FIELD)
        .only(TS_FIELD, LAT_FIELD, LON_FIELD, ALT_FIELD)
    )

def _iter_points(board_id: int, sess: str):
    qs = _telemetry_qs(board_id, sess)
    for r in qs:
        if r.lat is None or r.lon is None:
            continue
        yield {
            "ts": tz.localtime(getattr(r, TS_FIELD)) if getattr(r, TS_FIELD) else None,
            "lat": float(getattr(r, LAT_FIELD)),
            "lon": float(getattr(r, LON_FIELD)),
            "alt": float(getattr(r, ALT_FIELD)) if getattr(r, ALT_FIELD) is not None else None,
        }

# ===================== СЕРВИСНЫЕ JSON ЭНДПОИНТЫ ==============================

@require_GET
def list_boards(request):
    rows = Board.objects.order_by("boat_number", "id").only("id", "boat_number")
    items = [{"id": b.id, "label": f"#{b.boat_number or b.id}"} for b in rows]
    return JsonResponse({"boards": items}, encoder=DjangoJSONEncoder)

@require_GET
def list_sessions(request, board_id: int):
    qs = (
        Telemetry.objects
        .filter(board_id=board_id)
        .values(SESS_FIELD)
        .annotate(cnt=Count("id"), ts_min=Min(TS_FIELD), ts_max=Max(TS_FIELD))
        .order_by("ts_min")
    )

    def fmt(dt):
        if not dt:
            return ""
        return tz.localtime(dt).strftime("%Y-%m-%d %H:%M")

    items = []
    for r in qs:
        s = r[SESS_FIELD]
        start = fmt(r["ts_min"])
        end   = fmt(r["ts_max"])
        cnt   = r["cnt"] or 0
        label = f"{start} → {end} ({cnt} тчк.)" if start and end else f"{s} ({cnt})"
        items.append({"sess": s, "label": label})

    return JsonResponse({"sessions": items}, encoder=DjangoJSONEncoder)

# ======================= ПОЛУЧЕНИЕ ДАННЫХ ДЛЯ КАРТЫ ===========================

def _sessions_index(board_id: int, limit: int = 200) -> List[Dict]:
    sess_field, _, _ = _resolve_fields()
    qs = (
        Telemetry.objects
        .filter(board_id=board_id)
        .exclude(**{f"{sess_field}__isnull": True})
        .values(sess_field)
        .annotate(first_ts=Min(TS_FIELD), last_ts=Max(TS_FIELD), cnt=Count("id"))
        .order_by("last_ts")  # старые -> новые
    )
    if limit:
        qs = qs[:limit]

    rows: List[Dict] = []
    for r in qs:
        s = r[sess_field]
        first_ts = r["first_ts"]
        last_ts  = r["last_ts"]
        cnt      = r["cnt"] or 0

        if first_ts and last_ts:
            if first_ts.date() == last_ts.date():
                label = f"{_fmt_dt(first_ts)} → {last_ts.strftime('%H:%M')}  ({cnt} тчк.)"
            else:
                label = f"{_fmt_dt(first_ts)} → {_fmt_dt(last_ts)}  ({cnt} тчк.)"
        else:
            label = f"{s}  ({cnt} тчк.)"

        rows.append({
            "sess": s,
            "first_ts": first_ts,
            "last_ts": last_ts,
            "cnt": cnt,
            "label": label,
        })
    return rows

def _fetch_points_for_session(board_id: int, sess: str) -> List[Dict]:
    sess_field, ts_field, _ = _resolve_fields()
    qs = (
        Telemetry.objects
        .filter(board_id=board_id, **{sess_field: sess})
        .order_by(ts_field)
        .values(LAT_FIELD, LON_FIELD, ts_field, ALT_FIELD, HDG_FIELD, MODE_FIELD, VOLT_FIELD)
    )
    pts: List[Dict] = []
    for r in qs:
        lat = r[LAT_FIELD]
        lon = r[LON_FIELD]
        if lat is None or lon is None:
            continue
        pts.append({
            "lat": float(lat),
            "lon": float(lon),
            "ts": r[ts_field],
            "alt": None if r[ALT_FIELD] is None else float(r[ALT_FIELD]),
            "hdg": None if r[HDG_FIELD] is None else float(r[HDG_FIELD]),
            "mode": r.get(MODE_FIELD) or "",
            "volt": None if r[VOLT_FIELD] is None else float(r[VOLT_FIELD]),
        })
    return pts

def _calc_summary(board: Board, points: List[Dict]) -> Dict:
    start = points[0]["ts"] if points else None
    end   = points[-1]["ts"] if points else None
    duration = 0
    if start and end:
        duration = int((end - start).total_seconds())

    from math import radians, sin, cos, atan2, sqrt
    def hav(p1, p2):
        R = 6371000
        dphi = radians(p2["lat"] - p1["lat"])
        dl   = radians(p2["lon"] - p1["lon"])
        phi1 = radians(p1["lat"])
        phi2 = radians(p2["lat"])
        a = sin(dphi/2)**2 + cos(phi1)*cos(phi2)*sin(dl/2)**2
        return 2*R*atan2(sqrt(a), sqrt(1-a))

    dist = 0.0
    for i in range(1, len(points)):
        dist += hav(points[i-1], points[i])

    max_alt = None
    for p in points:
        if p["alt"] is not None:
            max_alt = p["alt"] if max_alt is None else max(max_alt, p["alt"])

    return {
        "board_number": board.boat_number,
        "start_time": start,
        "end_time": end,
        "flight_duration_s": duration,
        "distance_m": dist,
        "max_alt": max_alt,
    }

# ============================ СТАТУС СЕССИИ ====================================

def _session_status(points: List[Dict]) -> Tuple[str, str]:
    if not points:
        return "finished", "Полёт завершён"
    last_ts = points[-1]["ts"]
    age = (datetime.now(timezone.utc) - last_ts).total_seconds()
    if age < LOST_SECS:
        return "active", "Полёт активен"
    if age < FINISH_SECS:
        return "lost", "Связь потеряна"
    return "finished", "Полёт завершён"

# =============================== VIEW: MAP =====================================

@require_GET
def board_session_map(request, board_id: int, sess: str):
    try:
        board = Board.objects.get(pk=board_id)
    except Board.DoesNotExist:
        raise Http404("Board not found")

    points = _fetch_points_for_session(board_id, sess)
    summary = _calc_summary(board, points)
    sessions_all = _sessions_index(board_id, limit=200)

    html = _render_map_html(
        f"Борт #{board.boat_number} — сессия {escape(sess)}",
        summary, points, board, sess, sessions_all
    )
    return HttpResponse(html)

@require_GET
def board_session_data(request, board_id: int, sess: str):
    def _dt_to_epoch(dt):
        if not dt:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())

    def _fmt_dt(dt):
        return dt.isoformat(sep=' ', timespec='seconds') if dt else ""

    points = _fetch_points_for_session(board_id, sess)
    pts_payload = [{
        "lat": p.get("lat"),
        "lon": p.get("lon"),
        "ts": _fmt_dt(p.get("ts")),
        "ts_epoch": _dt_to_epoch(p.get("ts")),
        "alt": p.get("alt"),
        "hdg": p.get("hdg"),
        "mode": p.get("mode") or "",
        "volt": p.get("volt"),
    } for p in points]
    return JsonResponse({"points": pts_payload})

# =============================== РЕНДЕР HTML ===================================

def _render_map_html(title: str, summary: dict, points: list, board, sess: str, sessions_all: list):
    pts_json       = json.dumps(points, ensure_ascii=False, cls=DjangoJSONEncoder)
    sessions_json  = json.dumps(sessions_all, ensure_ascii=False, cls=DjangoJSONEncoder)
    current_sess   = json.dumps(sess, ensure_ascii=False, cls=DjangoJSONEncoder)

    board_id       = board.id
    board_number   = escape(str(board.boat_number or ""))

    start_human    = escape(_fmt_dt(summary.get("start_time")) or "")
    end_human      = escape(_fmt_dt(summary.get("end_time")) or "")
    dist_km_str    = f"{(summary.get('distance_m',0.0)/1000):.2f} км"
    max_alt_str    = f"{int(summary.get('max_alt') or 0)} м" if summary.get("max_alt") is not None else "—"
    duration_str   = _fmt_duration_human(summary.get("flight_duration_s") or 0)

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{escape(title)}</title>
  <link rel="stylesheet" href="{LEAFLET_CSS}">
  <link rel="stylesheet" href="{CLUSTER_CSS}">
  <link rel="stylesheet" href="{CLUSTER_CSS_D}">
  <style>
    html,body,#map {{ height:100%; width:100%; margin:0; padding:0; }}
    .leaflet-container {{ background:#e6e6e6 !important; }}

    #infocard {{
      position: fixed; right: 12px; top: 12px;
      background: #fff; border-radius: 8px; padding: 14px 16px;
      box-shadow: 0 6px 20px rgba(0,0,0,.18);
      font: 14px/1.4 -apple-system,Segoe UI,Roboto,Arial,sans-serif;
      width: 320px; z-index: 800;
    }}
    #infocard .row {{ display:flex; gap:8px; align-items:center; margin:4px 0; }}
    #infocard .label {{ color:#666; min-width:84px; }}
    #infocard .muted {{ color:#888; }}

    .status-dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; vertical-align:middle; }}

    .btn {{
      appearance:none; border:1px solid #ddd; background:#f8f8f8; border-radius:6px;
      padding:6px 10px; cursor:pointer;
    }}
    .btn:hover {{ background:#f0f0f0; }}

    /* фильтр — в правом верхнем углу, слева от инфокарточки */
    #leftpanel {{
      position: fixed;
      right: calc(12px + var(--infocard-w, 320px) + 12px);
      top: 12px;
      z-index: 800;
      background:#fff; border-radius:8px; padding:12px 14px;
      box-shadow:0 6px 20px rgba(0,0,0,.18); width:300px;
      font: 14px/1.4 -apple-system,Segoe UI,Roboto,Arial,sans-serif;
    }}

    .toast {{
      position: fixed; top: 12px; left: 50%; transform: translateX(-50%);
      background: #333; color:#fff; padding:10px 14px; border-radius:8px; z-index: 9999;
      opacity: 0; pointer-events:none; transition: opacity .25s ease;
    }}
    .toast.show {{ opacity: 0.98; }}
  </style>
</head>
<body>
  <div id="map"></div>

  <div id="leftpanel">
    <div style="font-weight:600; margin-bottom:8px;">Фильтр</div>

    <div style="margin-bottom:8px;">
      <div style="color:#666; font-size:13px; margin-bottom:4px;">Борт</div>
      <select id="boardSelect" style="width:100%; padding:6px 8px; border-radius:6px; border:1px solid #ddd;"></select>
    </div>

    <div>
      <div style="color:#666; font-size:13px; margin-bottom:4px;">Сессия</div>
      <select id="leftSessSelect" style="width:100%; padding:6px 8px; border-radius:6px; border:1px solid #ddd;"></select>
    </div>
  </div>

  <div id="infocard">
    <div class="row"><div class="label">Борт:</div><div><b>#{board_number}</b></div></div>
    <div class="row"><div class="label">Статус:</div><div><span class="status-dot"></span><span id="statusText">...</span></div></div>
    <div class="row"><div class="label">Старт:</div><div><a href="javascript:void(0)" id="gotoStart">перейти</a><span class="muted">  {start_human}</span></div></div>
    <div class="row"><div class="label">Финиш:</div><div><a href="javascript:void(0)" id="gotoEnd">перейти</a><span class="muted">  {end_human}</span></div></div>
    <div class="row"><div class="label">Длительность:</div><div id="dur">{escape(duration_str)}</div></div>
    <div class="row"><div class="label">Дистанция:</div><div id="dist">{escape(dist_km_str)}</div></div>
    <div class="row"><div class="label">Макс. высота:</div><div id="maxalt">{escape(max_alt_str)}</div></div>

    <div class="row" style="margin-top:6px;"><div class="label">Следовать:</div>
      <label style="display:inline-flex;align-items:center;gap:8px;cursor:pointer;">
        <input id="followToggle" type="checkbox" checked/> <span class="muted">камера следует за точкой</span>
      </label>
    </div>

    <div class="row" style="margin-top:8px; gap:10px;">
      <button class="btn" id="btnGpx">Экспорт GPX</button>
      <button class="btn" id="btnKml">Экспорт KML</button>
    </div>

    <div class="row" style="margin-top:10px;"><div class="label">Сессии:</div></div>
    <select id="sessSelect" style="width:100%; padding:6px 8px; border-radius:6px; border:1px solid #ddd;"></select>
  </div>

  <div id="toast" class="toast"></div>

  <script src="{LEAFLET_JS}"></script>
  <script src="{CLUSTER_JS}"></script>

  <script>
  // ===== данные от сервера =====
  const pts         = {json.dumps(points, ensure_ascii=False, cls=DjangoJSONEncoder)};
  const sessions    = {json.dumps(sessions_all, ensure_ascii=False, cls=DjangoJSONEncoder)};
  const currentSess = {json.dumps(sess, ensure_ascii=False, cls=DjangoJSONEncoder)};
  const boardId     = {board_id};
  // =============================

  const LOST_SECS   = {LOST_SECS};
  const FINISH_SECS = {FINISH_SECS};
  const CLOCK_SKEW  = 2;

  // подгон фильтра слева от карточки (берем реальную ширину)
  // подгон фильтра слева от карточки (берем реальную ширину)
  (function syncPanels(){{             
    const inf = document.getElementById('infocard');
    const set = ()=>document.documentElement.style.setProperty('--infocard-w', (inf?.offsetWidth || 320)+'px');
    set(); window.addEventListener('resize', set);
  }})(); 

  // Левая панель: борта и их сессии
  const boardSel = document.getElementById('boardSelect');
  const leftSessSel = document.getElementById('leftSessSelect');

  async function loadBoardsAndInit() {{
    try {{
      const r = await fetch('/api/v1/track/boards/');
      const data = await r.json();
      const items = Array.isArray(data.boards) ? data.boards : [];
      boardSel.innerHTML = items.map(b => (
        `<option value="${{b.id}}" ${{b.id===boardId?'selected':''}}>${{b.label}}</option>`
      )).join('');
      await loadSessionsFor(boardId, currentSess);
    }} catch (_) {{}}
  }}

  async function loadSessionsFor(bId, preselectSess = null) {{
    try {{
      const r = await fetch(`/api/v1/track/sessions/board/${{encodeURIComponent(bId)}}/`);
      const data = await r.json();
      const items = Array.isArray(data.sessions) ? data.sessions : [];
      leftSessSel.innerHTML = items.map(s => (
        `<option value="${{s.sess}}" ${{s.sess===preselectSess?'selected':''}}>${{s.label}}</option>`
      )).join('');
    }} catch (_) {{}}
  }}

  boardSel.addEventListener('change', async (e) => {{
    const newBoard = parseInt(e.target.value, 10);
    if (!Number.isFinite(newBoard)) return;
    await loadSessionsFor(newBoard, null);
    const firstSess = leftSessSel.value;
    if (firstSess) {{
      window.location.href = `/api/v1/track/board/${{newBoard}}/session/${{encodeURIComponent(firstSess)}}/`;
    }}
  }});

  leftSessSel.addEventListener('change', (e) => {{
    const s = e.target.value;
    const b = parseInt(boardSel.value, 10) || boardId;
    if (s) window.location.href = `/api/v1/track/board/${{b}}/session/${{encodeURIComponent(s)}}/`;
  }});

  loadBoardsAndInit();

  const map = L.map('map', {{
    attributionControl:false, worldCopyJump:false, inertia:false,
    maxBounds:[[ -85,-180 ],[ 85, 180 ]], maxBoundsViscosity:1.0
  }});
  map.setMinZoom(3);

  const osm  = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{maxZoom:19, noWrap:true, continuousWorld:false}});
  const esri = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{maxZoom:19, noWrap:true, continuousWorld:false}});
  map.createPane('labels'); map.getPane('labels').style.zIndex=650; map.getPane('labels').style.pointerEvents='none';
  const esriLabels = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{{z}}/{{y}}/{{x}}', {{pane:'labels', opacity:0.9, noWrap:true, continuousWorld:false}});

  const baseLayers = {{ "Схема (OSM)": osm, "Спутник (ESRI)": esri }};
  const overlays   = {{ "Подписи": esriLabels }};
  osm.addTo(map); esriLabels.addTo(map);
  L.control.layers(baseLayers, overlays, {{collapsed:true, position:'topleft'}}).addTo(map);

  const fmtDur = (s)=>{{ s=Math.max(0,Math.floor(s||0)); const h=s/3600|0, m=(s%3600)/60|0, ss=s%60|0; return h?`${{h}}ч ${{m}}м`: m?`${{m}}м ${{ss}}с`:`${{ss}}с`; }};
  const parseTs=(s)=> s ? new Date(s.replace(' ','T')) : null;
  const toRad = (x)=> x*Math.PI/180;
  const normLon = (lon)=>{{ let x=lon; if(x==null||Number.isNaN(x)) return x; while(x>180)x-=360; while(x<-180)x+=360; return x; }};
  const LL = (lat,lon)=> L.latLng(lat, normLon(lon));
  const havNorm=(a,b)=>{{ const R=6371000; const dφ=toRad(b.lat-a.lat); let dλ=normLon(b.lon-a.lon); const φ1=toRad(a.lat),φ2=toRad(b.lat); const t=Math.sin(dφ/2)**2 + Math.cos(φ1)*Math.cos(φ2)*Math.sin(toRad(dλ)/2)**2; return 2*Math.atan2(Math.sqrt(t),Math.sqrt(1-t))*R; }};
  const downsample=(arr,maxPts=2000)=> arr.length<=maxPts ? arr : arr.filter((_,i)=> i%Math.ceil(arr.length/maxPts)===0).concat([arr[arr.length-1]]);

  const planeIcon=(deg=0)=>L.divIcon({{
    className:'plane',
    html:`<div style="transform:rotate(${{deg}}deg);transform-origin:center;">
            <svg width="28" height="28" viewBox="0 0 24 24">
              <path d="M2 16l20-8-8 20-2-8-8-4z" fill="#E74C3C"/>
            </svg>
          </div>`,
    iconSize:[28,28], iconAnchor:[14,14]
  }});
  const svgPin=(fill,hole=4,w=20,h=28,r=hole)=>L.divIcon({{className:'pin', html:
    `<svg width="${{w}}" height="${{h}}" viewBox="0 0 20 28">
       <path d="M10 0C4.5 0 0 4.3 0 9.6 0 15.6 10 28 10 28s10-12.4 10-18.4C20 4.3 15.5 0 10 0z" fill="${{fill}}"/>
       <circle cx="10" cy="10" r="${{r}}" fill="#fff"/>
     </svg>`, iconSize:[w,h], iconAnchor:[w/2,h] }});
  const iconStart=svgPin("#2ECC71",4,20,28,4);
  const iconEnd  =svgPin("#E74C3C",4,20,28,4);
  const iconMid  =svgPin("#2980B9",3,14,20,3);

  const STATUS={{
    active:   {{ text:"Полёт активен",  color:"#27ae60" }},
    lost:     {{ text:"Связь потеряна", color:"#f39c12" }},
    finished: {{ text:"Полёт завершён", color:"#e74c3c" }}
  }};
  let lastStatus=null;
  let enriched=[], polyline=null, startMarker=null, endMarker=null, cluster=null;
  let firstTs=null, didFitOnce=false, followMode=true;

  const followEl = document.getElementById('followToggle');
  if (followEl) {{ followEl.checked=true; followEl.addEventListener('change', e=>followMode=!!e.target.checked); }}

  const tip = (p)=> `
    <div><b>${{p.ts || ''}}</b></div>
    <div>Высота: ${{p.alt ?? '—'}} м</div>
    <div>Курс: ${{p.hdg ?? '—'}}°</div>
    <div>Режим: ${{p.mode || '—'}}</div>
    <div>Время полёта: ${{p.elapsed!=null ? fmtDur(p.elapsed) : '—'}}</div>
    <div>Дистанция от старта: ${{(p.distAcc/1000).toFixed(2)}} км</div>
    <div>Батарея: ${{p.volt!=null ? p.volt.toFixed(2)+' В' : '—'}}</div>`;

  function bearingAB(a, b) {{
    const φ1 = toRad(a.lat), φ2 = toRad(b.lat);
    const Δλ = toRad(normLon(b.lon - a.lon));
    const y = Math.sin(Δλ) * Math.cos(φ2);
    const x = Math.cos(φ1)*Math.sin(φ2) - Math.sin(φ1)*Math.cos(φ2)*Math.cos(Δλ);
    let θ = Math.atan2(y, x) * 180 / Math.PI;
    if (θ < 0) θ += 360;
    return θ;
  }}
  function lastHeading(){{
    if(!enriched.length) return 0;
    const last=enriched[enriched.length-1];
    if(last.hdg!=null) return last.hdg;
    const prev = enriched.length>1 ? enriched[enriched.length-2] : null;
    return prev ? bearingAB(prev,last) : 0;
  }}
  function refreshEndIcon(statusCode){{
    if(!endMarker || !enriched.length) return;
    if(statusCode==="finished") endMarker.setIcon(iconEnd);
    else endMarker.setIcon(planeIcon(lastHeading()));
  }}

  function computeStatus(){{
    if(!enriched.length) return "finished";
    const last=enriched[enriched.length-1];
    const now=Date.now()/1000;
    const t  =(last.ts_epoch!=null)?last.ts_epoch:(parseTs(last.ts)?.getTime()/1000|0);
    if(!t) return "finished";
    const age=Math.max(0, now - t - CLOCK_SKEW);
    if(age>=FINISH_SECS) return "finished";
    if(age>=LOST_SECS)   return "lost";
    return "active";
  }}
  function applyStatusUI(code){{
    const s=STATUS[code]||STATUS.finished;
    const dot=document.querySelector(".status-dot"); if(dot) dot.style.background=s.color;
    const st=document.getElementById("statusText"); if(st) st.textContent=s.text;
    refreshEndIcon(code);
  }}
  function showBanner(msg,color="#333"){{ const t=document.getElementById("toast"); if(!t)return; t.textContent=msg; t.style.background=color; t.classList.add("show"); setTimeout(()=>t.classList.remove("show"),2500); }}

  function rebuild(rawPts){{
    if(polyline) map.removeLayer(polyline);
    if(startMarker) map.removeLayer(startMarker);
    if(endMarker) map.removeLayer(endMarker);
    if(cluster) map.removeLayer(cluster);

    if(!rawPts.length){{ applyStatusUI("finished"); return; }}

    firstTs = rawPts[0].ts_epoch ?? (parseTs(rawPts[0].ts)?.getTime()/1000|0);
    let distAcc=0;

    enriched = rawPts.map((p,i)=>{{
      const lonN=normLon(p.lon);
      const prev=i>0?rawPts[i-1]:null;
      if(i>0) distAcc += havNorm({{lat:prev.lat, lon:prev.lon}}, {{lat:p.lat, lon:p.lon}});
      const ts_epoch=(p.ts_epoch!=null)?p.ts_epoch:(parseTs(p.ts)?.getTime()/1000|0);
      const elapsed=(ts_epoch&&firstTs)?(ts_epoch-firstTs):null;
      return {{ ...p, ts_epoch, lon:lonN, ll:LL(p.lat,lonN), distAcc, elapsed }};
    }});

    polyline = L.polyline(enriched.map(p=>p.ll), {{color:"{POLYLINE_COLOR}", weight:{POLYLINE_WEIGHT}}}).addTo(map);

    cluster = L.markerClusterGroup({{disableClusteringAtZoom:16}});
    startMarker = L.marker(enriched[0].ll, {{icon:iconStart}}).bindTooltip("Старт");
    cluster.addLayer(startMarker);

    downsample(enriched.slice(1,-1), 2000).forEach(p=>{{
      cluster.addLayer(L.marker(p.ll, {{icon:iconMid}}).bindTooltip(tip(p)));
    }});

    const last=enriched[enriched.length-1];
    endMarker = L.marker(last.ll, {{icon:planeIcon(lastHeading())}}).bindTooltip(tip(last));
    cluster.addLayer(endMarker);

    cluster.addTo(map);

    if(!didFitOnce) {{
      if(enriched.length<5) map.setView(last.ll,16);
      else map.fitBounds(polyline.getBounds(), {{padding:[20,20], maxZoom:17}});
      didFitOnce=true;
    }} else if(followMode) {{
      map.panTo(last.ll, {{animate:true}});
    }}

    document.getElementById('dist') && (document.getElementById('dist').textContent = (last.distAcc/1000).toFixed(2) + " км");
    if(firstTs && last.ts_epoch) {{
      document.getElementById('dur') && (document.getElementById('dur').textContent = fmtDur(last.ts_epoch - firstTs));
    }}

    const st = computeStatus(); applyStatusUI(st); lastStatus = st;
  }}

  rebuild(pts);

  document.getElementById('gotoStart').onclick = ()=>{{ if(!enriched.length) return; map.setView(enriched[0].ll, Math.max(map.getZoom(),16)); startMarker && startMarker.openTooltip(); }};
  document.getElementById('gotoEnd').onclick   = ()=>{{ if(!enriched.length) return; const e=enriched[enriched.length-1]; map.setView(e.ll, Math.max(map.getZoom(),16)); endMarker && endMarker.openTooltip(); }};

  const sel = document.getElementById('sessSelect');
  sel.innerHTML = sessions.map(o => `<option value="${{o.sess}}" ${{o.sess===currentSess?'selected':''}}>${{o.label}}</option>`).join('');
  sel.onchange = (e)=> {{ window.location.href = `/api/v1/track/board/${{boardId}}/session/${{encodeURIComponent(e.target.value)}}/`; }};

  document.getElementById('btnGpx').onclick = ()=>{{ window.open(`/api/v1/track/export/gpx/board/${{boardId}}/session/${{encodeURIComponent(currentSess)}}/`,'_blank'); }};
  document.getElementById('btnKml').onclick = ()=>{{ window.open(`/api/v1/track/export/kml/board/${{boardId}}/session/${{encodeURIComponent(currentSess)}}/`,'_blank'); }};

  let animTimer=null;
  function animatePlane(fromLL,toLL,duration=1500){{
    if(!endMarker) return;
    const start=performance.now(); const lat1=fromLL.lat, lon1=fromLL.lng, lat2=toLL.lat, lon2=toLL.lng;
    function step(t){{ const k=Math.min(1,(t-start)/duration); const lat=lat1+(lat2-lat1)*k, lon=lon1+(lon2-lon1)*k; endMarker.setLatLng([lat,lon]); if(k<1) animTimer=requestAnimationFrame(step); }}
    if(animTimer) cancelAnimationFrame(animTimer);
    animTimer=requestAnimationFrame(step);
  }}

  async function poll(){{
    try {{
      const r = await fetch(`/api/v1/track/data/board/${{boardId}}/session/${{encodeURIComponent(currentSess)}}/`);
      if(!r.ok) return;
      const data = await r.json();
      if(!Array.isArray(data.points)) return;

      const had = pts.length;
      const prevLastLL = enriched.length ? enriched[enriched.length-1].ll : null;

      if (data.points.length !== had) {{
        pts.splice(0, pts.length, ...data.points);
        rebuild(pts);
        if(prevLastLL && enriched.length) {{
          animatePlane(prevLastLL, enriched[enriched.length-1].ll);
        }}
        refreshEndIcon(computeStatus());
        if(followMode && enriched.length) map.panTo(enriched[enriched.length-1].ll, {{animate:true}});
      }}

      const st = computeStatus();
      if (st !== lastStatus) {{
        showBanner(
          st === "active"  ? "Борт вновь на связи"
        : st === "lost"    ? "Связь потеряна — телеметрия не поступает"
                          : "Полёт завершён",
          ({{"active":"#27ae60","lost":"#f39c12","finished":"#e74c3c"}})[st]
        );
        applyStatusUI(st);
        lastStatus = st;
      }}
    }} catch(e) {{ /* ignore */ }}
  }}
  setInterval(poll, 5000);
  </script>
</body>
</html>"""

# ============================ ЭКСПОРТЫ GPX/KML =================================

@require_GET
def export_gpx(request, board_id: int, sess: str):
    pts = list(_iter_points(board_id, sess))
    if not pts:
        raise Http404("Нет точек для экспорта")

    from xml.sax.saxutils import escape as xesc
    def iso(dt): return dt.isoformat().replace("+00:00", "Z") if dt else ""

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>\n')
    parts.append('<gpx version="1.1" creator="sova" xmlns="http://www.topografix.com/GPX/1/1">\n')
    parts.append(f'  <trk><name>{xesc(f"board-{board_id}-{sess}")}</name><trkseg>\n')
    for p in pts:
        parts.append('    <trkpt lat="{:.7f}" lon="{:.7f}">'.format(p["lat"], p["lon"]))
        if p["alt"] is not None:
            parts.append("<ele>{:.2f}</ele>".format(p["alt"]))
        if p["ts"]:
            parts.append("<time>{}</time>".format(iso(p["ts"])))
        parts.append("</trkpt>\n")
    parts.append("  </trkseg></trk>\n</gpx>\n")

    data = "".join(parts)
    filename = f"board-{board_id}-{slugify(sess)}.gpx"
    resp = HttpResponse(data, content_type="application/gpx+xml; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{smart_str(filename)}"'
    return resp

@require_GET
def export_kml(request, board_id: int, sess: str):
    pts = list(_iter_points(board_id, sess))
    if not pts:
        raise Http404("Нет точек для экспорта")

    from xml.sax.saxutils import escape as xesc
    coords = " ".join("{:.7f},{:.7f},{}".format(p["lon"], p["lat"], p["alt"] or 0) for p in pts)

    kml = f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{xesc(f"board-{board_id}-{sess}")}</name>
    <Placemark>
      <name>Маршрут</name>
      <Style><LineStyle><color>ffad448e</color><width>4</width></LineStyle></Style>
      <LineString><tessellate>1</tessellate><coordinates>{coords}</coordinates></LineString>
    </Placemark>
  </Document>
</kml>'''
    filename = f"board-{board_id}-{slugify(sess)}.kml"
    resp = HttpResponse(kml, content_type="application/vnd.google-earth.kml+xml; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{smart_str(filename)}"'
    return resp
