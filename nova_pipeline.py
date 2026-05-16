"""
nova_pipeline.py
================
NOVA — Network Optimisation & Venue Analysis
Native Jejak pipeline module.

Given a complaint point (lat/lng) + search radius:
  1. Fetch tower/site locations from Athena
  2. Run Delaunay triangulation → Nominal Points (triangle centroids)
  3. Filter NPs inside the user's complaint radius
  4. Score each NP with signal quality from coverage_holes_clustered
  5. Rank and return top-K candidates (A, B, C …) as GeoJSON

Architecture:
  - Same Athena / DB_CONFIG patterns as atom_pipeline.py
  - Uses scipy.spatial.Delaunay + shapely (already in requirements)
  - PostGIS NOT required — all geometry in Python
"""

import os
import math
from typing import Optional
from datetime import datetime

import numpy as np
import pandas as pd
import awswrangler as wr
import boto3
import psycopg2
from scipy.spatial import Delaunay
from shapely.geometry import Point, Polygon, mapping

# ── Config — mirrors atom_pipeline.py exactly ────────────────────────────────
ATHENA_DATABASE = os.getenv("ATHENA_DATABASE",  "jejak-mappro-demo")
S3_STAGING_DIR  = os.getenv("S3_STAGING_DIR",   "s3://jejak-mappro-demo/3W-data/athena-query-results/")
AWS_REGION      = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")

DB_CONFIG = {
    'host':     os.getenv('DB_HOST',     'localhost'),
    'database': os.getenv('DB_NAME',     'vibe_db'),
    'user':     os.getenv('DB_USER',     'postgres'),
    'password': os.getenv('DB_PASSWORD', '1234'),
    'port':     os.getenv('DB_PORT',     '5432'),
}

# NP buffer radius for signal scoring (degrees ≈ 0.001° ~ 111 m)
NP_SIGNAL_RADIUS_DEG = 0.001

# Candidate label sequence
CANDIDATE_LABELS = list('ABCDEFGHIJKLMNOPQRSTUVWXYZ')

