"""
atom_pipeline.py
================
ATOM — Automated Telecommunication Opportunity Mapping
Native Jejak pipeline module.

Fetches MR bad-signal points from Athena, auto-tunes DBSCAN
parameters via KNN (AutoDBSCAN), runs DBSCAN clustering,
generates convex hull polygons per cluster, and returns
structured GeoJSON ready for Leaflet rendering.

Architecture:
  - Mirrors Jejak's existing data access patterns (awswrangler + Athena)
  - Uses same DB_CONFIG as auth.py / app.py
  - Pure sklearn + shapely — no new Docker dependencies required
"""

import os
import json
from typing import Optional
import numpy as np
import pandas as pd
import awswrangler as wr
import boto3
import psycopg2
from datetime import datetime
from sklearn.neighbors import NearestNeighbors
from sklearn.cluster import DBSCAN
from shapely.geometry import MultiPoint, mapping
from shapely.errors import TopologicalError

# ── Config — mirrors app.py exactly ──────────────────────────────────────────
ATHENA_DATABASE  = os.getenv("ATHENA_DATABASE",  "jejak-mappro-demo")
S3_STAGING_DIR   = os.getenv("S3_STAGING_DIR",   "s3://jejak-mappro-demo/3W-data/athena-query-results/")
AWS_REGION       = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")

DB_CONFIG = {
    'host':     os.getenv('DB_HOST',     'localhost'),
    'database': os.getenv('DB_NAME',     'vibe_db'),
    'user':     os.getenv('DB_USER',     'postgres'),
    'password': os.getenv('DB_PASSWORD', '1234'),
    'port':     os.getenv('DB_PORT',     '5432'),
}

# RSRP threshold — LTE "very poor" signal standard (dBm)
RSRP_THRESHOLD = -115

# 15 distinct colours for clusters (matches Jejak's existing getClusterColor palette + extras)
CLUSTER_COLORS = [
    '#ef4444', '#f97316', '#eab308', '#22c55e', '#3b82f6',
    '#8b5cf6', '#ec4899', '#14b8a6', '#f43f5e', '#84cc16',
    '#06b6d4', '#a855f7', '#fb923c', '#4ade80', '#60a5fa',
]
NOISE_COLOR = '#6b7280'


def _sanitise(obj):
    """Recursively convert numpy scalars → native Python so jsonify never chokes."""
    if isinstance(obj, dict):
        return {k: _sanitise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitise(v) for v in obj]
    if hasattr(obj, 'item'):      # numpy scalar (int64, float64, bool_, …)
        return obj.item()
    return obj


def _aws_session():
    return boto3.Session(region_name=AWS_REGION)


# ── Step 1: Auto-tune DBSCAN parameters via KNN ──────────────────────────────

def auto_dbscan_params(coords: np.ndarray) -> dict:
    """
    Compute suggested eps and minPts automatically from point density.

    Algorithm (mirrors ATOM+ DBSCAN parameters.py):
      1. Fit NearestNeighbors(k=6) on all coordinates
      2. Derive minPts from ratio of avg_5th_dist / avg_1st_dist
      3. Derive eps from the 74.5th-percentile of sorted k-distances

    Returns:
        {eps, min_pts, avg_nn_dist, point_count}
    """
    n = len(coords)
    if n < 5:
        raise ValueError(f"Not enough points for auto-tuning ({n} < 5 minimum)")

    k = min(6, n - 1)

    # k-distance statistics
    neigh = NearestNeighbors(n_neighbors=k)
    neigh.fit(coords)
    distances, _ = neigh.kneighbors(coords)

    avg_nn = float(np.mean(distances[:, 1]))                    # avg nearest-neighbour
    avg_dk = float(np.mean(distances[:, min(4, k - 1)]))        # avg 5th-neighbour

    # MinPts: dynamic but bounded [3, 10]
    beta = 2.0
    raw_min_pts = int(round(beta * (avg_dk / avg_nn))) if avg_nn > 0 else 3
    min_pts = max(3, min(10, raw_min_pts))

    # Eps: k-distance elbow at 74.5th percentile
    neigh2 = NearestNeighbors(n_neighbors=min_pts)
    neigh2.fit(coords)
    d2, _ = neigh2.kneighbors(coords)
    k_dists = np.sort(d2[:, -1])
    elbow_idx = int(len(k_dists) * 0.745)
    eps = float(k_dists[elbow_idx])

    return {
        'eps':         round(eps, 6),
        'min_pts':     min_pts,
        'avg_nn_dist': round(avg_nn, 6),
        'point_count': n,
    }


