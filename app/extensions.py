"""
Shared application-level singletons: database connection, AWS session,
in-memory query cache, and request decorators.
"""
import collections
import time
import os
from contextlib import contextmanager
from functools import wraps

import boto3
import awswrangler as wr
import pandas as pd
import psycopg2
from flask import session, jsonify, redirect, url_for

from app.config import (
    DB_CONFIG, AWS_REGION, ATHENA_DATABASE, S3_STAGING_DIR,
    ATHENA_CACHE_SETTINGS, MAX_CACHE_SIZE,
)

# ── AWS Session ────────────────────────────────────────────────────────────────
aws_session = boto3.Session(region_name=AWS_REGION)

# ── In-Memory Query Cache ──────────────────────────────────────────────────────
RAM_CACHE: collections.OrderedDict = collections.OrderedDict()


# ── Database ───────────────────────────────────────────────────────────────────
@contextmanager
def get_db_connection():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Athena / Cached DataFrame ──────────────────────────────────────────────────
def get_cached_dataframe(sql: str) -> pd.DataFrame:
    """Fetches from server RAM if available (<7 days old), otherwise queries Athena."""
    now = time.time()
    if sql in RAM_CACHE and (now - RAM_CACHE[sql]['timestamp']) < 604800:
        RAM_CACHE.move_to_end(sql)
        return RAM_CACHE[sql]['df']

    df = wr.athena.read_sql_query(
        sql=sql,
        database=ATHENA_DATABASE,
        s3_output=S3_STAGING_DIR,
        boto3_session=aws_session,
        ctas_approach=False,
        unload_approach=False,
        athena_cache_settings=ATHENA_CACHE_SETTINGS,
    )

    RAM_CACHE[sql] = {'timestamp': now, 'df': df}
    if len(RAM_CACHE) > MAX_CACHE_SIZE:
        RAM_CACHE.popitem(last=False)

    return df


# ── Pandas Filter Helper ───────────────────────────────────────────────────────
def apply_pandas_filters(df: pd.DataFrame, request_args) -> pd.DataFrame:
    """Filters a loaded DataFrame based on UI request arguments."""
    if df.empty:
        return df
    filtered = df.copy()

    region = request_args.get('region')
    if region and region != 'All' and 'region' in filtered.columns:
        filtered = filtered[filtered['region'].str.upper() == region.upper()]

    operator = request_args.get('operator')
    if operator and operator != 'All' and 'operator' in filtered.columns:
        filtered = filtered[filtered['operator'] == operator]

    cluster = request_args.get('cluster')
    if cluster and cluster != 'All' and 'cluster' in filtered.columns:
        filtered = filtered[filtered['cluster'] == cluster]

    week = request_args.get('week')
    if week and str(week).lower() not in ['all', ''] and 'week' in filtered.columns:
        filtered = filtered[pd.to_numeric(filtered['week'], errors='coerce') == int(week)]

    return filtered


# ── Auth Decorators ────────────────────────────────────────────────────────────
def api_login_required(f):
    """For API routes — returns JSON 401 instead of redirecting."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated
