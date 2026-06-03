"""
nova_pipeline.py
================
NOVA — Network Optimisation & Venue Analysis
Native Jejak pipeline module.

Given an ATOM cluster centroid (NP-id + lat/lng) + search radius:
  1. Fetch tower/site locations from Athena
  2. Detect geometry type (City: 2-D cluster  vs  Highway: near-linear)
  3. Generate two candidate pools:
       a. Triangulation NP  — circumcenter of Delaunay triangles
          (equidistant from 3 real sites → structurally correct spacing)
       b. Centroid NP       — triangle centroid (fallback for highway/sparse)
  4. Apply selection rule:
       IF triangulation NP exists inside radius  → use it (preferred)
       ELSE                                      → use centroid NP
  5. Score each candidate with MR signal data from Athena
  6. Rank top-K, mark selected/rejected with reasons
  7. Persist ALL candidates (selected + rejected) to nova_candidates table
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

# ── Config ────────────────────────────────────────────────────────────────────
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

# Signal scoring radius around each NP (degrees; ~111 m)
NP_SIGNAL_RADIUS_DEG = 0.001

# If eigenvalue ratio (major / minor axis of site cloud) exceeds this,
# the site distribution is "linear" (highway) → prefer centroid NPs
LINEAR_THRESHOLD = 8.0

CANDIDATE_LABELS = list('ABCDEFGHIJKLMNOPQRSTUVWXYZ')

CANDIDATE_COLORS = [
    '#16a34a',  # A — green  (best)
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
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _metres_to_deg(metres: float, lat: float) -> float:
    lat_deg = metres / 111_320
    lng_deg = metres / (111_320 * math.cos(math.radians(lat)))
    return max(lat_deg, lng_deg)


def _circumcenter(pts) -> Optional[tuple]:
    """
    Circumcenter of a triangle (equidistant from all 3 vertices).
    pts: array-like of shape (3, 2) → [[lng0,lat0], [lng1,lat1], [lng2,lat2]]
    Returns (lng, lat) or None if triangle is degenerate.
    """
    (ax, ay), (bx, by), (cx, cy) = pts[0], pts[1], pts[2]
    D = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(D) < 1e-14:
        return None  # collinear
    ux = ((ax**2 + ay**2) * (by - cy) +
          (bx**2 + by**2) * (cy - ay) +
          (cx**2 + cy**2) * (ay - by)) / D
    uy = ((ax**2 + ay**2) * (cx - bx) +
          (bx**2 + by**2) * (ax - cx) +
          (cx**2 + cy**2) * (bx - ax)) / D
    return (ux, uy)


def _is_linear_distribution(coords: np.ndarray) -> bool:
    """
    Detect highway/linear site distribution via PCA eigenvalue ratio.
    Returns True if the major/minor axis ratio exceeds LINEAR_THRESHOLD.
    """
    if len(coords) < 3:
        return True
    centred = coords - coords.mean(axis=0)
    cov = np.cov(centred.T)
    eigvals = np.linalg.eigvalsh(cov)
    eigvals = sorted(eigvals, reverse=True)
    if eigvals[1] < 1e-12:
        return True   # essentially 1-D
    return (eigvals[0] / eigvals[1]) > LINEAR_THRESHOLD


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_nova_pipeline(
    complaint_lat: float,
    complaint_lng: float,
    radius_m: float = 500,
    top_k: int = 3,
    initiated_by: str = 'system',
    atom_cluster_np_id: str = None,
) -> dict:
    """
    Full NOVA pipeline.

    Parameters
    ----------
    complaint_lat / complaint_lng : float
        Centre of search — ideally the ATOM cluster centroid (NP-id coords).
    radius_m : float
        Search radius in metres.
    top_k : int
        Maximum candidates to return as selected (A/B/C …).
    atom_cluster_np_id : str | None
        NP-id of the parent ATOM cluster (propagated to every candidate).

    Returns
    -------
    dict with keys:
        candidates        – selected + ranked candidate dicts
        all_candidates    – every candidate (selected + rejected), with reasons
        geojson           – FeatureCollection: markers + search circle + wedges
        delaunay_geojson  – FeatureCollection: Delaunay triangle polygons
        run_id            – int | None
        geometry_type     – 'city' | 'highway'
        meta              – radius_m, top_k, n_sites, …
    """
    print(f"[NOVA] Start — NP={atom_cluster_np_id}, "
          f"centre=({complaint_lat},{complaint_lng}), "
          f"radius={radius_m}m, top_k={top_k}, by={initiated_by}")

    # ── 1. Fetch site coordinates ─────────────────────────────────────────────
    deg_pad  = _metres_to_deg(radius_m * 5, complaint_lat)
    lat_min  = complaint_lat - deg_pad
    lat_max  = complaint_lat + deg_pad
    lng_min  = complaint_lng - deg_pad
    lng_max  = complaint_lng + deg_pad

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
    print(f"[NOVA] {n_sites} sites fetched")

    if n_sites < 3:
        return {'error': f'Need ≥ 3 tower sites for triangulation. Found {n_sites}.'}

    coords = sites_df[['lng', 'lat']].values.astype(float)

    # ── 2. Geometry type detection ────────────────────────────────────────────
    is_linear     = _is_linear_distribution(coords)
    geometry_type = 'highway' if is_linear else 'city'
    print(f"[NOVA] Geometry type: {geometry_type}")

    # ── 3. Delaunay triangulation ─────────────────────────────────────────────
    tri = Delaunay(coords)

    tri_features          = []
    circumcenter_pool     = []   # triangulation NPs (equidistant from 3 sites)
    centroid_pool         = []   # triangle centroid NPs (fallback)

    for simplex in tri.simplices:
        pts = coords[simplex]   # shape (3,2): [[lng,lat], ...]
        tri_idx = len(tri_features)

        # Triangle ring (for GeoJSON)
        ring = [(float(p[0]), float(p[1])) for p in pts] + [(float(pts[0][0]), float(pts[0][1]))]
        tri_features.append({
            'type': 'Feature',
            'geometry': {'type': 'Polygon', 'coordinates': [ring]},
            'properties': {'tri_id': tri_idx},
        })

        # Circumcenter (equidistant from 3 vertices)
        cc = _circumcenter(pts)
        if cc is not None:
            cc_lng, cc_lat = cc
            # Circumcenter can be far outside on obtuse triangles — keep only if reasonable
            cc_dist = _haversine_m(complaint_lat, complaint_lng, cc_lat, cc_lng)
            if cc_dist <= radius_m * 3:   # generous pre-filter
                circumcenter_pool.append({
                    'lat': round(float(cc_lat), 6),
                    'lng': round(float(cc_lng), 6),
                    'tri_id': tri_idx,
                    'candidate_type': 'triangulation',
                })

        # Centroid
        c_lng = float(pts[:, 0].mean())
        c_lat = float(pts[:, 1].mean())
        centroid_pool.append({
            'lat': round(c_lat, 6),
            'lng': round(c_lng, 6),
            'tri_id': tri_idx,
            'candidate_type': 'centroid',
        })

    print(f"[NOVA] Triangulation pool: {len(circumcenter_pool)} circumcenters, "
          f"{len(centroid_pool)} centroids")

    # ── 4. Apply selection rule ───────────────────────────────────────────────
    # Filter each pool to radius, compute distances
    def _filter_to_radius(pool):
        result = []
        for p in pool:
            d = _haversine_m(complaint_lat, complaint_lng, p['lat'], p['lng'])
            if d <= radius_m:
                p['dist_m'] = round(d, 1)
                result.append(p)
        return result

    cc_in_radius  = _filter_to_radius(circumcenter_pool)
    cen_in_radius = _filter_to_radius(centroid_pool)

    # De-duplicate within each pool (>10 m apart)
    def _dedup(pool, min_sep_m=10):
        kept = []
        for p in pool:
            if all(_haversine_m(p['lat'], p['lng'], k['lat'], k['lng']) > min_sep_m for k in kept):
                kept.append(p)
        return kept

    cc_in_radius  = _dedup(cc_in_radius)
    cen_in_radius = _dedup(cen_in_radius)

    # Selection rule
    if cc_in_radius and not is_linear:
        # City: prefer triangulation circumcenters
        primary_pool   = cc_in_radius
        fallback_pool  = cen_in_radius
        primary_type   = 'triangulation'
        fallback_type  = 'centroid'
        select_reason  = ('Triangulation NP preferred in city environment: '
                          'circumcenter ensures equidistance from existing sites.')
        reject_reason  = ('Centroid NP not selected: triangulation NP available '
                          'and preferred for equidistant site spacing.')
    else:
        # Highway or no circumcenters in radius: use centroids
        primary_pool   = cen_in_radius
        fallback_pool  = cc_in_radius
        primary_type   = 'centroid'
        fallback_type  = 'triangulation'
        select_reason  = ('Centroid NP used: ' + (
            'highway/linear site distribution detected — circumcenter would be geometrically unreliable.'
            if is_linear else
            'no triangulation NP found within search radius.'))
        reject_reason  = ('Triangulation NP not selected: ' + (
            'linear site geometry makes circumcenter placement unreliable.'
            if is_linear else
            'outside search radius.'))

    n_nps = len(primary_pool) + len(fallback_pool)
    print(f"[NOVA] {len(primary_pool)} primary ({primary_type}), "
          f"{len(fallback_pool)} fallback ({fallback_type}) NPs in radius")

    if not primary_pool and not fallback_pool:
        return {
            'error': f'No Nominal Points found within {radius_m} m. Try increasing the radius.',
            'delaunay_geojson': {'type': 'FeatureCollection', 'features': tri_features},
            'geometry_type': geometry_type,
        }

    # Merge pools for scoring (primary first)
    all_nps = primary_pool + fallback_pool

    # ── 5. Score with signal data ─────────────────────────────────────────────
    if all_nps:
        np_lats = [p['lat'] for p in all_nps]
        np_lngs = [p['lng'] for p in all_nps]
        sig_sql = f"""
            SELECT CAST(latitude        AS DOUBLE) AS lat,
                   CAST(longitude       AS DOUBLE) AS lng,
                   CAST(signal_strength AS DOUBLE) AS rsrp
            FROM coverage_holes_clustered
            WHERE UPPER(TRIM(CAST(data_source AS VARCHAR))) = 'MR'
              AND latitude  IS NOT NULL
              AND longitude IS NOT NULL
              AND CAST(latitude  AS DOUBLE) BETWEEN {min(np_lats)-NP_SIGNAL_RADIUS_DEG}
                                                AND {max(np_lats)+NP_SIGNAL_RADIUS_DEG}
              AND CAST(longitude AS DOUBLE) BETWEEN {min(np_lngs)-NP_SIGNAL_RADIUS_DEG}
                                                AND {max(np_lngs)+NP_SIGNAL_RADIUS_DEG}
            LIMIT 20000
        """
        try:
            sig_df = wr.athena.read_sql_query(
                sig_sql,
                database=ATHENA_DATABASE,
                s3_output=S3_STAGING_DIR,
                boto3_session=session,
            ).dropna(subset=['lat', 'lng'])
        except Exception as e:
            print(f"[NOVA] Signal query failed (non-fatal): {e}")
            sig_df = pd.DataFrame(columns=['lat', 'lng', 'rsrp'])
    else:
        sig_df = pd.DataFrame(columns=['lat', 'lng', 'rsrp'])

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

        def _w(r):
            if pd.isna(r): return 0
            if r <= -126: return 4
            if r <= -120: return 3
            if r <= -118: return 2
            if r <= -110: return 1
            return 0
        weight_sum = int(pts['rsrp'].apply(_w).sum())
        return count, weight_sum, avg_rsrp

    for p in all_nps:
        cnt, wscore, avg = _signal_score(p['lat'], p['lng'])
        p['signal_count']      = cnt
        p['signal_weight_sum'] = wscore
        p['avg_rsrp']          = round(avg, 1) if avg is not None else None

    # ── 6. Rank primary pool, label top-K ────────────────────────────────────
    primary_pool.sort(key=lambda p: (-p['signal_weight_sum'], p['dist_m']))

    selected_candidates = []
    for i, p in enumerate(primary_pool[:top_k]):
        label = CANDIDATE_LABELS[i] if i < len(CANDIDATE_LABELS) else str(i + 1)
        color = CANDIDATE_COLORS[i] if i < len(CANDIDATE_COLORS) else '#6b7280'
        p.update({
            'rank':             i + 1,
            'label':            label,
            'color':            color,
            'is_selected':      True,
            'selection_reason': select_reason,
            'rejection_reason': None,
            'np_id':            atom_cluster_np_id,
        })
        selected_candidates.append(p)

    # Mark unselected primary candidates
    for i, p in enumerate(primary_pool[top_k:]):
        p.update({
            'rank':             None,
            'label':            None,
            'color':            '#9ca3af',
            'is_selected':      False,
            'selection_reason': None,
            'rejection_reason': f'Outside top-{top_k} ranking by signal weight score.',
            'np_id':            atom_cluster_np_id,
        })

    # Mark all fallback pool candidates as rejected
    for p in fallback_pool:
        p.update({
            'rank':             None,
            'label':            None,
            'color':            '#9ca3af',
            'is_selected':      False,
            'selection_reason': None,
            'rejection_reason': reject_reason,
            'np_id':            atom_cluster_np_id,
        })

    all_candidates = all_nps   # primary (selected+unselected) + fallback

    print(f"[NOVA] {len(selected_candidates)} selected / {len(all_candidates)} total candidates")

    # ── 7. Build GeoJSON ──────────────────────────────────────────────────────
    features = []

    # Complaint / centroid point
    features.append({
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': [complaint_lng, complaint_lat]},
        'properties': {
            'type': 'complaint',
            'color': '#ef4444',
            'np_id': atom_cluster_np_id,
            'label': atom_cluster_np_id or 'NP',
        },
    })

    # Search circle
    deg_lat  = radius_m / 111_320
    deg_lng  = radius_m / (111_320 * math.cos(math.radians(complaint_lat)))
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

    # Candidate markers (selected only for main layer; all available via all_candidates)
    for c in selected_candidates:
        # Bearing from centroid to candidate (for wedge orientation)
        dlat = c['lat'] - complaint_lat
        dlng = c['lng'] - complaint_lng
        bearing_deg = math.degrees(math.atan2(dlng, dlat)) % 360

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
                'candidate_type':    c['candidate_type'],
                'bearing_deg':       round(bearing_deg, 1),
                'np_id':             atom_cluster_np_id,
                'selection_reason':  c['selection_reason'],
            },
        })

        # Wedge: line segment from centroid to candidate (for direction indicator)
        features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'LineString',
                'coordinates': [
                    [complaint_lng, complaint_lat],
                    [c['lng'], c['lat']],
                ],
            },
            'properties': {
                'type':  'candidate_ray',
                'color': c['color'],
                'label': c['label'],
            },
        })

    # ── 8. Persist run ────────────────────────────────────────────────────────
    run_id = _save_run(
        complaint_lat=complaint_lat,
        complaint_lng=complaint_lng,
        radius_m=radius_m,
        top_k=top_k,
        n_sites=n_sites,
        n_nps=n_nps,
        n_candidates=len(selected_candidates),
        initiated_by=initiated_by,
        atom_cluster_np_id=atom_cluster_np_id,
        all_candidates=all_candidates,
    )

    return _sanitise({
        'run_id':        run_id,
        'candidates':    selected_candidates,
        'all_candidates': all_candidates,
        'geometry_type': geometry_type,
        'geojson': {
            'type':     'FeatureCollection',
            'features': features,
        },
        'delaunay_geojson': {
            'type':     'FeatureCollection',
            'features': tri_features,
        },
        'meta': {
            'complaint_lat':     complaint_lat,
            'complaint_lng':     complaint_lng,
            'radius_m':          radius_m,
            'top_k':             top_k,
            'n_sites':           n_sites,
            'n_nps':             n_nps,
            'n_candidates':      len(selected_candidates),
            'geometry_type':     geometry_type,
            'atom_cluster_np_id': atom_cluster_np_id,
        },
    })


# ── Persistence ───────────────────────────────────────────────────────────────

def _save_run(complaint_lat, complaint_lng, radius_m, top_k,
              n_sites, n_nps, n_candidates, initiated_by,
              atom_cluster_np_id, all_candidates: list) -> Optional[int]:
    try:
        conn   = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO nova_runs
                (complaint_lat, complaint_lng, radius_m, top_k,
                 n_sites, n_nps, n_candidates, initiated_by, ran_at,
                 atom_cluster_np_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (complaint_lat, complaint_lng, radius_m, top_k,
             n_sites, n_nps, n_candidates, initiated_by, datetime.now(),
             atom_cluster_np_id),
        )
        run_id = cursor.fetchone()[0]

        # Persist ALL candidates (selected + rejected)
        for i, c in enumerate(all_candidates):
            cursor.execute(
                """
                INSERT INTO nova_candidates
                    (run_id, np_id, label, rank, lat, lng, dist_m,
                     signal_count, signal_weight_sum, avg_rsrp, color,
                     candidate_type, is_selected, selection_reason, rejection_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (run_id,
                 c.get('np_id'), c.get('label'), c.get('rank'),
                 c['lat'], c['lng'], c.get('dist_m', 0),
                 c.get('signal_count', 0), c.get('signal_weight_sum', 0),
                 c.get('avg_rsrp'), c.get('color', '#9ca3af'),
                 c.get('candidate_type', 'centroid'),
                 bool(c.get('is_selected', False)),
                 c.get('selection_reason'), c.get('rejection_reason')),
            )

        conn.commit()
        cursor.close()
        conn.close()
        print(f"[NOVA] Run saved → id={run_id}, {len(all_candidates)} candidates persisted")
        return run_id
    except Exception as e:
        print(f"[NOVA] Could not save run to DB: {e}")
        return None


