"""
pave_pipeline.py
================
PAVE — Path & Visibility Evaluation
Native Jejak pipeline module.

Given a candidate tower location (lat/lng):
  1. Filter existing sites within SEARCH_R
  2. Load DEM from S3 (byte-range streaming via GDAL /vsis3/)
  3. Compute viewshed polygon (radial sweep, 360 azimuths)
  4. Batch LOS check — candidate → every nearby site
  5. Extract terrain profile per site for chart visualisation
  6. Persist results and return structured GeoJSON

Architecture:
  - DEM from s3://jejak-mappro-demo/3W-data/DEM/merged_MsiaDEM.tif
  - Uses rasterio + GDAL /vsis3/ (no download — byte-range streaming)
  - Same DB_CONFIG / boto3 patterns as atom_pipeline / nova_pipeline
"""

import math
import os
import time
import threading
from typing import Optional
from datetime import datetime

import numpy as np
import psycopg2
import boto3

try:
    import rasterio
    from rasterio.session import AWSSession as RioAWSSession
    RASTERIO_OK = True
except ImportError:
    RASTERIO_OK = False

from shapely.geometry import Polygon, mapping
from shapely.validation import make_valid

# ── Constants ─────────────────────────────────────────────────────────────────
EARTH_R   = 6_371_000.0
REFRAC_K  = 0.13          # atmospheric refraction (matches QGIS visibility plugin)
OBS_H     = 30.0          # candidate tower height AGL (m)
TGT_H     = 30.0          # existing site antenna height AGL (m)
SEARCH_R  = 10_000.0      # neighbour search radius (m)
DEM_PATH  = '/vsis3/jejak-mappro-demo/3W-data/DEM/merged_MsiaDEM.tif'
N_AZ      = 72            # azimuth rays (72 = 5° steps; fast enough for map polygon)
LOS_N     = 32            # profile sample count (32 is accurate for 10 km)
MAX_SITES = 40            # cap nearby sites

# ── Config ────────────────────────────────────────────────────────────────────
AWS_REGION = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")

DB_CONFIG = {
    'host':     os.getenv('DB_HOST',     'localhost'),
    'database': os.getenv('DB_NAME',     'vibe_db'),
    'user':     os.getenv('DB_USER',     'postgres'),
    'password': os.getenv('DB_PASSWORD', '1234'),
    'port':     os.getenv('DB_PORT',     '5432'),
}

# ── DEM window cache ──────────────────────────────────────────────────────────
_DEM_CACHE: dict = {}
_DEM_LOCK         = threading.Lock()
_GRID_DEG         = 0.05


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _hav(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2)
         * math.sin(math.radians((lon2 - lon1) / 2)) ** 2)
    return 2.0 * EARTH_R * math.asin(math.sqrt(max(0.0, a)))