# ── Step 2: Full ATOM pipeline ────────────────────────────────────────────────

def run_atom_pipeline(region: str = 'All', week: str = None, initiated_by: str = 'system') -> dict:
    """
    Execute the full ATOM analysis pipeline:

    1. Fetch MR bad-signal points from Athena (coverage_holes_clustered)
    2. Auto-tune DBSCAN via KNN  →  eps + minPts
    3. Run DBSCAN clustering
    4. Build convex hull polygon per valid cluster
    5. Serialise results as GeoJSON
    6. Persist run metadata to atom_runs (PostgreSQL)

    Args:
        region:        UI region filter ('All' = no filter)
        week:          UI week filter (None = no filter)
        initiated_by:  username of the user who triggered the run

    Returns:
        {run_id, params, n_clusters, n_noise, total_points,
         cluster_summaries, geojson (hulls), points_geojson}
    """
    session = _aws_session()

    # ── 1. Fetch data ────────────────────────────────────────────────────────
    # coverage_holes_clustered only has: latitude, longitude, signal_strength,
    # cluster_id, serving_cell, data_source — no week/region columns.
    sql = f"""
        SELECT
            CAST(latitude        AS DOUBLE)  AS lat,
            CAST(longitude       AS DOUBLE)  AS lng,
            CAST(signal_strength AS DOUBLE)  AS rsrp,
            CAST(serving_cell    AS VARCHAR) AS serving_cell
        FROM coverage_holes_clustered
        WHERE UPPER(TRIM(CAST(data_source AS VARCHAR))) = 'MR'
          AND CAST(signal_strength AS DOUBLE) <= {RSRP_THRESHOLD}
          AND latitude  IS NOT NULL
          AND longitude IS NOT NULL
        LIMIT 50000
    """

    print(f"[ATOM] Fetching MR data from Athena...")
    try:
        df = wr.athena.read_sql_query(
            sql=sql,
            database=ATHENA_DATABASE,
            s3_output=S3_STAGING_DIR,
            boto3_session=session,
            ctas_approach=False,
        )
    except Exception as e:
        return {'error': f'Athena query failed: {str(e)}', 'point_count': 0}

    if df.empty:
        return {'error': 'No MR data found for the selected filters', 'point_count': 0}

    # ── 2. Clean ─────────────────────────────────────────────────────────────
    df = df.dropna(subset=['lat', 'lng'])
    df = df[df['lat'].between(-90, 90) & df['lng'].between(-180, 180)].copy()
    df = df.drop_duplicates(subset=['lat', 'lng'])

    n_input = len(df)
    print(f"[ATOM] {n_input} valid MR points loaded (RSRP ≤ {RSRP_THRESHOLD} dBm)")

    if n_input < 5:
        return {'error': f'Insufficient data points after cleaning ({n_input})', 'point_count': n_input}

    coords = df[['lat', 'lng']].values

    # ── 3. Auto-tune DBSCAN ──────────────────────────────────────────────────
    print("[ATOM] Running AutoDBSCAN (KNN parameter estimation)...")
    try:
        params = auto_dbscan_params(coords)
    except ValueError as e:
        return {'error': str(e), 'point_count': n_input}

    eps     = params['eps']
    min_pts = params['min_pts']
    print(f"[ATOM] AutoDBSCAN → eps={eps}, minPts={min_pts}")

    # ── 4. DBSCAN clustering ─────────────────────────────────────────────────
    print("[ATOM] Running DBSCAN clustering...")
    db = DBSCAN(eps=eps, min_samples=min_pts, metric='euclidean', n_jobs=-1)
    labels = db.fit_predict(coords)
    df['cluster_id'] = labels

    unique_clusters = [int(c) for c in sorted(set(labels)) if c != -1]
    n_clusters      = int(len(unique_clusters))
    n_noise         = int((labels == -1).sum())

    print(f"[ATOM] Result: {n_clusters} clusters, {n_noise} noise points")

    # ── 5. Build GeoJSON ─────────────────────────────────────────────────────
    hull_features   = []
    point_features  = []
    cluster_summaries = []

    # Points layer
    for _, row in df.iterrows():
        cid   = int(row['cluster_id'])
        color = CLUSTER_COLORS[cid % len(CLUSTER_COLORS)] if cid != -1 else NOISE_COLOR
        point_features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [float(row['lng']), float(row['lat'])],
            },
            'properties': {
                'type':         'point',
                'cluster_id':   cid,
                'rsrp':         round(float(row['rsrp']), 1) if pd.notna(row['rsrp']) else None,
                'serving_cell': str(row.get('serving_cell', '')),
                'color':        color,
            },
        })

    # Convex hull layer + cluster summary
    for cid in unique_clusters:
        cid        = int(cid)
        cluster_df = df[df['cluster_id'] == cid]
        n          = int(len(cluster_df))
        color      = CLUSTER_COLORS[cid % len(CLUSTER_COLORS)]

        # Need ≥ 3 non-collinear points for a polygon hull
        if n >= 3:
            try:
                pts  = [(float(x), float(y)) for x, y in zip(cluster_df['lng'], cluster_df['lat'])]
                hull = MultiPoint(pts).convex_hull
                geom = _sanitise(mapping(hull))
            except (TopologicalError, Exception):
                geom = {
                    'type': 'Point',
                    'coordinates': [
                        float(cluster_df['lng'].mean()),
                        float(cluster_df['lat'].mean()),
                    ],
                }
        else:
            geom = {
                'type': 'Point',
                'coordinates': [
                    float(cluster_df['lng'].mean()),
                    float(cluster_df['lat'].mean()),
                ],
            }

        avg_rsrp = float(cluster_df['rsrp'].mean()) if not cluster_df['rsrp'].isna().all() else None

        hull_features.append({
            'type': 'Feature',
            'geometry': geom,
            'properties': {
                'type':        'hull',
                'cluster_id':  cid,
                'point_count': n,
                'avg_rsrp':    round(avg_rsrp, 1) if avg_rsrp is not None else None,
                'color':       color,
            },
        })

        cluster_summaries.append({
            'cluster_id':  cid,
            'point_count': n,
            'avg_rsrp':    round(avg_rsrp, 1) if avg_rsrp is not None else None,
            'color':       color,
            'center_lat':  round(float(cluster_df['lat'].mean()), 5),
            'center_lng':  round(float(cluster_df['lng'].mean()), 5),
        })

    # ── 6. Persist run ───────────────────────────────────────────────────────
    run_id = _save_run(params, n_clusters, n_noise, n_input, region, week, initiated_by)

    return _sanitise({
        'run_id':            run_id,
        'params':            params,
        'n_clusters':        n_clusters,
        'n_noise':           n_noise,
        'total_points':      n_input,
        'cluster_summaries': cluster_summaries,
        'geojson': {
            'type':     'FeatureCollection',
            'features': hull_features,
        },
        'points_geojson': {
            'type':     'FeatureCollection',
            'features': point_features,
        },
    })