# Colour ramp for candidates (best → worst)
CANDIDATE_COLORS = [
    '#16a34a',  # A — green
    '#2563eb',  # B — blue
    '#9333ea',  # C — purple
    '#ea580c',  # D — orange
    '#dc2626',  # E — red
    '#0891b2',  # F — cyan
    '#ca8a04',  # G — yellow
    '#db2777',  # H — pink
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitise(obj):
    """Recursively convert numpy scalars → native Python so jsonify never chokes."""
    if isinstance(obj, dict):
        return {k: _sanitise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitise(v) for v in obj]
    if hasattr(obj, 'item'):
        return obj.item()
    return obj


def _aws_session():
    return boto3.Session(region_name=AWS_REGION)


def _haversine_m(lat1, lng1, lat2, lng2) -> float:
    """Distance between two points in metres (WGS-84)."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi  = math.radians(lat2 - lat1)
    dlam  = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _metres_to_deg(metres: float, lat: float) -> float:
    """Convert a metres radius to approximate degrees (for lat/lng filtering)."""
    lat_deg = metres / 111_320
    lng_deg = metres / (111_320 * math.cos(math.radians(lat)))
    return max(lat_deg, lng_deg)


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_nova_pipeline(
    complaint_lat: float,
    complaint_lng: float,
    radius_m: float = 500,
    top_k: int = 3,
    initiated_by: str = 'system',
) -> dict:
    """
    Full NOVA pipeline.

    Parameters
    ----------
    complaint_lat / complaint_lng : float
        Centre of the search area (complaint location).
    radius_m : float
        Search radius in metres (user-adjustable, default 500 m).
    top_k : int
        Maximum number of candidate NPs to return (default 3 = A/B/C).
    initiated_by : str
        Username from session for audit trail.

    Returns
    -------
    dict with keys:
        candidates        – list of ranked NP dicts (label, score, lat, lng, …)
        geojson           – FeatureCollection: candidate markers + search circle
        delaunay_geojson  – FeatureCollection: Delaunay triangle polygons
        run_id            – int | None (persisted in nova_runs)
        meta              – radius_m, top_k, n_sites, n_nps, n_candidates
    """

    print(f"[NOVA] Start — complaint=({complaint_lat},{complaint_lng}), "
          f"radius={radius_m}m, top_k={top_k}, by={initiated_by}")

    # ── 1. Fetch site coordinates from Athena ─────────────────────────────────
    deg_pad = _metres_to_deg(radius_m * 5, complaint_lat)   # wide fetch, filter later
    lat_min = complaint_lat - deg_pad
    lat_max = complaint_lat + deg_pad
    lng_min = complaint_lng - deg_pad
    lng_max = complaint_lng + deg_pad

    site_sql = f"""
        SELECT CAST(latitude  AS DOUBLE) AS lat,
               CAST(longitude AS DOUBLE) AS lng,
               CAST(site_id   AS VARCHAR) AS site_id
        FROM site_coordinates
        WHERE latitude  IS NOT NULL
          AND longitude IS NOT NULL
          AND CAST(latitude  AS DOUBLE) BETWEEN {lat_min} AND {lat_max}
          AND CAST(longitude AS DOUBLE) BETWEEN {lng_min} AND {lng_max}
    """
    try:
        session  = _aws_session()
        sites_df = wr.athena.read_sql_query(
            site_sql,
            database=ATHENA_DATABASE,
            s3_output=S3_STAGING_DIR,
            boto3_session=session,
        )
    except Exception as e:
        return {'error': f'Athena site_coordinates query failed: {e}'}

    sites_df = sites_df.dropna(subset=['lat', 'lng'])
    n_sites  = int(len(sites_df))
    print(f"[NOVA] {n_sites} sites fetched from Athena")

    if n_sites < 3:
        return {'error': f'Need at least 3 tower sites in area for Delaunay triangulation. Found {n_sites}.'}

    # ── 2. Delaunay triangulation → Nominal Points ────────────────────────────
    coords    = sites_df[['lng', 'lat']].values.astype(float)
    tri       = Delaunay(coords)

    nps = []
    tri_features = []

    for simplex in tri.simplices:
        pts = coords[simplex]          # 3×2 array: [[lng,lat], …]
        centroid_lng = float(pts[:, 0].mean())
        centroid_lat = float(pts[:, 1].mean())
        np_id = len(nps) + 1
        nps.append({'id': np_id, 'lat': centroid_lat, 'lng': centroid_lng})

        # Keep triangle for the Delaunay layer
        ring = [(float(p[0]), float(p[1])) for p in pts] + [(float(pts[0][0]), float(pts[0][1]))]
        tri_features.append({
            'type': 'Feature',
            'geometry': {'type': 'Polygon', 'coordinates': [ring]},
            'properties': {'np_id': np_id},
        })

    print(f"[NOVA] {len(nps)} Nominal Points generated")

    # ── 3. Filter NPs inside the complaint radius ─────────────────────────────
    nps_in_radius = []
    for np_pt in nps:
        dist = _haversine_m(complaint_lat, complaint_lng, np_pt['lat'], np_pt['lng'])
        if dist <= radius_m:
            np_pt['dist_m'] = round(dist, 1)
            nps_in_radius.append(np_pt)

    n_nps = int(len(nps_in_radius))
    print(f"[NOVA] {n_nps} NPs inside {radius_m}m radius")

    if n_nps == 0:
        return {
            'error': f'No Nominal Points found within {radius_m} m. Try increasing the radius.',
            'delaunay_geojson': {'type': 'FeatureCollection', 'features': tri_features},
        }

    # ── 4. Score each NP using signal data from Athena ────────────────────────
    # Fetch bad-signal MR in the bounding box of all in-radius NPs
    np_lats = [p['lat'] for p in nps_in_radius]
    np_lngs = [p['lng'] for p in nps_in_radius]
    sig_lat_min = min(np_lats) - NP_SIGNAL_RADIUS_DEG
    sig_lat_max = max(np_lats) + NP_SIGNAL_RADIUS_DEG
    sig_lng_min = min(np_lngs) - NP_SIGNAL_RADIUS_DEG
    sig_lng_max = max(np_lngs) + NP_SIGNAL_RADIUS_DEG

    sig_sql = f"""
        SELECT CAST(latitude        AS DOUBLE) AS lat,
               CAST(longitude       AS DOUBLE) AS lng,
               CAST(signal_strength AS DOUBLE) AS rsrp
        FROM coverage_holes_clustered
        WHERE UPPER(TRIM(CAST(data_source AS VARCHAR))) = 'MR'
          AND latitude  IS NOT NULL
          AND longitude IS NOT NULL
          AND CAST(latitude  AS DOUBLE) BETWEEN {sig_lat_min} AND {sig_lat_max}
          AND CAST(longitude AS DOUBLE) BETWEEN {sig_lng_min} AND {sig_lng_max}
        LIMIT 20000
    """
    try:
        sig_df = wr.athena.read_sql_query(
            sig_sql,
            database=ATHENA_DATABASE,
            s3_output=S3_STAGING_DIR,
            boto3_session=session,
        )
        sig_df = sig_df.dropna(subset=['lat', 'lng'])
    except Exception as e:
        print(f"[NOVA] Signal query failed (non-fatal): {e}")
        sig_df = pd.DataFrame(columns=['lat', 'lng', 'rsrp'])

    # Score each NP: sum of signal weight points within NP_SIGNAL_RADIUS_DEG
    # Higher sum  = worse coverage = higher urgency = better candidate
    def _signal_score(np_lat, np_lng):
        if sig_df.empty:
            return 0, 0, None
        dlat = (sig_df['lat'] - np_lat).abs()
        dlng = (sig_df['lng'] - np_lng).abs()
        mask = (dlat <= NP_SIGNAL_RADIUS_DEG) & (dlng <= NP_SIGNAL_RADIUS_DEG)
        pts  = sig_df[mask]
        if pts.empty:
            return 0, 0, None
        count    = int(len(pts))
        avg_rsrp = float(pts['rsrp'].mean()) if pts['rsrp'].notna().any() else None

        # Weighted score: each sample contributes 0–4 by RSRP band (NOVA formula)
        def _weight(r):
            if pd.isna(r): return 0
            if r <= -126: return 4
            if r <= -120: return 3
            if r <= -118: return 2
            if r <= -110: return 1
            return 0
        weight_sum = int(pts['rsrp'].apply(_weight).sum())
        return count, weight_sum, avg_rsrp

    for np_pt in nps_in_radius:
        cnt, wscore, avg_rsrp     = _signal_score(np_pt['lat'], np_pt['lng'])
        np_pt['signal_count']     = cnt
        np_pt['signal_weight_sum'] = wscore
        np_pt['avg_rsrp']         = round(avg_rsrp, 1) if avg_rsrp is not None else None

    # ── 5. Rank: primary = signal_weight_sum desc, secondary = dist_m asc ─────
    nps_in_radius.sort(
        key=lambda p: (-p['signal_weight_sum'], p['dist_m'])
    )

    # Assign labels A, B, C …
    candidates = []
    for i, np_pt in enumerate(nps_in_radius[:top_k]):
        label = CANDIDATE_LABELS[i] if i < len(CANDIDATE_LABELS) else str(i + 1)
        color = CANDIDATE_COLORS[i] if i < len(CANDIDATE_COLORS) else '#6b7280'
        candidates.append({
            'rank':              i + 1,
            'label':             label,
            'color':             color,
            'np_id':             np_pt['id'],
            'lat':               round(np_pt['lat'], 6),
            'lng':               round(np_pt['lng'], 6),
            'dist_m':            np_pt['dist_m'],
            'signal_count':      np_pt['signal_count'],
            'signal_weight_sum': np_pt['signal_weight_sum'],
            'avg_rsrp':          np_pt['avg_rsrp'],
        })

    print(f"[NOVA] {len(candidates)} candidates ranked")

    # ── 6. Build GeoJSON ──────────────────────────────────────────────────────
    features = []

    # Complaint point
    features.append({
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': [complaint_lng, complaint_lat]},
        'properties': {'type': 'complaint', 'color': '#ef4444'},
    })

    # Search circle (approximated as 64-point polygon)
    deg_lat = radius_m / 111_320
    deg_lng = radius_m / (111_320 * math.cos(math.radians(complaint_lat)))
    circle_pts = [
        [complaint_lng + deg_lng * math.cos(math.radians(a)),
         complaint_lat + deg_lat * math.sin(math.radians(a))]
        for a in range(0, 361, 6)
    ]
    features.append({
        'type': 'Feature',
        'geometry': {'type': 'Polygon', 'coordinates': [circle_pts]},
        'properties': {'type': 'search_circle', 'radius_m': radius_m},
    })

    # Candidate NP markers
    for c in candidates:
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [c['lng'], c['lat']]},
            'properties': {
                'type':              'candidate',
                'label':             c['label'],
                'rank':              c['rank'],
                'color':             c['color'],
                'dist_m':            c['dist_m'],
                'signal_count':      c['signal_count'],
                'signal_weight_sum': c['signal_weight_sum'],
                'avg_rsrp':          c['avg_rsrp'],
            },
        })

    # ── 7. Persist run ────────────────────────────────────────────────────────
    run_id = _save_run(
        complaint_lat=complaint_lat,
        complaint_lng=complaint_lng,
        radius_m=radius_m,
        top_k=top_k,
        n_sites=n_sites,
        n_nps=n_nps,
        n_candidates=len(candidates),
        initiated_by=initiated_by,
        candidates=candidates,
    )

    return _sanitise({
        'run_id':     run_id,
        'candidates': candidates,
        'geojson': {
            'type':     'FeatureCollection',
            'features': features,
        },
        'delaunay_geojson': {
            'type':     'FeatureCollection',
            'features': tri_features,
        },
        'meta': {
            'complaint_lat': complaint_lat,
            'complaint_lng': complaint_lng,
            'radius_m':      radius_m,
            'top_k':         top_k,
            'n_sites':       n_sites,
            'n_nps':         n_nps,
            'n_candidates':  len(candidates),
        },
    })


# ── Persistence ───────────────────────────────────────────────────────────────

def _save_run(complaint_lat, complaint_lng, radius_m, top_k,
              n_sites, n_nps, n_candidates, initiated_by,
              candidates: list) -> Optional[int]:
    try:
        conn   = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO nova_runs
                (complaint_lat, complaint_lng, radius_m, top_k,
                 n_sites, n_nps, n_candidates, initiated_by, ran_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (complaint_lat, complaint_lng, radius_m, top_k,
             n_sites, n_nps, n_candidates, initiated_by, datetime.now()),
        )
        run_id = cursor.fetchone()[0]

        # Persist each candidate
        for c in candidates:
            cursor.execute(
                """
                INSERT INTO nova_candidates
                    (run_id, label, rank, lat, lng, dist_m,
                     signal_count, signal_weight_sum, avg_rsrp, color)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (run_id, c['label'], c['rank'], c['lat'], c['lng'],
                 c['dist_m'], c['signal_count'], c['signal_weight_sum'],
                 c['avg_rsrp'], c['color']),
            )

        conn.commit()
        cursor.close()
        conn.close()
        print(f"[NOVA] Run saved → nova_runs.id={run_id} with {len(candidates)} candidates")
        return run_id
    except Exception as e:
        print(f"[NOVA] Could not save run to DB: {e}")
        return None