def _hav_vec(lat0: float, lon0: float,
             lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    p0 = math.radians(lat0)
    p  = np.radians(lats)
    a  = (np.sin((p - p0) / 2) ** 2
          + math.cos(p0) * np.cos(p)
          * np.sin(np.radians((lons - lon0) / 2)) ** 2)
    return 2.0 * EARTH_R * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _sanitise(obj):
    if isinstance(obj, dict):
        return {k: _sanitise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitise(v) for v in obj]
    if hasattr(obj, 'item'):
        return obj.item()
    return obj


# ── DEM I/O ───────────────────────────────────────────────────────────────────

def _read_dem(lat: float, lon: float, radius_m: float,
              boto_session) -> tuple:
    if not RASTERIO_OK:
        raise RuntimeError("rasterio is not installed. Run: pip install rasterio")
    pad   = 1.10
    d_lat = radius_m * pad / 111_000.0
    d_lon = radius_m * pad / (111_000.0 * math.cos(math.radians(lat)))

    gdal_opts = dict(
        GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',
        CPL_VSIL_CURL_CACHE_SIZE=128_000_000,
        GDAL_CACHEMAX=256,
        VSI_CACHE='TRUE',
        VSI_CACHE_SIZE=64_000_000,
    )

    with rasterio.Env(RioAWSSession(boto_session), **gdal_opts):
        with rasterio.open(DEM_PATH) as src:
            win  = src.window(lon - d_lon, lat - d_lat,
                              lon + d_lon, lat + d_lat)
            data = src.read(1, window=win,
                            boundless=True, fill_value=0).astype(np.float32)
            tf   = src.window_transform(win)
            nd   = float(src.nodata) if src.nodata is not None else -9999.0

    data[data == nd]   = 0.0
    data[data  < -500] = 0.0
    return data, tf


def get_dem(lat: float, lon: float, radius_m: float, boto_session) -> tuple:
    key = (round(lat / _GRID_DEG) * _GRID_DEG,
           round(lon / _GRID_DEG) * _GRID_DEG,
           radius_m)
    if key in _DEM_CACHE:
        return _DEM_CACHE[key]
    with _DEM_LOCK:
        if key not in _DEM_CACHE:
            _DEM_CACHE[key] = _read_dem(lat, lon, radius_m, boto_session)
    return _DEM_CACHE[key]


# ── Pixel helpers ─────────────────────────────────────────────────────────────

def _px(tf, lon: float, lat: float) -> tuple:
    col, row = ~tf * (lon, lat)
    return int(col), int(row)


def _safe_el(dem: np.ndarray, col: int, row: int) -> float:
    h, w = dem.shape
    return float(dem[max(0, min(row, h - 1)), max(0, min(col, w - 1))])


# ── Viewshed polygon ──────────────────────────────────────────────────────────

def viewshed_polygon(dem: np.ndarray, tf,
                     obs_lat: float, obs_lon: float,
                     obs_hagl: float = OBS_H,
                     radius_m: float = SEARCH_R,
                     n_az: int = N_AZ):
    lat_m = 111_000.0
    lon_m = 111_000.0 * math.cos(math.radians(obs_lat))
    pxl   = (abs(tf.e) * lat_m + abs(tf.a) * lon_m) / 2.0
    n_s   = max(10, min(600, int(radius_m / pxl)))

    oc, or_ = _px(tf, obs_lon, obs_lat)
    h, w    = dem.shape
    obs_el  = _safe_el(dem, oc, or_) + obs_hagl

    steps = np.arange(1, n_s + 1, dtype=np.float64)
    dists = steps * pxl
    crv   = dists ** 2 * (1.0 - REFRAC_K) / (2.0 * EARTH_R)

    az    = np.linspace(0, 2 * math.pi, n_az, endpoint=False)
    sin_a = np.sin(az)
    cos_a = np.cos(az)

    lats = obs_lat + cos_a[:, None] * dists[None, :] / lat_m
    lons = obs_lon + sin_a[:, None] * dists[None, :] / lon_m

    cols  = ((lons - tf.c) / tf.a).astype(np.int32)
    rows  = ((lats - tf.f) / tf.e).astype(np.int32)
    valid = (cols >= 0) & (cols < w) & (rows >= 0) & (rows < h)
    cc    = np.clip(cols, 0, w - 1)
    rc    = np.clip(rows, 0, h - 1)

    terr         = dem[rc, cc].astype(np.float64)
    terr[~valid] = obs_el

    app    = terr + crv[None, :]
    va     = (app - obs_el) / dists[None, :]
    cummax = np.maximum.accumulate(va, axis=1)
    prev   = np.full_like(cummax, -np.inf)
    prev[:, 1:] = cummax[:, :-1]
    vis    = va >= prev

    si   = np.arange(n_s, dtype=np.int32)[None, :]
    last = np.where(vis, si, -1).max(axis=1)
    last = np.maximum(last, 0)

    vlats  = lats[np.arange(n_az), last]
    vlons  = lons[np.arange(n_az), last]
    coords = list(zip(vlons.tolist(), vlats.tolist()))
    coords.append(coords[0])

    try:
        poly = Polygon(coords)
        return make_valid(poly) if not poly.is_valid else poly
    except Exception:
        return None


# ── Batch LOS check ───────────────────────────────────────────────────────────

def los_batch(dem: np.ndarray, tf,
              obs_lat: float, obs_lon: float, obs_hagl: float,
              s_lats: np.ndarray, s_lons: np.ndarray,
              tgt_hagl: float = TGT_H,
              n_samp: int = LOS_N) -> np.ndarray:
    h, w    = dem.shape
    oc, or_ = _px(tf, obs_lon, obs_lat)
    obs_el  = _safe_el(dem, oc, or_) + obs_hagl

    sl = np.asarray(s_lats, dtype=np.float64)
    so = np.asarray(s_lons, dtype=np.float64)

    t  = np.linspace(0.0, 1.0, n_samp + 2)[1:-1]
    il = obs_lat + t[None, :] * (sl[:, None] - obs_lat)
    io = obs_lon + t[None, :] * (so[:, None] - obs_lon)

    cols = ((io - tf.c) / tf.a).astype(np.int32)
    rows = ((il - tf.f) / tf.e).astype(np.int32)
    vld  = (cols >= 0) & (cols < w) & (rows >= 0) & (rows < h)
    cc   = np.clip(cols, 0, w - 1)
    rc   = np.clip(rows, 0, h - 1)

    terr       = dem[rc, cc].astype(np.float64)
    terr[~vld] = 0.0

    tc   = np.clip(((so - tf.c) / tf.a).astype(int), 0, w - 1)
    tr   = np.clip(((sl - tf.f) / tf.e).astype(int), 0, h - 1)
    t_el = dem[tr, tc].astype(np.float64) + tgt_hagl

    los_h   = obs_el + t[None, :] * (t_el[:, None] - obs_el)
    tot_d   = _hav_vec(obs_lat, obs_lon, sl, so)
    d_along = tot_d[:, None] * t[None, :]
    crv     = d_along ** 2 * (1.0 - REFRAC_K) / (2.0 * EARTH_R)

    blocked = ((terr + crv) > los_h).any(axis=1)
    return ~blocked


# ── Terrain profile extractor (for chart visualisation) ──────────────────────

def get_profile_data(dem: np.ndarray, tf,
                     obs_lat: float, obs_lon: float, obs_hagl: float,
                     tgt_lat: float, tgt_lon: float, tgt_hagl: float = TGT_H,
                     n_samp: int = LOS_N) -> dict:
    """
    Return terrain elevation + LOS line sample points for one site pair.
    Used to render the profile chart in the frontend.
    """
    h, w    = dem.shape
    oc, or_ = _px(tf, obs_lon, obs_lat)
    obs_el  = _safe_el(dem, oc, or_) + obs_hagl

    tc_px = int(np.clip(((tgt_lon - tf.c) / tf.a), 0, w - 1))
    tr_px = int(np.clip(((tgt_lat - tf.f) / tf.e), 0, h - 1))
    tgt_ground = float(dem[tr_px, tc_px])
    tgt_el     = tgt_ground + tgt_hagl

    t       = np.linspace(0.0, 1.0, n_samp + 2)   # include endpoints
    il      = obs_lat + t * (tgt_lat - obs_lat)
    io      = obs_lon + t * (tgt_lon - obs_lon)

    cols    = np.clip(((io - tf.c) / tf.a).astype(np.int32), 0, w - 1)
    rows    = np.clip(((il - tf.f) / tf.e).astype(np.int32), 0, h - 1)
    terrain = dem[rows, cols].astype(np.float64)

    total_d = _hav(obs_lat, obs_lon, tgt_lat, tgt_lon)
    dists   = t * total_d
    crv     = dists ** 2 * (1.0 - REFRAC_K) / (2.0 * EARTH_R)

    terrain_corr = terrain + crv
    los_line     = obs_el + t * (tgt_el - obs_el)
    blocked_mask = terrain_corr > los_line

    return {
        'distances_m':        [round(float(d), 1) for d in dists],
        'terrain':            [round(float(v), 1) for v in terrain],
        'terrain_corrected':  [round(float(v), 1) for v in terrain_corr],
        'los_line':           [round(float(v), 1) for v in los_line],
        'blocked_at':         [bool(b) for b in blocked_mask],
        'total_distance_m':   round(total_d),
        'obs_elevation_m':    round(obs_el, 1),
        'tgt_elevation_m':    round(tgt_el, 1),
    }


# ── Main entry point ──────────────────────────────────────────────────────────

def run_pave(candidate_lat: float,
             candidate_lon: float,
             all_sites: list,
             boto_session,
             initiated_by: str = 'system',
             nova_run_id: Optional[int] = None,
             nova_candidate_label: Optional[str] = None,
             fast_mode: bool = True) -> dict:
    """
    fast_mode=True  → skip viewshed polygon + skip terrain profiles in main run.
                       Profiles are fetched on demand via /api/pave/profile.
                       Typical run time: 5-20s.
    fast_mode=False → include viewshed + all profiles (slow, 1-5 min).
    """
    """
    Full PAVE analysis for one candidate tower location.

    Parameters
    ----------
    candidate_lat/lon    : float  WGS-84 decimal degrees
    all_sites            : list of dicts — keys 'site_id', 'lat', 'lng'
    boto_session         : boto3.Session
    initiated_by         : str
    nova_run_id          : optional link back to NOVA run
    nova_candidate_label : optional 'A' / 'B' / 'C'

    Returns
    -------
    dict with keys:
        candidate_lat, candidate_lon,
        viewshed_geojson  (GeoJSON geometry dict | None),
        sites             (list with los, distance, profile_data),
        summary           (total_nearby, los_count, no_los_count, processing_time_s),
        run_id            (int | None)
    """
    t0 = time.time()
    print(f"[PAVE] Start — ({candidate_lat},{candidate_lon}) by={initiated_by}")

    # ── 1. Filter nearby sites ────────────────────────────────────────────────
    nearby = []
    for s in all_sites:
        try:
            slat, slng = float(s.get('lat') or 0), float(s.get('lng') or 0)
        except (TypeError, ValueError):
            continue
        if not slat or not slng:
            continue
        d = _hav(candidate_lat, candidate_lon, slat, slng)
        if d <= SEARCH_R:
            nearby.append({**s, '_dist': d, 'lat': slat, 'lng': slng})

    nearby.sort(key=lambda x: x['_dist'])
    nearby = nearby[:MAX_SITES]
    n_nearby = len(nearby)
    print(f"[PAVE] {n_nearby} sites within {SEARCH_R/1000:.0f} km")

    if n_nearby == 0:
        return {
            'candidate_lat':  candidate_lat,
            'candidate_lon':  candidate_lon,
            'viewshed_geojson': None,
            'sites': [],
            'summary': {
                'total_nearby': 0, 'los_count': 0, 'no_los_count': 0,
                'processing_time_s': round(time.time() - t0, 2),
            },
            'run_id': None,
            'error': f'No existing sites found within {SEARCH_R/1000:.0f} km of this location.',
        }

    # ── 2. Load DEM ───────────────────────────────────────────────────────────
    try:
        dem, tf = get_dem(candidate_lat, candidate_lon, SEARCH_R, boto_session)
    except Exception as e:
        return {'error': f'DEM load failed: {e}', 'run_id': None}

    # ── 3. Viewshed polygon (skipped in fast_mode) ────────────────────────────
    if fast_mode:
        vs_geo = None
        print("[PAVE] fast_mode: viewshed skipped")
    else:
        poly   = viewshed_polygon(dem, tf, candidate_lat, candidate_lon)
        vs_geo = _sanitise(mapping(poly)) if poly else None
        print(f"[PAVE] Viewshed polygon {'computed' if poly else 'failed'}")

    # ── 4. Batch LOS check ────────────────────────────────────────────────────
    sl  = np.array([s['lat'] for s in nearby])
    so  = np.array([s['lng'] for s in nearby])
    los = los_batch(dem, tf, candidate_lat, candidate_lon, OBS_H, sl, so, TGT_H)
    print(f"[PAVE] LOS: {int(los.sum())} clear / {int((~los).sum())} blocked")

    # ── 5. Build site list (profiles lazy in fast_mode) ───────────────────────
    sites_out = []
    for i, s in enumerate(nearby):
        site_entry = {
            'site_id':    s.get('site_id', '—'),
            'lat':        round(s['lat'], 6),
            'lng':        round(s['lng'], 6),
            'los':        bool(los[i]),
            'distance_m': int(round(s['_dist'])),
            'profile':    None,   # fetched on demand via /api/pave/profile
        }
        if not fast_mode:
            site_entry['profile'] = get_profile_data(
                dem, tf,
                candidate_lat, candidate_lon, OBS_H,
                s['lat'], s['lng'], TGT_H,
            )
        sites_out.append(site_entry)

    lc = int(los.sum())
    elapsed = round(time.time() - t0, 2)
    print(f"[PAVE] Done in {elapsed}s")

    # ── 6. Persist ────────────────────────────────────────────────────────────
    run_id = _save_run(
        candidate_lat=candidate_lat,
        candidate_lon=candidate_lon,
        nova_run_id=nova_run_id,
        nova_candidate_label=nova_candidate_label,
        total_nearby=n_nearby,
        los_count=lc,
        no_los_count=n_nearby - lc,
        processing_time_s=elapsed,
        initiated_by=initiated_by,
        sites=sites_out,
    )

    return _sanitise({
        'run_id':             run_id,
        'candidate_lat':      candidate_lat,
        'candidate_lon':      candidate_lon,
        'viewshed_geojson':   vs_geo,
        'sites':              sites_out,
        'summary': {
            'total_nearby':      n_nearby,
            'los_count':         lc,
            'no_los_count':      n_nearby - lc,
            'processing_time_s': elapsed,
        },
    })


# ── Persistence ───────────────────────────────────────────────────────────────

def _save_run(candidate_lat, candidate_lon, nova_run_id, nova_candidate_label,
              total_nearby, los_count, no_los_count, processing_time_s,
              initiated_by, sites) -> Optional[int]:
    try:
        conn   = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO pave_runs
                (candidate_lat, candidate_lon, nova_run_id, nova_candidate_label,
                 total_nearby, los_count, no_los_count, processing_time_s,
                 initiated_by, ran_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (candidate_lat, candidate_lon, nova_run_id, nova_candidate_label,
             total_nearby, los_count, no_los_count, processing_time_s,
             initiated_by, datetime.now()),
        )
        run_id = cursor.fetchone()[0]

        for s in sites:
            cursor.execute(
                """
                INSERT INTO pave_sites
                    (run_id, site_id, lat, lng, los, distance_m)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (run_id, s['site_id'], s['lat'], s['lng'],
                 s['los'], s['distance_m']),
            )

        conn.commit()
        cursor.close()
        conn.close()
        print(f"[PAVE] Run saved → pave_runs.id={run_id}")
        return run_id
    except Exception as e:
        print(f"[PAVE] DB save error: {e}")
        return None


def get_pave_recent_runs(limit: int = 10) -> list:
    try:
        conn   = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, candidate_lat, candidate_lon, nova_run_id,
                   nova_candidate_label, total_nearby, los_count,
                   no_los_count, processing_time_s, initiated_by, ran_at
            FROM pave_runs ORDER BY ran_at DESC LIMIT %s
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [
            {
                'id':                    r[0],
                'candidate_lat':         r[1],
                'candidate_lon':         r[2],
                'nova_run_id':           r[3],
                'nova_candidate_label':  r[4],
                'total_nearby':          r[5],
                'los_count':             r[6],
                'no_los_count':          r[7],
                'processing_time_s':     r[8],
                'initiated_by':          r[9],
                'ran_at':                r[10].isoformat() if r[10] else None,
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[PAVE] get_pave_recent_runs error: {e}")
        return []