# ── Persistence ───────────────────────────────────────────────────────────────

def _save_run(params, n_clusters, n_noise, total_points, region, week, initiated_by) -> Optional[int]:
    """Write ATOM run summary to atom_runs table."""
    try:
        conn   = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO atom_runs
                (eps, min_pts, n_clusters, n_noise, total_points, region, week, initiated_by, ran_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                params['eps'], params['min_pts'],
                n_clusters, n_noise, total_points,
                region or 'All', week or 'All',
                initiated_by,
                datetime.now(),
            ),
        )
        run_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        print(f"[ATOM] Run saved → atom_runs.id={run_id}")
        return run_id
    except Exception as e:
        print(f"[ATOM] Could not save run to DB: {e}")
        return None


def get_recent_runs(limit: int = 10) -> list:
    """Return the last N ATOM runs from PostgreSQL."""
    try:
        conn   = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, eps, min_pts, n_clusters, n_noise, total_points,
                   region, week, initiated_by, ran_at
            FROM atom_runs
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
                'id':           r[0],
                'eps':          float(r[1]),
                'min_pts':      int(r[2]),
                'n_clusters':   int(r[3]),
                'n_noise':      int(r[4]),
                'total_points': int(r[5]),
                'region':       r[6],
                'week':         r[7],
                'initiated_by': r[8],
                'ran_at':       r[9].isoformat() if r[9] else None,
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[ATOM] Error fetching run history: {e}")
        return []