def get_nova_run_candidates(run_id: int) -> list:
    """Return all saved candidates for a specific NOVA run (selected + rejected)."""
    try:
        conn   = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, np_id, label, rank, lat, lng, dist_m,
                   signal_count, signal_weight_sum, avg_rsrp, color,
                   candidate_type, is_selected, selection_reason, rejection_reason,
                   created_at
            FROM nova_candidates
            WHERE run_id = %s
            ORDER BY is_selected DESC, rank ASC NULLS LAST, id ASC
            """,
            (run_id,),
        )
        cols = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        result = []
        for row in rows:
            r = dict(zip(cols, row))
            if r.get('created_at'):
                r['created_at'] = r['created_at'].isoformat()
            result.append(r)
        return result
    except Exception as e:
        print(f"[NOVA] get_nova_run_candidates error: {e}")
        return []


def get_nova_candidates_by_np_id(np_id: str) -> list:
    """Return all NOVA candidates ever generated for a given NP-id."""
    try:
        conn   = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT nc.id, nc.run_id, nc.label, nc.rank, nc.lat, nc.lng,
                   nc.dist_m, nc.signal_count, nc.signal_weight_sum, nc.avg_rsrp,
                   nc.color, nc.candidate_type, nc.is_selected,
                   nc.selection_reason, nc.rejection_reason, nc.created_at,
                   nr.ran_at, nr.geometry_type_hint
            FROM nova_candidates nc
            JOIN nova_runs nr ON nr.id = nc.run_id
            WHERE nc.np_id = %s
            ORDER BY nc.is_selected DESC, nc.rank ASC NULLS LAST, nc.created_at DESC
            """,
            (np_id,),
        )
        cols = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        result = []
        for row in rows:
            r = dict(zip(cols, row))
            for k in ('created_at', 'ran_at'):
                if r.get(k): r[k] = r[k].isoformat()
            result.append(r)
        return result
    except Exception as e:
        print(f"[NOVA] get_nova_candidates_by_np_id error: {e}")
        return []


def get_nova_recent_runs(limit: int = 10) -> list:
    try:
        conn   = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, complaint_lat, complaint_lng, radius_m, top_k,
                   n_sites, n_nps, n_candidates, initiated_by, ran_at,
                   atom_cluster_np_id
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
                'id':                  r[0],
                'complaint_lat':       r[1],
                'complaint_lng':       r[2],
                'radius_m':            r[3],
                'top_k':               r[4],
                'n_sites':             r[5],
                'n_nps':               r[6],
                'n_candidates':        r[7],
                'initiated_by':        r[8],
                'ran_at':              r[9].isoformat() if r[9] else None,
                'atom_cluster_np_id':  r[10],
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[NOVA] get_nova_recent_runs error: {e}")
        return []