def get_nova_run_candidates(run_id: int) -> list:
    """Return saved candidates for a specific NOVA run."""
    try:
        conn   = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT label, rank, lat, lng, dist_m,
                   signal_count, signal_weight_sum, avg_rsrp, color
            FROM nova_candidates
            WHERE run_id = %s
            ORDER BY rank
            """,
            (run_id,),
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [
            {
                'label':             r[0],
                'rank':              r[1],
                'lat':               r[2],
                'lng':               r[3],
                'dist_m':            r[4],
                'signal_count':      r[5],
                'signal_weight_sum': r[6],
                'avg_rsrp':          r[7],
                'color':             r[8],
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[NOVA] get_nova_run_candidates error: {e}")
        return []


def get_nova_recent_runs(limit: int = 10) -> list:
    try:
        conn   = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, complaint_lat, complaint_lng, radius_m, top_k,
                   n_sites, n_nps, n_candidates, initiated_by, ran_at
            FROM nova_runs
            ORDER BY ran_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [
            {
                'id':            r[0],
                'complaint_lat': r[1],
                'complaint_lng': r[2],
                'radius_m':      r[3],
                'top_k':         r[4],
                'n_sites':       r[5],
                'n_nps':         r[6],
                'n_candidates':  r[7],
                'initiated_by':  r[8],
                'ran_at':        r[9].isoformat() if r[9] else None,
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[NOVA] get_nova_recent_runs error: {e}")
        return []
