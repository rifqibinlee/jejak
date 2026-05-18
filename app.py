import os
import json
import pandas as pd
import numpy as np
from flask import Flask, render_template, request, jsonify, Response, redirect, send_file
from datetime import datetime, date, timedelta
import awswrangler as wr
import boto3
import traceback
import math
import time
import requests
from functools import wraps
import orjson

# --- AI AGENT IMPORT ---
from agent import run_netalytics_agent
from genset_pipeline import route_substations
from atom_pipeline import run_atom_pipeline, get_recent_runs
from nova_pipeline import run_nova_pipeline, get_nova_recent_runs, get_nova_run_candidates
from pave_pipeline import run_pave, get_pave_recent_runs
from geoserver_integration import (
    catalog_payload,
    geoserver_enabled,
    proxy_wms_get,
)

# --- PLOTLY & BOKEH IMPORTS ---
from sklearn.linear_model import LinearRegression
from scipy.stats import t as t_dist
import matplotlib
matplotlib.use('Agg')
from bokeh.plotting import figure
from bokeh.layouts import gridplot
from bokeh.models import ColumnDataSource, HoverTool
from bokeh.embed import json_item

import psycopg2
from psycopg2.extras import execute_values
from contextlib import contextmanager
from flask import session, url_for

import jwt
import collections

# --- AUTH MODULE ---
from auth import (
    authenticate_user, register_user, login_required, role_required,
    get_user_permissions, get_all_users, get_login_history,
    update_user, delete_user, change_password
)

app = Flask(__name__)

app.secret_key = os.environ.get('SECRET_KEY', 'vibe-production-secret-key-2026')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)

METABASE_SITE_URL = os.environ.get('METABASE_URL', 'http://52.221.228.202:3000')
METABASE_SECRET_KEY = os.environ.get('METABASE_SECRET_KEY', '0e1c46582460375c024ab228cb2994daec4daa0e21ca21711adc279444e68947') # Get this from Metabase Admin Settings

# --- POSTGRES DB CONFIG ---
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'vibe_db'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', '1234'),
    'port': os.getenv('DB_PORT', '5432')
}

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

# --- AWS ATHENA CONFIGURATION ---
ATHENA_DATABASE = "jejak-mappro-demo"
S3_STAGING_DIR = "s3://jejak-mappro-demo/3W-data/athena-query-results/"
PRICING_FILE = 'capex_pricing.json'

ATHENA_CACHE_SETTINGS = {
    "max_cache_seconds": 604800, # Cache for 7 Days
    "max_cache_query_inspections": 500
}

# Cap the RAM cache so it doesn't grow infinitely and cause OOM crashes
MAX_CACHE_SIZE = 20
RAM_CACHE = collections.OrderedDict()

def api_login_required(f):
    """Decorator for API routes that returns JSON instead of redirecting"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function

def get_cached_dataframe(sql):
    """Fetches from server RAM if available, otherwise asks Athena/S3."""
    now = time.time()

    # 1. Check if the exact SQL is already in RAM and less than 7 days old
    if sql in RAM_CACHE and (now - RAM_CACHE[sql]['timestamp']) < 604800:
        # Move to end to mark as recently used
        RAM_CACHE.move_to_end(sql)
        return RAM_CACHE[sql]['df']

    # 2. If not in RAM, use Wrangler to ask Athena (This triggers AWS Query Result Reuse)
    df = wr.athena.read_sql_query(
        sql=sql,
        database=ATHENA_DATABASE,
        s3_output=S3_STAGING_DIR,
        boto3_session=aws_session,
        ctas_approach=False,
        unload_approach=False, # MUST be False to keep AWS billing low and avoid HIVE path errors
        athena_cache_settings=ATHENA_CACHE_SETTINGS
    )

    # 3. Store the new dataset in RAM
    RAM_CACHE[sql] = {'timestamp': now, 'df': df}
    
    # 4. If RAM gets too full, silently delete the oldest cached query
    if len(RAM_CACHE) > MAX_CACHE_SIZE:
        RAM_CACHE.popitem(last=False)
        
    return df

def get_optimized_json_response(s3_path, filters):
    # 1. State only the columns you NEED for the specific UI chart
    # This makes Parquet lightning fast as it skips reading other data
    required_columns = ['zoom_sector_id', 'eric_prb_util_rate', 'week', 'region']

    # 2. Read from S3 using Parquet (Zero RAM Tax for unused columns)
    df = wr.s3.read_parquet(
        path=s3_path,
        columns=required_columns,
        use_threads=True
    )

    # 3. Filter BEFORE converting to JSON (The "Controlled Fire")
    # This ensures you only process rows relevant to the user
    if filters.get('region') and filters['region'] != 'All':
        df = df[df['region'] == filters['region']]

    # 4. Use orjson for a low-memory spike conversion
    # converting 100 rows takes milliseconds and negligible RAM
    return orjson.dumps(df.to_dict('records'))

# [CRITICAL FIX]: Force the exact AWS Region so Wrangler doesn't get lost,
# and disable CTAS to prevent bucket verification errors.
aws_session = boto3.Session(region_name="ap-southeast-1")

# --- MALAYSIA HOLIDAYS ---
MALAYSIA_HOLIDAYS = {
    datetime(2026, 1, 1): "New Year", datetime(2026, 2, 1): "Federal Territory",
    datetime(2026, 2, 17): "CNY", datetime(2026, 3, 20): "Hari Raya Aidilfitri",
    datetime(2026, 5, 1): "Labour Day", datetime(2026, 5, 27): "Hari Raya Haji",
    datetime(2026, 8, 31): "Merdeka", datetime(2026, 9, 16): "Malaysia Day",
    datetime(2026, 12, 25): "Christmas"
}

def apply_pandas_filters(df, request_args):
    """Filters a loaded Pandas DataFrame based on UI request arguments in memory."""
    if df.empty:
        return df

    filtered_df = df.copy()

    # Filter by Region
    region = request_args.get('region')
    if region and region != 'All' and 'region' in filtered_df.columns:
        filtered_df = filtered_df[filtered_df['region'].str.upper() == region.upper()]

    # Filter by Operator
    operator = request_args.get('operator')
    if operator and operator != 'All' and 'operator' in filtered_df.columns:
        filtered_df = filtered_df[filtered_df['operator'] == operator]

    # Filter by Cluster
    cluster = request_args.get('cluster')
    if cluster and cluster != 'All' and 'cluster' in filtered_df.columns:
        filtered_df = filtered_df[filtered_df['cluster'] == cluster]

    # Filter by Week
    week = request_args.get('week')
    if week and str(week).lower() not in ['all', ''] and 'week' in filtered_df.columns:
        filtered_df = filtered_df[pd.to_numeric(filtered_df['week'], errors='coerce') == int(week)]

    return filtered_df

@app.route('/api/map/upgrade-cases')
@api_login_required
def api_map_upgrade_cases():
    """Get sites with upgrade cases directly from Athena"""
    week = request.args.get('week', type=int)
    year = request.args.get('year', str(datetime.now().year))

    if not week:
        return jsonify([]), 400

    try:
        sql = f"""
            SELECT DISTINCT
                split_part(cu.zoom_sector_id, '_', 1) as site_id,
                cu.zoom_sector_id,
                cu.suggested_upgrade_case as upgrade_case,
                cu.estimated_total_capex_rm as total_capex,
                cu.projected_prb_pct as prb,
                ca.eric_dl_user_ip_thpt as dl_thpt,
                GREATEST(COALESCE(ca.eric_max_rrc_user,0), COALESCE(ca.max_active_user,0)) as user_count,
                CAST(cu.data_week AS INTEGER) as week
            FROM capex_upgrades cu
            LEFT JOIN congestion_analysis ca
                ON cu.zoom_sector_id = ca.zoom_sector_id
                AND cu.data_week = ca.week
                AND CAST(ca.year AS VARCHAR) = '{year}'
            WHERE cu.suggested_upgrade_case IS NOT NULL
              AND cu.suggested_upgrade_case NOT IN ('', 'None', 'No Upgrade Needed')
              AND CAST(cu.data_week AS INTEGER) = {week}
            ORDER BY cu.estimated_total_capex_rm DESC
        """

        df = get_cached_dataframe(sql)

        if df.empty:
            return jsonify([])

        # Group by site_id
        result = []
        for site_id, group in df.groupby('site_id'):
            upgrade_details = []
            for _, row in group.iterrows():
                upgrade_details.append({
                    'sector_id': row['zoom_sector_id'],
                    'upgrade_case': row['upgrade_case'],
                    'capex': float(row['total_capex']) if pd.notna(row['total_capex']) else 0,
                    'prb': float(row['prb']) if pd.notna(row['prb']) else 0,
                    'thpt': float(row['dl_thpt']) if pd.notna(row['dl_thpt']) else 0,
                    'users': int(row['user_count']) if pd.notna(row['user_count']) else 0
                })

            result.append({
                'site_id': site_id,
                'upgrade_details': upgrade_details,
                'total_capex': sum(d['capex'] for d in upgrade_details)
            })

        return jsonify(result)

    except Exception as e:
        print(f"Error fetching upgrade cases: {e}")
        traceback.print_exc()
        return jsonify([]), 500

# --- CORE ROUTES ---
@app.route('/')
@api_login_required
def index():
    role = session.get('role', 'Staff')
    return render_template(
        'index.html',
        user_id=session.get('user_id'),
        username=session.get('username', 'User'),
        full_name=session.get('full_name', ''),
        role=role
    )

# @app.route('/map')
# @api_login_required
# def map_view():
#     role = session.get('role', 'Staff')
# 
#     # 1. Fetch the permissions for this specific role
#     user_permissions = get_user_permissions(role)
# 
#     return render_template(
#         'map.html',
#         user_id=session.get('user_id'),
#         username=session.get('username', 'User'),
#         full_name=session.get('full_name', ''),
#         role=role,
#         permissions=user_permissions  # 2. Pass it to the template!
#     )

@app.route('/iam')
@api_login_required
@role_required('Admin')
def iam_panel():
    role = session.get('role', 'Admin')
    return render_template(
        'iam.html',
        user_id=session.get('user_id'),
        username=session.get('username', 'Admin'),
        full_name=session.get('full_name', ''),
        role=role
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password: return jsonify({'success': False, 'message': 'Username and password required'}), 400

    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', 'Unknown')
    success, user_data, message = authenticate_user(username, password, ip_address, user_agent)

    if success:
        session['user_id'] = user_data['id']
        session['username'] = user_data['username']
        session['role'] = user_data['role']
        session['full_name'] = user_data['full_name']
        session.permanent = True
        return jsonify({'success': True, 'message': message, 'redirect': '/'})
    return jsonify({'success': False, 'message': message}), 401

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        return render_template('register.html')
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    email = data.get('email', '').strip()
    full_name = data.get('full_name', '').strip()
    role = data.get('role', 'Staff')

    if not all([username, password, email, full_name]): return jsonify({'success': False, 'message': 'All fields are required'}), 400
    success, message = register_user(username, password, email, full_name, role)
    if success:
        return jsonify({'success': True, 'message': message, 'redirect': '/login'})
    return jsonify({'success': False, 'message': message}), 400

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/api/iam/users', methods=['GET'])
@api_login_required
@role_required('Admin')
def get_users():
    return jsonify(get_all_users())

@app.route('/api/iam/users/<int:user_id>', methods=['PUT', 'DELETE'])
@api_login_required
@role_required('Admin')
def manage_user(user_id):
    if request.method == 'PUT':
        success, message = update_user(user_id, **request.json)
    else:
        success, message = delete_user(user_id)
    return jsonify({'success': success, 'message': message})

@app.route('/api/iam/login-history', methods=['GET'])
@api_login_required
@role_required('Admin')
def get_login_history_route():
    return jsonify(get_login_history())

@app.route('/api/iam/activity', methods=['GET'])
@api_login_required
@role_required('Admin')
def get_user_activity():
    filter_type = request.args.get('filter', 'all')
    offset = request.args.get('offset', 0, type=int)
    limit = request.args.get('limit', 20, type=int)

    try:
        with get_db_connection() as conn:
            parts, params = [], []
            if filter_type in ('all', 'annotation'):
                parts.append("""SELECT 'annotation' AS type, ma.created_by_username AS username, ma.created_at AS timestamp, ma.title AS title, ma.shape_type AS shape_type, ma.priority AS priority, ma.status AS ann_status, NULL::TEXT AS partner_name, NULL::TEXT AS preview FROM map_annotations ma""")
            if filter_type in ('all', 'message'):
                parts.append("""SELECT 'message' AS type, sender.username AS username, m.sent_at AS timestamp, NULL::TEXT AS title, NULL::TEXT AS shape_type, NULL::TEXT AS priority, NULL::TEXT AS ann_status, partner.username AS partner_name, LEFT(m.content, 80) AS preview FROM messages m JOIN users sender ON m.sender_id = sender.id JOIN conversations c ON m.conversation_id = c.id JOIN conversation_participants cp ON cp.conversation_id = c.id AND cp.user_id != m.sender_id JOIN users partner ON cp.user_id = partner.id""")
            if not parts: return jsonify([])

            final_sql = f"SELECT * FROM ({' UNION ALL '.join(parts)}) AS activity ORDER BY timestamp DESC LIMIT %s OFFSET %s"
            params += [limit, offset]
            df = pd.read_sql(final_sql, conn, params=params)
            df['timestamp'] = df['timestamp'].apply(lambda x: x.isoformat() if pd.notna(x) and x is not None else None)
            return jsonify(df.replace({float('nan'): None}).to_dict('records'))
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/user/permissions', methods=['GET'])
@api_login_required
def get_permissions():
    return jsonify(get_user_permissions(session.get('role', 'Staff')))

@app.route('/api/user/change-password', methods=['POST'])
@api_login_required
def change_user_password():
    new_password = request.json.get('new_password', '')
    if not new_password or len(new_password) < 6: return jsonify({'success': False, 'message': 'Password must be at least 6 characters'}), 400
    success, message = change_password(session.get('user_id'), new_password)
    return jsonify({'success': success, 'message': message})

@app.route('/api/user/profile', methods=['GET', 'PUT'])
@api_login_required
def user_profile():
    user_id = session.get('user_id')
    if request.method == 'GET':
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, email, full_name, role FROM users WHERE id = %s", (user_id,))
            row = cursor.fetchone()
            if not row: return jsonify({'error': 'User not found'}), 404
            return jsonify({'id': row[0], 'username': row[1], 'email': row[2], 'full_name': row[3], 'role': row[4]})

    data = request.json
    full_name, email = data.get('full_name', '').strip(), data.get('email', '').strip()
    if not full_name or not email: return jsonify({'success': False, 'message': 'Full name and email are required'}), 400
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM users WHERE email = %s AND id != %s", (email, user_id))
            if cursor.fetchone(): return jsonify({'success': False, 'message': 'Email already in use'}), 400
            cursor.execute("UPDATE users SET full_name = %s, email = %s WHERE id = %s", (full_name, email, user_id))
        session['full_name'] = full_name
        return jsonify({'success': True, 'message': 'Profile updated successfully'})
    except Exception as e: return jsonify({'success': False, 'message': str(e)}), 500

def get_or_create_conversation(user_id, other_user_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT cp1.conversation_id FROM conversation_participants cp1
            JOIN conversation_participants cp2 ON cp1.conversation_id = cp2.conversation_id
            JOIN conversations c ON c.id = cp1.conversation_id
            WHERE cp1.user_id = %s AND cp2.user_id = %s AND c.is_group = FALSE
        """, (user_id, other_user_id))
        row = cursor.fetchone()
        if row: return row[0]
        cursor.execute("INSERT INTO conversations (created_by, is_group) VALUES (%s, FALSE) RETURNING id", (user_id,))
        conv_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO conversation_participants (conversation_id, user_id) VALUES (%s, %s), (%s, %s)", (conv_id, user_id, conv_id, other_user_id))
        return conv_id

@app.route('/api/messages/conversations', methods=['GET'])
@api_login_required
def get_conversations():
    user_id = session.get('user_id')
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT c.id, c.title, c.is_group,
                    ARRAY_AGG(u.full_name) FILTER (WHERE u.id != %s) AS member_names,
                    ARRAY_AGG(u.username)  FILTER (WHERE u.id != %s) AS member_usernames,
                    (SELECT content FROM messages WHERE conversation_id = c.id ORDER BY sent_at DESC LIMIT 1) AS last_message,
                    (SELECT sent_at FROM messages WHERE conversation_id = c.id ORDER BY sent_at DESC LIMIT 1) AS last_time,
                    (SELECT COUNT(*) FROM messages m2 WHERE m2.conversation_id = c.id AND m2.sender_id != %s
                     AND NOT EXISTS (SELECT 1 FROM message_reads mr WHERE mr.message_id = m2.id AND mr.user_id = %s)) AS unread_count
                FROM conversations c
                JOIN conversation_participants cp  ON cp.conversation_id  = c.id AND cp.user_id = %s
                JOIN conversation_participants cp2 ON cp2.conversation_id = c.id
                JOIN users u ON u.id = cp2.user_id
                GROUP BY c.id, c.title, c.is_group
                ORDER BY last_time DESC NULLS LAST
            """, (user_id, user_id, user_id, user_id, user_id))
            result = []
            for r in cursor.fetchall():
                display_name = r[1] or 'Group Chat' if r[2] else (r[3][0] if r[3] else 'Unknown')
                result.append({'id': r[0], 'title': display_name, 'is_group': r[2], 'member_names': r[3] or [], 'member_usernames': r[4] or [], 'partner_name': display_name, 'last_message': r[5], 'last_time': r[6].isoformat() if r[6] else None, 'unread_count': int(r[7])})
            return jsonify(result)
    except Exception: return jsonify([])

@app.route('/api/messages/conversation/<int:conv_id>', methods=['GET'])
@api_login_required
def get_conversation_messages(conv_id):
    user_id = session.get('user_id')
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM conversation_participants WHERE conversation_id = %s AND user_id = %s", (conv_id, user_id))
        if not cursor.fetchone(): return jsonify({'error': 'Unauthorized'}), 403
        cursor.execute("""
            INSERT INTO message_reads (message_id, user_id)
            SELECT m.id, %s FROM messages m WHERE m.conversation_id = %s AND m.sender_id != %s AND NOT EXISTS (SELECT 1 FROM message_reads mr WHERE mr.message_id = m.id AND mr.user_id = %s) ON CONFLICT DO NOTHING
        """, (user_id, conv_id, user_id, user_id))
        cursor.execute("SELECT m.id, m.sender_id, u.full_name, m.content, m.sent_at, (m.sender_id = %s) FROM messages m JOIN users u ON u.id = m.sender_id WHERE m.conversation_id = %s ORDER BY m.sent_at ASC", (user_id, conv_id))
        return jsonify([{'id': r[0], 'sender_id': r[1], 'sender_name': r[2], 'content': r[3], 'sent_at': r[4].isoformat(), 'is_mine': r[5]} for r in cursor.fetchall()])

@app.route('/api/messages/send', methods=['POST'])
@api_login_required
def send_message():
    user_id, data = session.get('user_id'), request.json
    conv_id, content = data.get('conversation_id'), data.get('content', '').strip()
    if not conv_id or not content: return jsonify({'success': False}), 400
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM conversation_participants WHERE conversation_id = %s AND user_id = %s", (conv_id, user_id))
        if not cursor.fetchone(): return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        cursor.execute("INSERT INTO messages (conversation_id, sender_id, content) VALUES (%s, %s, %s)", (conv_id, user_id, content))
        cursor.execute("INSERT INTO message_reads (message_id, user_id) SELECT currval('messages_id_seq'), %s ON CONFLICT DO NOTHING", (user_id,))
        return jsonify({'success': True})

@app.route('/api/messages/new', methods=['POST'])
@api_login_required
def start_new_conversation():
    user_id, data = session.get('user_id'), request.json
    recipient_id, content = data.get('recipient_id'), data.get('content', '').strip()
    if not recipient_id or not content or recipient_id == user_id: return jsonify({'success': False}), 400
    conv_id = get_or_create_conversation(user_id, recipient_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO messages (conversation_id, sender_id, content) VALUES (%s, %s, %s)", (conv_id, user_id, content))
        cursor.execute("SELECT full_name FROM users WHERE id = %s", (recipient_id,))
        return jsonify({'success': True, 'conversation_id': conv_id, 'partner_name': cursor.fetchone()[0]})

@app.route('/api/messages/group/new', methods=['POST'])
@api_login_required
def start_group_conversation():
    user_id, data = session.get('user_id'), request.json
    member_ids = data.get('member_ids', [])
    if len(member_ids) < 2: return jsonify({'success': False}), 400
    title = data.get('title', '').strip() or 'Group Chat'
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO conversations (title, created_by, is_group) VALUES (%s, %s, TRUE) RETURNING id", (title, user_id))
        conv_id = cursor.fetchone()[0]
        for uid in list(set([user_id] + member_ids)):
            cursor.execute("INSERT INTO conversation_participants (conversation_id, user_id, is_admin) VALUES (%s, %s, %s)", (conv_id, uid, uid == user_id))
        return jsonify({'success': True, 'conversation_id': conv_id, 'title': title})

@app.route('/api/messages/group/<int:conv_id>/<action>', methods=['POST'])
@api_login_required
def manage_group(conv_id, action):
    user_id, data = session.get('user_id'), request.json or {}
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_group FROM conversations WHERE id = %s", (conv_id,))
        if not cursor.fetchone()[0]: return jsonify({'success': False}), 400
        cursor.execute("SELECT is_admin FROM conversation_participants WHERE conversation_id = %s AND user_id = %s", (conv_id, user_id))
        admin_check = cursor.fetchone()
        if action in ['add', 'remove', 'rename', 'delete'] and not (admin_check and admin_check[0]): return jsonify({'success': False}), 403

        if action == 'leave': cursor.execute("DELETE FROM conversation_participants WHERE conversation_id = %s AND user_id = %s", (conv_id, user_id))
        elif action == 'add': cursor.execute("INSERT INTO conversation_participants (conversation_id, user_id, is_admin) VALUES (%s, %s, FALSE) ON CONFLICT DO NOTHING", (conv_id, data.get('user_id')))
        elif action == 'remove': cursor.execute("DELETE FROM conversation_participants WHERE conversation_id = %s AND user_id = %s", (conv_id, data.get('user_id')))
        elif action == 'rename': cursor.execute("UPDATE conversations SET title = %s WHERE id = %s", (data.get('title'), conv_id))
        elif action == 'delete': cursor.execute("DELETE FROM conversations WHERE id = %s", (conv_id,))
        return jsonify({'success': True})

@app.route('/api/messages/group/<int:conv_id>/members', methods=['GET'])
@api_login_required
def get_group_members(conv_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT u.id, u.full_name, u.username, u.role, cp.is_admin, cp.joined_at FROM conversation_participants cp JOIN users u ON u.id = cp.user_id WHERE cp.conversation_id = %s ORDER BY cp.is_admin DESC, cp.joined_at ASC", (conv_id,))
        return jsonify([{'id': r[0], 'full_name': r[1], 'username': r[2], 'role': r[3], 'is_admin': r[4]} for r in cursor.fetchall()])

@app.route('/api/messages/users', methods=['GET'])
@api_login_required
def get_users_for_messaging():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, full_name, username FROM users WHERE is_active = TRUE ORDER BY full_name")
        return jsonify([{'id': r[0], 'full_name': r[1], 'username': r[2]} for r in cursor.fetchall()])

@app.route('/api/messages/unread-count', methods=['GET'])
@api_login_required
def get_unread_count():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""SELECT COUNT(*) FROM messages m JOIN conversation_participants cp ON cp.conversation_id = m.conversation_id WHERE cp.user_id = %s AND m.sender_id != %s AND NOT EXISTS (SELECT 1 FROM message_reads mr WHERE mr.message_id = m.id AND mr.user_id = %s)""", (session.get('user_id'), session.get('user_id'), session.get('user_id')))
        return jsonify({'count': cursor.fetchone()[0]})

@app.route('/api/reviews', methods=['GET', 'POST'])
@api_login_required
def handle_reviews():
    if request.method == 'GET':
        with get_db_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT id, user_id, username, category, rating, title, body, is_anonymous, created_at, updated_at FROM reviews"
            params = []
            if request.args.get('category'): query += " WHERE category = %s"; params.append(request.args.get('category'))
            cursor.execute(query + " ORDER BY created_at DESC LIMIT %s", params + [int(request.args.get('limit', 50))])
            cols = ['id','user_id','username','category','rating','title','body','is_anonymous','created_at','updated_at']
            result = []
            for row in cursor.fetchall():
                d = dict(zip(cols, row))
                if d['is_anonymous'] and session.get('role') != 'Admin': d['username'] = 'Anonymous'
                d['created_at'] = d['created_at'].isoformat() if d['created_at'] else None
                result.append(d)
            return jsonify(result)

    data = request.get_json()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reviews (user_id, username, category, rating, title, body, is_anonymous)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at
        """, (session['user_id'], session['username'], data.get('category', 'General'), int(data.get('rating', 0)), data.get('title', ''), data.get('body', ''), bool(data.get('is_anonymous', False))))
        row = cursor.fetchone()
    return jsonify({'success': True, 'id': row[0], 'created_at': row[1].isoformat()}), 201

@app.route('/api/reviews/<int:review_id>', methods=['DELETE'])
@api_login_required
def delete_review(review_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM reviews WHERE id = %s", (review_id,))
        row = cursor.fetchone()
        if not row: return jsonify({'error': 'Not found'}), 404
        if row[0] != session['user_id'] and session.get('role') != 'Admin': return jsonify({'error': 'Denied'}), 403
        cursor.execute("DELETE FROM reviews WHERE id = %s", (review_id,))
    return jsonify({'success': True})

# ── Paste these routes into app.py, right after the delete_review route ──────

@app.route('/api/reviews/<int:review_id>/comments', methods=['GET', 'POST'])
@api_login_required
def review_comments(review_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if request.method == 'GET':
            cursor.execute(
                "SELECT id, user_id, username, body, created_at "
                "FROM review_comments WHERE review_id = %s ORDER BY created_at ASC",
                (review_id,)
            )
            cols = ['id', 'user_id', 'username', 'body', 'created_at']
            rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
            for r in rows:
                r['created_at'] = r['created_at'].isoformat() if r['created_at'] else None
            return jsonify(rows)

        # POST – add comment
        data = request.get_json()
        body = (data.get('body') or '').strip()
        if not body:
            return jsonify({'error': 'Comment body required'}), 400
        cursor.execute(
            "INSERT INTO review_comments (review_id, user_id, username, body) "
            "VALUES (%s, %s, %s, %s) RETURNING id, created_at",
            (review_id, session['user_id'], session['username'], body)
        )
        row = cursor.fetchone()
        return jsonify({'success': True, 'id': row[0], 'created_at': row[1].isoformat()}), 201


@app.route('/api/reviews/<int:review_id>/comments/<int:comment_id>', methods=['DELETE'])
@api_login_required
def delete_review_comment(review_id, comment_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM review_comments WHERE id = %s AND review_id = %s",
                       (comment_id, review_id))
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        if row[0] != session['user_id'] and session.get('role') != 'Admin':
            return jsonify({'error': 'Denied'}), 403
        cursor.execute("DELETE FROM review_comments WHERE id = %s", (comment_id,))
    return jsonify({'success': True})


@app.route('/api/reviews/<int:review_id>/react', methods=['POST'])
@api_login_required
def react_review(review_id):
    """Toggle like / dislike. Sending the same reaction again removes it."""
    data     = request.get_json()
    reaction = data.get('reaction')  # 'like' or 'dislike'
    if reaction not in ('like', 'dislike'):
        return jsonify({'error': 'Invalid reaction'}), 400

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, reaction FROM review_reactions WHERE review_id=%s AND user_id=%s",
            (review_id, session['user_id'])
        )
        existing = cursor.fetchone()

        if existing:
            if existing[1] == reaction:          # same → remove (toggle off)
                cursor.execute("DELETE FROM review_reactions WHERE id=%s", (existing[0],))
            else:                                # different → swap
                cursor.execute("UPDATE review_reactions SET reaction=%s WHERE id=%s",
                               (reaction, existing[0]))
        else:
            cursor.execute(
                "INSERT INTO review_reactions (review_id, user_id, reaction) VALUES (%s,%s,%s)",
                (review_id, session['user_id'], reaction)
            )

        # return fresh counts
        cursor.execute(
            "SELECT reaction, COUNT(*) FROM review_reactions WHERE review_id=%s GROUP BY reaction",
            (review_id,)
        )
        counts = {r: 0 for r in ('like', 'dislike')}
        for rec_reaction, cnt in cursor.fetchall():
            counts[rec_reaction] = cnt

        # what is the current user's reaction now?
        cursor.execute(
            "SELECT reaction FROM review_reactions WHERE review_id=%s AND user_id=%s",
            (review_id, session['user_id'])
        )
        mine = cursor.fetchone()
    return jsonify({'success': True, 'likes': counts['like'], 'dislikes': counts['dislike'],
                    'my_reaction': mine[0] if mine else None})


@app.route('/api/reviews/keywords', methods=['GET'])
@api_login_required
def review_keywords():
    """Return the top 20 keywords (excluding stop-words) from all review bodies."""
    import re, collections
    STOP = {
        'the','a','an','and','or','but','in','on','at','to','for','of','with',
        'is','it','its','was','are','be','been','have','has','i','my','we','our',
        'this','that','they','their','you','your','not','no','so','as','by','if',
        'all','can','get','more','very','just','from','about','also','up','do',
        'there','been','will','would','could','should','some','any',
    }
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT body FROM reviews")
        words = []
        for (body,) in cursor.fetchall():
            words += re.findall(r"[a-zA-Z]{3,}", body.lower())
    freq = collections.Counter(w for w in words if w not in STOP)
    top = [{'word': w, 'count': c} for w, c in freq.most_common(20)]
    return jsonify(top)

# ==========================================================
# MAP ANNOTATIONS, NOTES & TASKS API
# ==========================================================

def _compute_representative_point(shape_type, geojson_str, center_lat=None, center_lng=None):
    """
    Calculates the exact (lat, lng) center point for any shape so
    Tasks/Notes can fly to the correct map location.
    """
    try:
        if shape_type in ('circle', 'buffer') and center_lat is not None and center_lng is not None:
            return center_lat, center_lng

        geo = json.loads(geojson_str) if isinstance(geojson_str, str) else geojson_str
        if geo.get('type') == 'FeatureCollection':
            features = geo.get('features', [])
            geo = features[0].get('geometry', {}) if features else {}
        elif geo.get('type') == 'Feature':
            geo = geo.get('geometry', {})

        gtype = geo.get('type', '')
        coords = geo.get('coordinates', [])

        def flatten_coords(c):
            if not c:
                return []
            if isinstance(c[0], (int, float)):
                return [c]
            result = []
            for item in c:
                result.extend(flatten_coords(item))
            return result

        flat = flatten_coords(coords)
        if not flat:
            return None, None

        if gtype == 'Point':
            return flat[0][1], flat[0][0]

        if gtype == 'LineString':
            mid = flat[len(flat) // 2]
            return mid[1], mid[0]

        lngs = [c[0] for c in flat]
        lats = [c[1] for c in flat]
        return sum(lats) / len(lats), sum(lngs) / len(lngs)

    except Exception:
        return None, None


@app.route('/api/annotations', methods=['GET'])
@api_login_required
def get_annotations():
    try:
        status_filter = request.args.get('status', '')
        user_id = session['user_id']

        base_q = """
            SELECT DISTINCT
                a.id, a.title, a.description, a.shape_type, a.geojson,
                a.center_lat, a.center_lng, a.radius_meters,
                a.representative_lat, a.representative_lng,
                a.color, a.fill_color, a.fill_opacity, a.stroke_weight,
                a.created_by, a.created_by_username,
                a.assigned_to, a.assigned_to_username,
                a.status, a.priority,
                a.created_at, a.updated_at,
                a.closed_at, a.days_open,
                (SELECT COUNT(*) FROM annotation_comments c
                 WHERE c.annotation_id = a.id) AS comment_count,
                COALESCE(a.is_rollout_completed_site, FALSE) AS is_rollout_completed_site
            FROM map_annotations a
            LEFT JOIN annotation_assignees aa ON aa.annotation_id = a.id
            WHERE (
                a.created_by = %s OR a.assigned_to = %s OR aa.user_id = %s
                OR COALESCE(a.is_rollout_completed_site, FALSE) = TRUE
            )
        """
        params = [user_id, user_id, user_id]

        if status_filter:
            base_q += " AND a.status = %s"
            params.append(status_filter)

        base_q += " ORDER BY a.created_at DESC"

        cols = [
            'id', 'title', 'description', 'shape_type', 'geojson',
            'center_lat', 'center_lng', 'radius_meters',
            'representative_lat', 'representative_lng',
            'color', 'fill_color', 'fill_opacity', 'stroke_weight',
            'created_by', 'created_by_username',
            'assigned_to', 'assigned_to_username',
            'status', 'priority', 'created_at', 'updated_at',
            'closed_at', 'days_open', 'comment_count',
            'is_rollout_completed_site',
        ]

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(base_q, params)
                rows = cur.fetchall()

                ann_ids = [r[0] for r in rows]
                assignees_map = {}
                if ann_ids:
                    cur.execute("""
                        SELECT aa.annotation_id, u.id, u.username, u.full_name
                        FROM annotation_assignees aa
                        JOIN users u ON u.id = aa.user_id
                        WHERE aa.annotation_id = ANY(%s)
                        ORDER BY aa.annotation_id, u.full_name
                    """, (ann_ids,))
                    for ann_id, uid, uname, fname in cur.fetchall():
                        assignees_map.setdefault(ann_id, []).append({
                            'id': uid, 'username': uname, 'full_name': fname or uname
                        })

        result = []
        for row in rows:
            d = dict(zip(cols, row))
            d['created_at'] = d['created_at'].isoformat() if d['created_at'] else None
            d['updated_at'] = d['updated_at'].isoformat() if d['updated_at'] else None
            d['closed_at']  = d['closed_at'].isoformat()  if d['closed_at']  else None
            d['assignees'] = assignees_map.get(d['id'], [])
            if d['assignees']:
                d['assigned_to_username'] = ', '.join(a['full_name'] for a in d['assignees'])
            result.append(d)

        return jsonify(result)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/annotations', methods=['POST'])
@api_login_required
def create_annotation():
    try:
        data     = request.get_json()
        user_id  = session['user_id']
        username = session['username']

        assigned_ids = data.get('assigned_to_ids') or []
        if not assigned_ids and data.get('assigned_to'):
            assigned_ids = [int(data['assigned_to'])]
        assigned_ids = [int(x) for x in assigned_ids if x]

        assigned_to          = assigned_ids[0] if assigned_ids else None
        assigned_to_username = None

        geojson = data.get('geojson')
        if isinstance(geojson, dict):
            geojson = json.dumps(geojson)

        shape_type = data.get('shape_type', 'polygon')
        rep_lat, rep_lng = _compute_representative_point(
            shape_type, geojson,
            center_lat=data.get('center_lat'),
            center_lng=data.get('center_lng')
        )

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if assigned_to:
                    cur.execute("SELECT username FROM users WHERE id = %s", (assigned_to,))
                    row = cur.fetchone()
                    assigned_to_username = row[0] if row else None

                cur.execute("""
                    INSERT INTO map_annotations
                        (title, description, shape_type, geojson,
                         center_lat, center_lng, radius_meters,
                         representative_lat, representative_lng,
                         color, fill_color, fill_opacity, stroke_weight,
                         created_by, created_by_username,
                         assigned_to, assigned_to_username,
                         status, priority)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id, created_at
                """, (
                    data.get('title', 'Untitled'),
                    data.get('description', ''),
                    shape_type,
                    geojson,
                    data.get('center_lat'),
                    data.get('center_lng'),
                    data.get('radius_meters'),
                    rep_lat,
                    rep_lng,
                    data.get('color', '#2563eb'),
                    data.get('fill_color', '#2563eb'),
                    data.get('fill_opacity', 0.2),
                    data.get('stroke_weight', 2),
                    user_id,
                    username,
                    assigned_to,
                    assigned_to_username,
                    data.get('status', 'open'),
                    data.get('priority', 'normal'),
                ))
                new_id, created_at = cur.fetchone()

                if assigned_ids:
                    for aid in assigned_ids:
                        cur.execute("""
                            INSERT INTO annotation_assignees (annotation_id, user_id)
                            VALUES (%s, %s) ON CONFLICT DO NOTHING
                        """, (new_id, aid))

        return jsonify({
            'id': new_id,
            'created_at': created_at.isoformat(),
            'representative_lat': rep_lat,
            'representative_lng': rep_lng,
        }), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/annotations/<int:ann_id>', methods=['PUT'])
@api_login_required
def update_annotation(ann_id):
    try:
        data = request.get_json()

        assigned_ids = data.get('assigned_to_ids') or []
        if not assigned_ids and data.get('assigned_to'):
            assigned_ids = [int(data['assigned_to'])]
        assigned_ids = [int(x) for x in assigned_ids if x]

        assigned_to          = assigned_ids[0] if assigned_ids else None
        assigned_to_username = None

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT created_by FROM map_annotations WHERE id = %s", (ann_id,))
                row = cur.fetchone()
                if not row:
                    return jsonify({'error': 'Not found'}), 404
                if row[0] != session['user_id'] and session.get('role') != 'Admin':
                    return jsonify({'error': 'Unauthorized'}), 403

                if assigned_to:
                    cur.execute("SELECT username FROM users WHERE id = %s", (assigned_to,))
                    ur = cur.fetchone()
                    assigned_to_username = ur[0] if ur else None

                cur.execute("""
                    UPDATE map_annotations SET
                        title                = %s,
                        description          = %s,
                        assigned_to          = %s,
                        assigned_to_username = %s,
                        status               = %s,
                        priority             = %s,
                        color                = %s,
                        fill_color           = %s
                    WHERE id = %s
                """, (
                    data.get('title'),
                    data.get('description'),
                    assigned_to,
                    assigned_to_username,
                    data.get('status'),
                    data.get('priority'),
                    data.get('color', '#2563eb'),
                    data.get('fill_color', '#2563eb'),
                    ann_id,
                ))

                cur.execute("DELETE FROM annotation_assignees WHERE annotation_id = %s", (ann_id,))
                for aid in assigned_ids:
                    cur.execute("""
                        INSERT INTO annotation_assignees (annotation_id, user_id)
                        VALUES (%s, %s) ON CONFLICT DO NOTHING
                    """, (ann_id, aid))

        return jsonify({'success': True})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/annotations/<int:ann_id>', methods=['DELETE'])
@api_login_required
def delete_annotation(ann_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT created_by FROM map_annotations WHERE id = %s", (ann_id,))
                row = cur.fetchone()
                if not row:
                    return jsonify({'error': 'Not found'}), 404
                if row[0] != session['user_id'] and session.get('role') != 'Admin':
                    return jsonify({'error': 'Unauthorized'}), 403

                cur.execute("DELETE FROM map_annotations WHERE id = %s", (ann_id,))

        return jsonify({'success': True})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/annotations/<int:ann_id>/comments', methods=['GET', 'POST'])
@api_login_required
def handle_annotation_comments(ann_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if request.method == 'GET':
                    cur.execute("""
                        SELECT id, author_id, author_username, body, created_at
                        FROM annotation_comments
                        WHERE annotation_id = %s
                        ORDER BY created_at ASC
                    """, (ann_id,))
                    rows = cur.fetchall()
                    result = [
                        {'id': r[0], 'author_id': r[1], 'author_username': r[2], 'body': r[3], 'created_at': r[4].isoformat()}
                        for r in rows
                    ]
                    return jsonify(result)

                data = request.get_json()
                cur.execute("""
                    INSERT INTO annotation_comments
                        (annotation_id, author_id, author_username, body)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, created_at
                """, (ann_id, session['user_id'], session['username'], data.get('body', '')))
                new_id, created_at = cur.fetchone()
        return jsonify({'id': new_id, 'created_at': created_at.isoformat()}), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/users/list', methods=['GET'])
@api_login_required
def list_users_for_assign():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, full_name, role FROM users WHERE is_active = TRUE ORDER BY full_name")
        return jsonify([{'id': r[0], 'username': r[1], 'full_name': r[2], 'role': r[3]} for r in cursor.fetchall()])

def get_pricing_flat():
    """Returns full pricing (price + min + max) from S3/local JSON for Admin/Planner.
       Normalizes old flat-number format into {price, min, max} if needed."""
    raw = get_pricing()
    normalized = {}
    for category, items in raw.items():
        normalized[category] = {}
        for action_name, vals in items.items():
            if isinstance(vals, dict):
                normalized[category][action_name] = {
                    "price": float(vals.get("price", 0)),
                    "min": float(vals.get("min", 0)),
                    "max": float(vals.get("max", 0))
                }
            else:
                # Old flat-number format: use the number as price, min, and max
                p = float(vals)
                normalized[category][action_name] = {"price": p, "min": p, "max": p}
    return normalized

def get_pricing_for_calc():
    """Returns only flat prices for CAPEX calculation engine."""
    flat = get_pricing_flat()
    return {cat: {name: vals["price"] for name, vals in items.items()} for cat, items in flat.items()}

def get_pricing_ranges():
    """Returns only min/max ranges from S3/local JSON for Staff users."""
    full = get_pricing()
    ranges = {}
    for category, items in full.items():
        ranges[category] = {}
        for action_name, vals in items.items():
            if isinstance(vals, dict) and 'min' in vals and 'max' in vals:
                price_min = float(vals['min'])
                price_max = float(vals['max'])
            else:
                # Fallback: old flat-price format (no min/max), use price as both
                p = float(vals) if not isinstance(vals, dict) else float(vals.get('price', 0))
                price_min = p
                price_max = p
            ranges[category][action_name] = {
                "min": price_min,
                "max": price_max,
                "display": f"RM {price_min:,.2f} \u2013 RM {price_max:,.2f}"
            }
    return ranges

@app.route('/api/pricing', methods=['GET', 'POST'])
@api_login_required
def pricing_endpoint():
    role = session.get('role', 'Staff')
    if request.method == 'POST':
        if role not in ['Admin', 'Planner']:
            return jsonify({'error': 'Unauthorized'}), 403

        new_pricing = request.json

        # 1. Save locally (as a backup cache)
        with open(PRICING_FILE, 'w') as f:
            json.dump(new_pricing, f, indent=4)

        # 2. Push to S3 so AWS Glue uses the new prices on its next run
        try:
            s3_client = aws_session.client('s3')
            s3_client.put_object(
                Bucket='jejak-mappro-demo ',
                Key='capex_pricing/capex_pricing.json',
                Body=json.dumps(new_pricing, indent=4),
                ContentType='application/json'
            )
            return jsonify({"success": True, "message": "Pricing updated successfully and pushed to AWS S3!"})
        except Exception as e:
            print(f"Error uploading pricing to S3: {e}")
            return jsonify({"success": False, "message": f"Saved locally, but failed to sync with AWS: {str(e)}"}), 500

    if role in ['Admin', 'Planner']:
        return jsonify(get_pricing_flat())
    return jsonify(get_pricing_ranges())

# --- ATHENA DATA ENDPOINTS ---
@app.route('/api/years')
def api_years():
    try:
        sql = "SELECT DISTINCT year FROM sector_calculations ORDER BY year DESC"
        df = get_cached_dataframe(sql)
        return jsonify(df['year'].tolist())
    except Exception as e:
        print(f"Athena Error: {e}")
        return jsonify([datetime.now().year])

@app.route('/api/weeks')
def api_weeks():
    try:
        # Fetch all Year and Week combinations globally, perfectly sorted mathematically
        sql = "SELECT DISTINCT CAST(year AS INTEGER) as yr, CAST(week AS INTEGER) as wk FROM sector_calculations ORDER BY yr DESC, wk DESC"
        df = get_cached_dataframe(sql)

        # Return an array of objects to the frontend: [{'year': 2026, 'week': 15}, ...]
        result = [{"year": int(row['yr']), "week": int(row['wk'])} for _, row in df.iterrows()]
        return jsonify(result)
    except Exception as e:
        print(f"Error fetching weeks: {e}")
        return jsonify([])

@app.route('/api/filters/regions')
def api_filters_regions():
    try:
        sql = "SELECT DISTINCT UPPER(region) as reg FROM sector_calculations WHERE region IS NOT NULL ORDER BY UPPER(region)"
        df = get_cached_dataframe(sql)
        return jsonify(df['reg'].tolist())
    except Exception: return jsonify([])

@app.route('/api/superset/guest-token')
@api_login_required
def get_superset_guest_token():
    dashboard_id = request.args.get('dashboard_id')
    if not dashboard_id:
        return jsonify({"error": "Dashboard ID required"}), 400

    try:
        # 1. Authenticate with Superset internally over the Docker network
        login_res = requests.post(
            'http://superset:8088/api/v1/security/login',
            json={"username": "admin", "password": "admin", "provider": "db"}, # Replace with your actual admin password
            timeout=5
        )
        login_res.raise_for_status()
        access_token = login_res.json().get('access_token')

        # 2. Request a temporary Guest Token for the specific dashboard
        guest_token_res = requests.post(
            'http://superset:8088/api/v1/security/guest_token/',
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "user": {
                    "username": session.get('username'),
                    "first_name": "NetAlytics",
                    "last_name": "Admin"
                },
                "resources": [{"type": "dashboard", "id": dashboard_id}],
                "rls": [] # Row Level Security (we can use this later to filter data by region!)
            },
            timeout=5
        )
        guest_token_res.raise_for_status()

        return jsonify({"token": guest_token_res.json().get('token')})

    except Exception as e:
        print(f"Superset Token Error: {e}")
        return jsonify({"error": "Failed to communicate with analytics engine"}), 500

@app.route('/api/dashboard/stats')
def api_dashboard_stats():
    try:
        year = request.args.get('year', str(datetime.now().year))
        week = request.args.get('week', 'All')
        region = request.args.get('region', 'All')
        operator = request.args.get('operator', 'All')
        cluster = request.args.get('cluster', 'All')

        # 1. Build the dynamic WHERE clauses for Athena
        where_sc = f"CAST(year AS VARCHAR) = '{year}'"
        where_ca = f"CAST(year AS VARCHAR) = '{year}' AND congested = TRUE"

        if week != 'All':
            where_sc += f" AND CAST(week AS VARCHAR) = '{week}'"
            where_ca += f" AND CAST(week AS VARCHAR) = '{week}'"
        if region != 'All':
            where_sc += f" AND UPPER(region) = '{region.upper()}'"
            where_ca += f" AND UPPER(region) = '{region.upper()}'"
        if operator != 'All':
            where_sc += f" AND operator = '{operator}'"
            where_ca += f" AND operator = '{operator}'"
        if cluster != 'All':
            where_sc += f" AND cluster = '{cluster}'"
            where_ca += f" AND cluster = '{cluster}'"

        # 2. Push Aggregation to Athena: AWS does the math, Pandas gets 1 row
        sql_sc = f"""
            SELECT 
                COUNT(DISTINCT split_part(zoom_sector_id, '_', 1)) as total_sectors, 
                AVG(eric_data_volume_ul_dl) as avg_vol 
            FROM sector_calculations 
            WHERE {where_sc}
        """
        df_sc = get_cached_dataframe(sql_sc)

        sql_ca = f"""
            SELECT COUNT(DISTINCT zoom_sector_id) as congested_count 
            FROM congestion_analysis 
            WHERE {where_ca}
        """
        df_ca = get_cached_dataframe(sql_ca)

        return jsonify({
            'total_sectors': int(df_sc['total_sectors'].iloc[0]) if not df_sc.empty and pd.notna(df_sc['total_sectors'].iloc[0]) else 0,
            'congested_count': int(df_ca['congested_count'].iloc[0]) if not df_ca.empty and pd.notna(df_ca['congested_count'].iloc[0]) else 0,
            'avg_volume': float(df_sc['avg_vol'].iloc[0]) if not df_sc.empty and pd.notna(df_sc['avg_vol'].iloc[0]) else 0.0
        })
    except Exception as e: 
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# --- FETCH ONCE ARCHITECTURE (No Pagination in SQL) ---
@app.route('/api/sector_data')
def api_sector_data():
    try:
        year = request.args.get('year', str(datetime.now().year))
        start = int(request.args.get('start', 0))
        length = int(request.args.get('length', 25))

        # 1. Columnar Projection: Exactly mapped to your Athena schema
        required_columns = [
            'zoom_sector_id', 'week', 'region', 'cluster', 'ibc_macro', 
            'f1f2f3', 'eric_prb_util_rate', 'eric_dl_user_ip_thpt', 
            'eric_data_volume_ul_dl', 'dataset_type', 'operator', 'area_target'
        ]

        # 2. Partition Pushdown: Prevent S3 from scanning the wrong years
        # (Assumes 'year' is a partition key in your Glue catalog)
        my_partition_filter = lambda x: x["year"] == year

        # 3. Direct Parquet Read (Bypasses Athena Query Costs & CSV RAM Tax)
        df = wr.s3.read_parquet_table(
            database=ATHENA_DATABASE,
            table="sector_calculations",
            columns=required_columns,
            partition_filter=my_partition_filter,
            boto3_session=aws_session,
            use_threads=True
        )

        # 4. Instantly filter and sort in Pandas
        df_filtered = apply_pandas_filters(df, request.args)
        df_filtered = df_filtered.sort_values(by=['zoom_sector_id', 'week'], ascending=[True, False])

        # 5. Slice for DataTables
        total_records = len(df_filtered)
        df_page = df_filtered.iloc[start : start + length]

        # 6. Construct the payload dictionary
        response_payload = {
            'draw': int(request.args.get('draw', 1)),
            'recordsTotal': total_records,
            'recordsFiltered': total_records,
            'data': df_page.replace({np.nan: None}).to_dict('records')
        }

        # 7. Low-Memory Serialization: Serialize to bytes using orjson, return as JSON
        json_bytes = orjson.dumps(response_payload)
        return Response(json_bytes, mimetype='application/json')

    except Exception as e: 
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/forecast_data')
def api_forecast_data():
    try:
        year = request.args.get('year', str(datetime.now().year))
        start = int(request.args.get('start', 0))
        length = int(request.args.get('length', 25))

        # --- 1. COLUMNAR PARQUET READ (Table 1) ---
        sc_cols = ['zoom_sector_id', 'week', 'year', 'ibc_macro', 'dataset_type', 'operator',
                   'region', 'cluster', 'eric_data_volume_ul_dl', 'eric_prb_util_rate', 'eric_dl_user_ip_thpt']
        
        df_sc = wr.s3.read_parquet_table(
            database=ATHENA_DATABASE, table="sector_calculations", columns=sc_cols,
            partition_filter=lambda x: x.get("year", year) == year, boto3_session=aws_session, use_threads=True
        )

        # --- 2. COLUMNAR PARQUET READ (Table 2) ---
        fr_cols = ['zoom_sector_id', 'week', 'year', 'month', 'ibc_macro', 'dataset_type', 'operator',
                   'predicted_eric_data_volume_ul_dl', 'predicted_eric_prb_util_rate', 'predicted_eric_dl_user_ip_thpt', 'congested']
        
        df_fr = wr.s3.read_parquet_table(
            database=ATHENA_DATABASE, table="forecast_results", columns=fr_cols,
            partition_filter=lambda x: x.get("year", year) == year, boto3_session=aws_session, use_threads=True
        )

        # 3. Format SC Data to match Datatable format
        df_sc['month'] = pd.to_numeric(df_sc['week'], errors='coerce') // 4 + 1
        df_sc['actual_data_volume'] = df_sc['eric_data_volume_ul_dl'].round(2).astype(str)
        df_sc['actual_prb_util_rate'] = df_sc['eric_prb_util_rate'].round(2).astype(str)
        df_sc['actual_dl_user_ip_thpt'] = df_sc['eric_dl_user_ip_thpt'].round(2).astype(str)
        df_sc['predicted_eric_data_volume_ul_dl'] = None
        df_sc['predicted_eric_prb_util_rate'] = None
        df_sc['predicted_eric_dl_user_ip_thpt'] = None
        df_sc['congested'] = False

        # 4. Format FR Data
        df_fr['actual_data_volume'] = None
        df_fr['actual_prb_util_rate'] = None
        df_fr['actual_dl_user_ip_thpt'] = None
        df_fr['predicted_eric_data_volume_ul_dl'] = df_fr['predicted_eric_data_volume_ul_dl'].round(2).astype(str)
        df_fr['predicted_eric_prb_util_rate'] = df_fr['predicted_eric_prb_util_rate'].round(2).astype(str)
        df_fr['predicted_eric_dl_user_ip_thpt'] = df_fr['predicted_eric_dl_user_ip_thpt'].round(2).astype(str)

        # Keep only Quarterly weeks for FR
        df_fr = df_fr[df_fr['week'].astype(str).isin(['13', '26', '39', '52'])]

        # 5. Filter SC Data in Pandas (ignore week to keep timeline intact)
        req_args = request.args.to_dict()
        req_args.pop('week', None)
        df_sc_filtered = apply_pandas_filters(df_sc, req_args)

        # 6. Only keep Forecasts for the sectors that survived the SC filter
        valid_sectors = df_sc_filtered['zoom_sector_id'].unique()
        df_fr_filtered = df_fr[df_fr['zoom_sector_id'].isin(valid_sectors)]

        # Combine, sort, and slice
        df_combined = pd.concat([df_sc_filtered, df_fr_filtered], ignore_index=True)
        
        # Ensure year and week are numeric for proper sorting
        df_combined['year_num'] = pd.to_numeric(df_combined['year'], errors='coerce')
        df_combined['week_num'] = pd.to_numeric(df_combined['week'], errors='coerce')
        df_combined = df_combined.sort_values(by=['zoom_sector_id', 'year_num', 'week_num'], ascending=[True, True, True])

        total_records = len(df_combined)
        df_page = df_combined.iloc[start : start + length]

        # --- 3. ORJSON OPTIMIZATION ---
        response_payload = {
            'draw': int(request.args.get('draw', 1)),
            'recordsTotal': total_records,
            'recordsFiltered': total_records,
            'data': df_page.replace({np.nan: None}).to_dict('records')
        }
        
        return Response(orjson.dumps(response_payload), mimetype='application/json')
    except Exception as e: 
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/congestion_data')
def api_congestion_data():
    try:
        year = request.args.get('year', str(datetime.now().year))
        start = int(request.args.get('start', 0))
        length = int(request.args.get('length', 25))

        # --- 1. COLUMNAR PARQUET READ ---
        req_cols = [
            'zoom_sector_id', 'week', 'year', 'month', 'region', 'cluster',
            'eric_data_volume_ul_dl', 'eric_prb_util_rate', 'eric_dl_user_ip_thpt',
            'eric_max_rrc_user', 'max_active_user', 'congested_weeks', 'congested_count_month',
            'operator', 'area_target', 'bau_nic', 'congested'
        ]

        df = wr.s3.read_parquet_table(
            database=ATHENA_DATABASE,
            table="congestion_analysis",
            columns=req_cols,
            partition_filter=lambda x: x.get("year", year) == year,
            boto3_session=aws_session,
            use_threads=True
        )

        # Standard Pandas memory filtering on the lightweight dataframe
        df_filtered = apply_pandas_filters(df, request.args)
        df_filtered = df_filtered[df_filtered['congested'] == True]
        df_filtered = df_filtered.sort_values(by=['congested_weeks', 'eric_prb_util_rate'], ascending=[False, False])

        total_records = len(df_filtered)
        df_page = df_filtered.iloc[start : start + length]

        # --- 2. ORJSON OPTIMIZATION ---
        response_payload = {
            'draw': int(request.args.get('draw', 1)),
            'recordsTotal': total_records,
            'recordsFiltered': total_records,
            'data': df_page.replace({np.nan: None}).to_dict('records')
        }
        
        return Response(orjson.dumps(response_payload), mimetype='application/json')
    except Exception as e: 
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/forecast_by_site')
def api_forecast_by_site():
    """
    Returns forecast_results rows for all sectors of a given site_id,
    for a specific quarterly week snapshot (13, 26, 39, or 52).
    Also returns the latest actual week from sector_calculations for comparison.
    """
    site_id  = request.args.get('site_id', '').strip().upper()
    week     = request.args.get('week', '52')
    year     = request.args.get('year', str(datetime.now().year))

    if not site_id:
        return jsonify({'error': 'site_id required'}), 400

    try:
        # Forecast rows for this site's sectors
        sql_fr = f"""
            SELECT zoom_sector_id, week, year, month, operator,
                   CAST(ROUND(predicted_eric_data_volume_ul_dl, 2) AS DOUBLE) as pred_vol,
                   CAST(ROUND(predicted_eric_prb_util_rate,     2) AS DOUBLE) as pred_prb,
                   CAST(ROUND(predicted_eric_dl_user_ip_thpt,   2) AS DOUBLE) as pred_thpt,
                   congested
            FROM forecast_results
            WHERE UPPER(split_part(zoom_sector_id, '_', 1)) = '{site_id}'
              AND CAST(year AS VARCHAR) = '{year}'
              AND CAST(week AS VARCHAR) = '{week}'
        """
        df_fr = get_cached_dataframe(sql_fr)

        # Latest actual week for this site
        sql_ac = f"""
            SELECT zoom_sector_id, week, year,
                   CAST(eric_data_volume_ul_dl AS DOUBLE) as actual_vol,
                   CAST(eric_prb_util_rate     AS DOUBLE) as actual_prb,
                   CAST(eric_dl_user_ip_thpt   AS DOUBLE) as actual_thpt,
                   congested
            FROM congestion_analysis
            WHERE UPPER(split_part(zoom_sector_id, '_', 1)) = '{site_id}'
              AND CAST(year AS VARCHAR) = '{year}'
              AND week = (
                  SELECT MAX(week) FROM congestion_analysis
                  WHERE UPPER(split_part(zoom_sector_id, '_', 1)) = '{site_id}'
                    AND CAST(year AS VARCHAR) = '{year}'
              )
        """
        df_ac = get_cached_dataframe(sql_ac)

        return jsonify({
            'site_id':  site_id,
            'year':     year,
            'forecast_week': int(week),
            'actual':   df_ac.replace({np.nan: None}).to_dict('records'),
            'forecast': df_fr.replace({np.nan: None}).to_dict('records'),
        })
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/sites')
def api_sites():
    selected_week = request.args.get('week')
    year = request.args.get('year')

    # Fallback to oldest year if 'All' is selected
    if not year or year.lower() == 'all':
        try:
            year_df = get_cached_dataframe("SELECT MIN(year) as start_yr FROM sector_calculations")
            year = str(int(year_df['start_yr'].iloc[0])) if not year_df.empty and pd.notna(year_df['start_yr'].iloc[0]) else "2025"
        except:
            year = "2025"

    region = request.args.get('region', 'All')
    operator = request.args.get('operator', 'All')
    cluster = request.args.get('cluster', 'All')

    try:
        # Static lookups (Safe for RAM)
        df_coords = get_cached_dataframe("SELECT site_id, region, cluster, latitude, longitude FROM site_coordinates WHERE latitude IS NOT NULL")
        df_cov = get_cached_dataframe("SELECT site_id, cell_name as sector_id, azimuth, 65 as beamwidth, coverage_radius_m as radius, technology, 'Unknown' as band FROM site_coverage_params")

        # --- 1. DYNAMIC SQL PUSHDOWN LOGIC ---
        where_clause = f"CAST(ca.year AS VARCHAR) = '{year}'"
        
        if not selected_week or str(selected_week).lower() == 'all':
            week_df = get_cached_dataframe(f"SELECT MAX(CAST(week AS INTEGER)) as max_wk FROM congestion_analysis WHERE year = '{year}'")
            selected_week = str(int(week_df['max_wk'].iloc[0])) if not week_df.empty and pd.notna(week_df['max_wk'].iloc[0]) else "1"
        else:
            selected_week = str(selected_week)

        where_clause += f" AND CAST(ca.week AS VARCHAR) = '{selected_week}'"

        if region != 'All':
            where_clause += f" AND UPPER(ca.region) = '{region.upper()}'"
        if operator != 'All':
            where_clause += f" AND ca.operator = '{operator}'"
        if cluster != 'All':
            where_clause += f" AND ca.cluster = '{cluster}'"

        # Injecting the filters directly into Athena
        sql_cong = f"""
            SELECT 
                UPPER(TRIM(ca.site_id)) as mapped_site_id,
                ca.region, ca.cluster, ca.week,
                ca.zoom_sector_id, ca.eric_prb_util_rate, ca.eric_dl_user_ip_thpt, ca.eric_data_volume_ul_dl,
                GREATEST(COALESCE(ca.eric_max_rrc_user,0), COALESCE(ca.max_active_user,0)) as users,
                ca.congested_weeks, ca.month, ca.congested_count_month, ca.operator, ca.area_target, ca.bau_nic,
                cu.current_f1_l9, cu.current_f1_l18, cu.current_f1_l21, cu.current_f1_l26,
                cu.current_f2_l9, cu.current_f2_l18, cu.current_f2_l21, cu.current_f2_l26
            FROM congestion_analysis ca
            LEFT JOIN capex_upgrades cu 
                ON TRIM(UPPER(ca.zoom_sector_id)) = TRIM(UPPER(cu.zoom_sector_id))
                AND CAST(ca.year AS VARCHAR) = CAST(cu.data_year AS VARCHAR)
                AND CAST(ca.week AS INTEGER) = CAST(cu.data_week AS INTEGER)
            WHERE {where_clause}
        """
        # Returns only the exact rows needed
        df_cong_filtered = get_cached_dataframe(sql_cong)

        # 3. The Mapping Logic
        coords_list = df_coords.to_dict('records')
        cov_list = df_cov.to_dict('records')
        cong_list = df_cong_filtered.to_dict('records')

        sites_map = {}
        for row in coords_list:
            sid = str(row['site_id']).upper()
            # Only add to sites_map if the region/cluster match the UI filters
            if region != 'All' and str(row['region']).upper() != region.upper(): continue
            if cluster != 'All' and str(row['cluster']) != cluster: continue

            sites_map[sid] = {
                'site_id': sid, 'region': row['region'], 'cluster': row['cluster'],
                'lat': float(row['latitude']) if pd.notna(row['latitude']) else 0.0,
                'lng': float(row['longitude']) if pd.notna(row['longitude']) else 0.0,
                'sectors': [], 'coverage': [], 'max_cong_weeks': 0, 'data_week': selected_week,
                'area_target': 'Unknown', 'bau_nic': 'Unknown', 'operator': 'Unknown', 'band_matrix': []
            }

        for row in cov_list:
            sid = str(row['site_id']).upper()
            if sid in sites_map:
                sites_map[sid]['coverage'].append({
                    'sec': row['sector_id'], 'az': float(row['azimuth']) if pd.notna(row['azimuth']) else 0.0,
                    'bw': float(row['beamwidth']) if pd.notna(row['beamwidth']) else 65.0,
                    'rad': float(row['radius']) if pd.notna(row['radius']) else 1000.0,
                    'tech': row['technology'], 'band': row['band']
                })

        for row in cong_list:
            sid = str(row['mapped_site_id']).upper().strip()

            if sid in sites_map:
                sites_map[sid]['sectors'].append({
                    'name': row['zoom_sector_id'],
                    'prb': row['eric_prb_util_rate'],
                    'thpt': row['eric_dl_user_ip_thpt'],
                    'vol': row['eric_data_volume_ul_dl'],
                    'users': row['users'],
                    'month': row['month'],
                    'cong_month_cnt': row['congested_count_month']
                })
                cw = row['congested_weeks']
                sites_map[sid]['max_cong_weeks'] = max(sites_map[sid]['max_cong_weeks'], cw if pd.notna(cw) else 0)
                sites_map[sid]['operator'] = row['operator']
                sites_map[sid]['area_target'] = row['area_target']
                sites_map[sid]['bau_nic'] = row['bau_nic']

                for c in ['f1', 'f2']:
                    for b in ['l9', 'l18', 'l21', 'l26']:
                        val = row.get(f"current_{c}_{b}")
                        if pd.notna(val) and str(val).strip() not in ["0", "", "nan", "None"]:
                            sites_map[sid]['band_matrix'].append({
                                'sector': row['zoom_sector_id'],
                                'f1f2f3': c.upper(), 'band': b.upper(), 'xtxr': str(val).strip()
                            })

        active_sites = [site for site in sites_map.values() if len(site['sectors']) > 0]
                
        # --- 2. ORJSON OPTIMIZATION ---
        return Response(orjson.dumps(active_sites), mimetype='application/json')

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/map/holes')
def get_map_holes():
    week = request.args.get('week', type=int)
    try:
        if week:
            sql = f"SELECT latitude, longitude, signal_strength, cluster_id, serving_cell, data_source FROM coverage_holes_clustered WHERE week = '{week}' LIMIT 10000"
        else:
            sql = "SELECT latitude, longitude, signal_strength, cluster_id, serving_cell, data_source FROM coverage_holes_clustered LIMIT 10000"

        df = get_cached_dataframe(sql)
        features = [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r['longitude'], r['latitude']]},
            "properties": {"signal": r['signal_strength'], "cluster": r['cluster_id'], "serving_cell": r['serving_cell'], "data_source": r['data_source']}
        } for _, r in df.iterrows()]
        return jsonify({"type": "FeatureCollection", "features": features})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/site_ids')
def api_site_ids():
    q = request.args.get('q', '').upper().strip()
    if len(q) < 2: return jsonify([])
    try:
        # Fetch from RAM instead of triggering Athena
        sql = f"SELECT DISTINCT site_id FROM site_coordinates WHERE UPPER(site_id) LIKE '%{q}%' LIMIT 10"
        df = get_cached_dataframe(sql)
        return jsonify(df['site_id'].tolist())
    except Exception as e:
        print(f"Search Error: {e}")
        return jsonify([])

@app.route('/api/map/top_congested')
def api_map_top_congested():
    try:
        # Get year from request, or find the LATEST year as a fallback
        year = request.args.get('year')
        if not year or year.lower() == 'all':
            year_df = get_cached_dataframe("SELECT MAX(year) as max_yr FROM sector_calculations")
            year = str(int(year_df['max_yr'].iloc[0])) if not year_df.empty else "2026"

        week = request.args.get('week', '40')
        region = request.args.get('region', 'All')
        operator = request.args.get('operator', 'All')
        cluster = request.args.get('cluster', 'All')

        # 1. Build the strict WHERE clause
        where_ca = f"CAST(year AS VARCHAR) = '{year}' AND congested = TRUE"
        if week != 'All':
            where_ca += f" AND CAST(week AS VARCHAR) = '{week}'"
        if region != 'All':
            where_ca += f" AND UPPER(region) = '{region.upper()}'"
        if operator != 'All':
            where_ca += f" AND operator = '{operator}'"
        if cluster != 'All':
            where_ca += f" AND cluster = '{cluster}'"

        # 2. Push Sorting and Limiting to Athena
        sql = f"""
            SELECT zoom_sector_id, eric_prb_util_rate as prb, congested_weeks
            FROM congestion_analysis
            WHERE {where_ca}
            ORDER BY congested_weeks DESC, eric_prb_util_rate DESC
            LIMIT 10
        """
        df = get_cached_dataframe(sql)

        return jsonify([{
            "zoom_sector_id": r['zoom_sector_id'],
            "congested_weeks": int(r['congested_weeks']) if pd.notna(r['congested_weeks']) else 0,
            "prb": round(float(r['prb']), 2) if pd.notna(r['prb']) else 0.0
        } for _, r in df.iterrows()])
    except Exception as e:
        print(f"Leaderboard Error: {e}")
        return jsonify([])


@app.route('/api/map/worst_clusters')
def api_map_worst_clusters():
    try:
        # CAST values to DOUBLE inside AVG() to prevent Athena Type Mismatch crashes
        # CAST cluster_id to VARCHAR to safely compare it to '-1'
        sql_mr = """
            SELECT
                cluster_id,
                COUNT(*) as point_count,
                AVG(CAST(signal_strength AS DOUBLE)) as avg_signal,
                AVG(CAST(latitude AS DOUBLE)) as center_lat,
                AVG(CAST(longitude AS DOUBLE)) as center_lon
            FROM coverage_holes_clustered
            WHERE UPPER(TRIM(CAST(data_source AS VARCHAR))) = 'MR'
              AND CAST(cluster_id AS VARCHAR) NOT IN ('-1', '-1.0')
            GROUP BY cluster_id
            ORDER BY point_count DESC LIMIT 10
        """
        df_mr = get_cached_dataframe(sql_mr)

        sql_ookla = """
            SELECT
                cluster_id,
                COUNT(*) as point_count,
                AVG(CAST(signal_strength AS DOUBLE)) as avg_signal,
                AVG(CAST(latitude AS DOUBLE)) as center_lat,
                AVG(CAST(longitude AS DOUBLE)) as center_lon
            FROM coverage_holes_clustered
            WHERE UPPER(TRIM(CAST(data_source AS VARCHAR))) = 'OOKLA'
              AND CAST(cluster_id AS VARCHAR) NOT IN ('-1', '-1.0')
            GROUP BY cluster_id
            ORDER BY point_count DESC LIMIT 10
        """
        df_ookla = get_cached_dataframe(sql_ookla)

        mr_results = df_mr.replace({np.nan: None}).to_dict('records') if not df_mr.empty else []
        ookla_results = df_ookla.replace({np.nan: None}).to_dict('records') if not df_ookla.empty else []

        return jsonify({
            "mr": mr_results,
            "ookla": ookla_results
        })
    except Exception as e:
        print(f"Worst Clusters Error: {e}")
        return jsonify({"mr": [], "ookla": []})

# --- INTERACTIVE FORECAST PLOTTING ---
@app.route('/plot')
def plot_route():
    site_id = request.args.get('site_id')
    forecast_horizon = request.args.get('forecast_horizon', default=52, type=int)
    if not site_id: return jsonify({'error': 'Missing site_id'}), 400

    try:
        METRICS = [
            {'col': 'eric_data_volume_ul_dl', 'title': 'Data Volume (GB)',  'color': '#1f77b4', 'limit': None},
            {'col': 'eric_prb_util_rate',     'title': 'PRB Util (%)',      'color': '#ff7f0e', 'limit': 100},
            {'col': 'eric_dl_user_ip_thpt',   'title': 'Throughput (Mbps)', 'color': '#2ca02c', 'limit': None}
        ]

        sql = f"""
            SELECT zoom_sector_id, week, year, eric_data_volume_ul_dl, eric_prb_util_rate, eric_dl_user_ip_thpt
            FROM sector_calculations WHERE zoom_sector_id LIKE '{site_id.strip()}%' ORDER BY year, week
        """
        df_actual = get_cached_dataframe(sql)

        if df_actual.empty: return jsonify({'error': 'No data found'}), 404

        def get_date(r):
            try: return date.fromisocalendar(int(r['year']), int(r['week']), 1)
            except: return None

        df_actual['plot_date'] = pd.to_datetime(df_actual.apply(get_date, axis=1))
        df_actual = df_actual.dropna(subset=['plot_date'])
        start_date = df_actual['plot_date'].min()

        all_plots = []
        sectors = df_actual['zoom_sector_id'].unique()

        for i, sector in enumerate(sectors):
            df_sec = df_actual[df_actual['zoom_sector_id'] == sector].sort_values('plot_date')
            df_sec['days'] = (df_sec['plot_date'] - start_date).dt.days
            x_raw = df_sec['days'].values.reshape(-1, 1)
            last_day = x_raw.max()
            future_days_col = np.arange(last_day + 7, last_day + (7 * forecast_horizon), 7).reshape(-1, 1)
            future_dates = [start_date + timedelta(days=int(d)) for d in future_days_col.flatten()]

            row_plots = []
            for j, metric in enumerate(METRICS):
                y_raw = df_sec[metric['col']].values
                mask = ~np.isnan(y_raw)
                p = figure(title=f"{sector} - {metric['title']}" if j==1 else (sector if j==0 else metric['title']), x_axis_type="datetime", sizing_mode="stretch_width", height=280, tools="pan,wheel_zoom,reset,save", background_fill_color="#fafafa")

                if np.sum(mask) > 2:
                    x_clean = x_raw[mask]; y_clean = y_raw[mask]; n = len(x_clean)
                    model = LinearRegression(); model.fit(x_clean, y_clean)
                    y_pred = model.predict(future_days_col)
                    x_mean = np.mean(x_clean); y_hat_hist = model.predict(x_clean)
                    residuals = y_clean - y_hat_hist; rss = np.sum(residuals**2); dof = n - 2
                    s_err = np.sqrt(rss / dof); sxx = np.sum((x_clean - x_mean)**2)
                    t_val = t_dist.ppf(0.975, dof)

                    ci_width = [t_val * (s_err * np.sqrt((1/n) + ((d - x_mean)**2 / sxx))) for d in future_days_col.flatten()]
                    y_pred = np.maximum(y_pred, 0)
                    if metric['limit']: y_pred = np.minimum(y_pred, metric['limit'])
                    upper = y_pred + ci_width; lower = np.maximum(y_pred - ci_width, 0)
                    if metric['limit']: upper = np.minimum(upper, metric['limit'])

                    band_x = np.append(future_dates, future_dates[::-1]); band_y = np.append(lower, upper[::-1])
                    p.patch(band_x, band_y, color=metric['color'], alpha=0.15, line_width=0)
                    p.line(future_dates, y_pred, color=metric['color'], line_dash="dashed", line_width=1.5, legend_label="Forecast")

                    source_actual = ColumnDataSource(data=dict(date=df_sec['plot_date'], val=df_sec[metric['col']], week_num=df_sec['week']))
                    p.line('date', 'val', source=source_actual, color=metric['color'], line_width=1.5, legend_label="Actual")
                    c = p.scatter('date', 'val', source=source_actual, color=metric['color'], size=5, marker="circle")
                    p.add_tools(HoverTool(renderers=[c], tooltips=[("Week", "@week_num"), ("Val", "@val{0.2f}")], formatters={'@date': 'datetime'}))

                p.legend.location = "top_left"; p.legend.label_text_font_size = "7pt"
                row_plots.append(p)
            all_plots.append(row_plots)

        grid = gridplot(all_plots, toolbar_location="right", sizing_mode="stretch_width")
        return jsonify({'plot_image': json.dumps(json_item(grid, "myplot"))})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/plot_page')
def plot_page():
    """
    This is the missing HTML page that the AI's iframe tries to load.
    It fetches the raw JSON from the /plot API and renders the Bokeh graph.
    """
    site_id = request.args.get('site_id')
    forecast_horizon = request.args.get('forecast_horizon', 52)

    if not site_id:
        return "Missing site_id", 400

    # A minimal HTML skeleton required to render the Bokeh JSON payload
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <script src="https://cdn.bokeh.org/bokeh/release/bokeh-3.9.0.min.js"></script>
        <script src="https://cdn.bokeh.org/bokeh/release/bokeh-widgets-3.9.0.min.js"></script>
        <script src="https://cdn.bokeh.org/bokeh/release/bokeh-tables-3.9.0.min.js"></script>
        <script src="https://cdn.bokeh.org/bokeh/release/bokeh-api-3.9.0.min.js"></script>
        <style>
            /* FIX: Allow vertical scrolling and remove rigid height limits */
            body {{ margin: 0; padding: 10px; background: white; overflow-y: auto; overflow-x: hidden; }}
            #myplot {{ width: 100%; display: block; }}
        </style>
    </head>
    <body>
        <div id="myplot"></div>
        <script>
            // Fetch the raw math/graph JSON from your existing /plot route
            fetch('/plot?site_id={site_id}&forecast_horizon={forecast_horizon}')
                .then(response => response.json())
                .then(data => {{
                    if (data.error) {{
                        document.getElementById('myplot').innerHTML = "<p style='color:red; padding:20px; font-family:sans-serif; font-weight:bold;'>" + data.error + "</p>";
                    }} else {{
                        // Tell Bokeh to draw the graph inside the 'myplot' div
                        Bokeh.embed.embed_item(JSON.parse(data.plot_image), "myplot");
                    }}
                }})
                .catch(err => console.error('Error rendering graph:', err));
        </script>
    </body>
    </html>
    """
    return html_content

# --- ADMIN PRICING LOGIC (KEPT INTACT) ---
DEFAULT_PRICING = {
    "EQ": {
        "Accelerate NIC": {"price": 65000.00, "min": 50000.00, "max": 80000.00},
        "Add Layer": {"price": 30000.00, "min": 10000.00, "max": 50000.00},
        "Add Sector IBC": {"price": 20000.00, "min": 1000.00, "max": 40000.00},
        "Add Sector Outdoor": {"price": 40000.00, "min": 10000.00, "max": 70000.00},
        "BW Upg": {"price": 25000.00, "min": 1000.00, "max": 50000.00},
        "Bi-Sect Antenna + Accessory": {"price": 15000.00, "min": 1000.00, "max": 30000.00},
        "Bi-Sect Radio": {"price": 35000.00, "min": 10000.00, "max": 60000.00},
        "MM": {"price": 60000.00, "min": 20000.00, "max": 100000.00},
        "NNS": {"price": 290000.00, "min": 80000.00, "max": 500000.00},
        "Split Omni to Sector": {"price": 225000.00, "min": 50000.00, "max": 400000.00},
        "Swap all Sector Radio Ericsson to ZTE": {"price": 275000.00, "min": 50000.00, "max": 500000.00}
    },
    "ES": {
        "Accelerate NIC": {"price": 26000.00, "min": 3000.00, "max": 50000.00},
        "Add Layer": {"price": 32000.00, "min": 5000.00, "max": 60000.00},
        "Add Sector IBC": {"price": 27000.00, "min": 5000.00, "max": 50000.00},
        "Add Sector Outdoor": {"price": 32000.00, "min": 5000.00, "max": 60000.00},
        "BW Upg": {"price": 25000.00, "min": 850.00, "max": 50000.00},
        "Bi-Sect": {"price": 34000.00, "min": 9000.00, "max": 60000.00},
        "Dismantle": {"price": 39000.00, "min": 9510.00, "max": 70000.00},
        "MM": {"price": 35000.00, "min": 9820.00, "max": 60000.00},
        "NNS": {"price": 40000.00, "min": 10000.00, "max": 70000.00},
        "Split Omni to Sector": {"price": 40000.00, "min": 9810.00, "max": 70000.00},
        "Swap all sector radio Ericsson to ZTE": {"price": 41000.00, "min": 9910.00, "max": 72100.00}
    }
}

def get_pricing():
    # Try to fetch the absolute latest pricing from S3 so the Dashboard always matches AWS Glue
    try:
        s3_client = aws_session.client('s3')
        response = s3_client.get_object(Bucket='jejak-mappro-demo', Key='3W-data/capex_pricing/capex_pricing.json')
        return json.loads(response['Body'].read().decode('utf-8'))
    except Exception as e:
        print(f"Could not read pricing from S3, falling back to local/default. Error: {e}")
        if os.path.exists(PRICING_FILE):
            with open(PRICING_FILE, 'r') as f: return json.load(f)
        return DEFAULT_PRICING

def recalculate_live_capex(row, pricing):
    case_str = str(row.get('suggested_upgrade_case', ''))
    if not case_str or case_str.lower() in ['nan', 'none', '']:
        return 0.0, 0.0, 0.0

    # Dynamically count how many layers were added by comparing Current vs Suggested
    added_layers = 0
    for c in ['f1', 'f2']:
        for b in ['l9', 'l18', 'l21', 'l26']:
            curr = str(row.get(f'current_{c}_{b}', '0')).strip().lower()
            sugg = str(row.get(f'suggested_{c}_{b}', '0')).strip().lower()
            if curr in ['0', '', 'none', 'nan', '<na>'] and sugg not in ['0', '', 'none', 'nan', '<na>']:
                added_layers += 1

    eq_prices = pricing.get("EQ", {})
    es_prices = pricing.get("ES", {})

    eq_costs = []
    es_options = []

    # Apply the Engineering layer multiplier
    layer_mult = {1: 1.0, 2: 1.7, 3: 2.7, 4: 3.5, 5: 4.5, 6: 5.5, 7: 6.5, 8: 7.2}.get(added_layers, 1.0) if added_layers > 0 else 0
    add_layer_eq_cost = eq_prices.get("Add Layer", 0) * layer_mult

    case_lower = case_str.lower()

    # Base Cases
    if "case 11" in case_lower:
        eq_costs.append(eq_prices.get("NNS", 0))
        es_options.append(es_prices.get("NNS", 0))
    elif "case 4" in case_lower:
        eq_costs.append(eq_prices.get("MM", 0))
        es_options.append(es_prices.get("MM", 0))
    else:
        if "bandwidth" in case_lower or "case 1 " in case_lower:
            eq_costs.append(eq_prices.get("BW Upg", 0))
            es_options.append(es_prices.get("BW Upg", 0))
        if "layer" in case_lower or "case 3 " in case_lower:
            eq_costs.append(add_layer_eq_cost)
            es_options.append(es_prices.get("Add Layer", 0))
        if "bi-sect" in case_lower or "case 2 " in case_lower:
            eq_costs.extend([eq_prices.get("Bi-Sect Radio", 0), eq_prices.get("Bi-Sect Antenna + Accessory", 0)])
            es_options.append(es_prices.get("Bi-Sect", 0))

    # Add-ons
    if "case 8" in case_lower:
        eq_costs.append(eq_prices.get("Add Sector IBC", 0))
        es_options.append(es_prices.get("Add Sector IBC", 0))
    if "case 9" in case_lower:
        eq_costs.extend([eq_prices.get("Bi-Sect Radio", 0), eq_prices.get("Bi-Sect Antenna + Accessory", 0)])
        es_options.append(es_prices.get("Bi-Sect", 0))
    if "case 10" in case_lower:
        eq_costs.append(eq_prices.get("Accelerate NIC", 0))
        es_options.append(es_prices.get("Accelerate NIC", 0))
    if "case 12" in case_lower:
        eq_costs.append(eq_prices.get("Swap all Sector Radio Ericsson to ZTE", 0))
        es_options.append(es_prices.get("Swap all sector radio Ericsson to ZTE", 0))

    final_eq = sum(eq_costs)
    final_es = max(es_options) if es_options else 0
    return final_eq + final_es, final_eq, final_es

# --- KEEPING YOUR UPGRADE CALCULATION IN EC2 ---
@app.route('/api/map/site_upgrade_details')
def api_site_upgrade_details():
    site_id = request.args.get('site_id')
    week = request.args.get('week')
    year = request.args.get('year', str(datetime.now().year))

    if not site_id: return jsonify({'error': 'No Site ID'}), 400
    if not week or week.lower() == 'all': week = 40

    try:
        sql = f"""
            SELECT
                ca.zoom_sector_id,
                ca.eric_prb_util_rate,
                ca.area_target as sc_area_target,
                cu.suggested_upgrade_case,
                cu.estimated_total_capex_rm,
                cu.eq_capex_rm,
                cu.es_capex_rm,
                cu.projected_prb_pct,
                cu.current_f1_l9, cu.suggested_f1_l9,
                cu.current_f1_l18, cu.suggested_f1_l18,
                cu.current_f1_l21, cu.suggested_f1_l21,
                cu.current_f1_l26, cu.suggested_f1_l26,
                cu.current_f2_l9, cu.suggested_f2_l9,
                cu.current_f2_l18, cu.suggested_f2_l18,
                cu.current_f2_l21, cu.suggested_f2_l21,
                cu.current_f2_l26, cu.suggested_f2_l26
            FROM congestion_analysis ca
            LEFT JOIN capex_upgrades cu
                ON TRIM(UPPER(ca.zoom_sector_id)) = TRIM(UPPER(cu.zoom_sector_id))
                AND CAST(ca.year AS VARCHAR) = CAST(cu.data_year AS VARCHAR)
                AND CAST(ca.week AS INTEGER) = CAST(cu.data_week AS INTEGER)
            WHERE split_part(ca.zoom_sector_id, '_', 1) = '{site_id}'
            AND CAST(ca.year AS VARCHAR) = '{year}'
            AND CAST(ca.week AS INTEGER) = {week}
        """
        df = get_cached_dataframe(sql)

        if df.empty:
            return jsonify({"error": "No sector data found for this week."})

        area_tgt = df['sc_area_target'].iloc[0] if pd.notna(df['sc_area_target'].iloc[0]) else 'Unknown'
        sectors_dict = {}

        # FETCH THE LIVE PRICING FROM YOUR ADMIN PANEL
        # get_pricing_for_calc() returns flat numbers (e.g. {"EQ": {"Add Layer": 5000}, ...})
        # get_pricing() may return dicts like {"price":5000,"min":4000,"max":6000} which
        # would cause "unsupported operand type(s) for *: 'dict' and 'float'" in recalculate_live_capex
        live_pricing = get_pricing_for_calc()

        # Also fetch full pricing with ranges for Staff range display in CAPEX popup
        live_pricing_full = get_pricing_flat()

        for _, row in df.iterrows():
            sec_id = row['zoom_sector_id']
            prb = float(row['eric_prb_util_rate']) if pd.notna(row['eric_prb_util_rate']) else 0.0

            area_str = str(row.get('sc_area_target', '')).lower()
            is_urban = 'urban' in area_str or 'kmc' in area_str
            prb_threshold = 80.0 if is_urban else 92.0

            suggested_case_str = str(row['suggested_upgrade_case']).strip()
            has_upgrade = pd.notna(row['suggested_upgrade_case']) and suggested_case_str.lower() not in ['nan', 'none', '']

            matrix = { "F1": {}, "F2": {}, "F3": {} }
            bands = ['L9', 'L18', 'L21', 'L26']
            carriers = ['F1', 'F2', 'F3']

            for c in carriers:
                for b in bands:
                    matrix[c][b] = {"curr": "-", "sugg": "-"}

            capex_rm = 0.0
            eq_cost = 0.0
            es_cost = 0.0

            if has_upgrade:
                case_label = suggested_case_str

                # --- LIVE RECALCULATION: Override AWS database with your Live Admin Prices ---
                live_total, live_eq, live_es = recalculate_live_capex(row, live_pricing)

                capex_rm = live_total
                eq_cost = live_eq
                es_cost = live_es

                proj_prb = float(row['projected_prb_pct']) if pd.notna(row['projected_prb_pct']) else prb

                for c in carriers:
                    for b in bands:
                        col_curr = f"current_{c.lower()}_{b.lower()}"
                        col_sugg = f"suggested_{c.lower()}_{b.lower()}"

                        c_val = str(row.get(col_curr, "0")).strip()
                        s_val = str(row.get(col_sugg, "0")).strip()

                        if c_val.lower() not in ["0", "0.0", "none", "nan", "", "<na>"]:
                            matrix[c][b]["curr"] = c_val
                        if s_val.lower() not in ["0", "0.0", "none", "nan", "", "<na>"]:
                            matrix[c][b]["sugg"] = s_val
            else:
                proj_prb = prb
                if prb >= prb_threshold:
                    case_label = "MISSING FROM REFERENCE DATA"
                else:
                    case_label = "No Upgrade Needed"

            capex_data = None
            if has_upgrade and capex_rm > 0:
                # Build range info for Staff view from full pricing
                eq_range = None
                es_range = None
                try:
                    # Recalculate using min/max prices for Staff range display
                    min_pricing = {cat: {name: vals["min"] for name, vals in items.items()} for cat, items in live_pricing_full.items()}
                    max_pricing = {cat: {name: vals["max"] for name, vals in items.items()} for cat, items in live_pricing_full.items()}
                    _, min_eq, min_es = recalculate_live_capex(row, min_pricing)
                    _, max_eq, max_es = recalculate_live_capex(row, max_pricing)
                    eq_range = {"min": min_eq, "max": max_eq}
                    es_range = {"min": min_es, "max": max_es}
                except Exception:
                    pass

                capex_data = {
                    "total_capex": capex_rm,
                    "eq_breakdown": [[case_label[:45] + "...", eq_cost, eq_range]],
                    "es_chosen": {"name": "Engineering Services (Highest)", "cost": es_cost, "range": es_range}
                }

            sectors_dict[sec_id] = {
                "is_congested": has_upgrade or (prb >= prb_threshold),
                "capacity_pct": round(proj_prb, 2),
                "case_label": case_label,
                "matrix": matrix,
                "capex": capex_data
            }

        return jsonify({
            "site_id": site_id,
            "area_target": area_tgt,
            "sectors": sectors_dict
        })
    except Exception as e:
        print(f"DEBUG: Internal Error: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

@app.route('/download/cd_file')
def download_cd_file():
    try:
        s3_client = aws_session.client('s3')
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': 'jejak-mappro-demo ', 'Key': '3W-data/processed_network_data/cd-combined-results/CD_Combined_Results.csv'},
            ExpiresIn=3600
        )
        return redirect(presigned_url)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download/sector')
def download_sector():
    try:
        s3_client = aws_session.client('s3')
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': 'jejak-mappro-demo ', 'Key': '3W-data/processed_network_data/cd-combined-results/Sector_Metrics.csv'},
            ExpiresIn=3600
        )
        return redirect(presigned_url)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download/congested')
def download_congested():
    try:
        s3_client = aws_session.client('s3')
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': 'jejak-mappro-demo ', 'Key': '3W-data/processed_network_data/cd-combined-results/Congested_Sectors.csv'},
            ExpiresIn=3600
        )
        return redirect(presigned_url)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat', methods=['POST'])
@login_required
def chat_agent():
    data = request.json
    user_prompt = data.get('message', '').strip()
    week = data.get('week', 'All')

    # FIX: Grab the UI filters so the agent isn't blind
    region = data.get('region', 'All')
    operator = data.get('operator', 'All')
    cluster = data.get('cluster', 'All')

    thread_id = str(session.get('user_id', 'default_session'))

    if not user_prompt:
        return jsonify({"error": "Empty message"}), 400

    print(f"[AI] Routing prompt directly to LangGraph Agent (Thread: {thread_id})...")

    # =========================================================
    # TRIGGER CLAUDE 4 LANGGRAPH AGENT DIRECTLY
    # (Semantic Cache completely bypassed to ensure rich, contextual answers)
    # =========================================================
    try:
        # Pass the UI filters directly into the agent executor
        ai_response = run_netalytics_agent(user_prompt, week, region, operator, cluster, thread_id)

        return jsonify({"reply": ai_response, "cached": False})

    except Exception as e:
        print(f"[AI Error] {e}")
        import traceback
        print(traceback.format_exc())
        return jsonify({"error": "My analytical engine encountered an error. Please try again."}), 500

# =====================================================================
# CCTV PLANNING PIPELINE (runs cctv2.py via PyQGIS processing)
# =====================================================================

@app.route('/api/cctv/run', methods=['POST'])
@login_required
def api_cctv_run():
    """
    Receives 5 input files, runs the CCTV planning pipeline using
    cctv2_pipeline.py (pure Python/Shapely/GeoPandas — no QGIS needed),
    and returns the output layers as GeoJSON for map display.

    Expected form fields (multipart/form-data):
        - building:     GeoJSON file (polygon)
        - parking_area: GeoJSON file (polygon)
        - pole_points:  GeoJSON file (point)
        - camera_table: CSV file (camera_type, hfov_deg, range_m, unit_price_rm)
        - offset_table: CSV file (offset)
    """
    import tempfile
    import shutil

    try:
        tmpdir = tempfile.mkdtemp(prefix='cctv_')
        input_paths = {}

        # Save GeoJSON inputs
        for key in ['building', 'parking_area', 'pole_points']:
            if key not in request.files:
                return jsonify({'error': f'Missing required input: {key}'}), 400
            f = request.files[key]
            path = os.path.join(tmpdir, f'{key}.geojson')
            f.save(path)
            input_paths[key] = path

        # Save CSV inputs
        for key in ['camera_table', 'offset_table']:
            if key not in request.files:
                return jsonify({'error': f'Missing required input: {key}'}), 400
            f = request.files[key]
            path = os.path.join(tmpdir, f'{key}.csv')
            f.save(path)
            input_paths[key] = path

        # Run the pipeline
        from cctv2_pipeline import run_cctv_pipeline

        results = run_cctv_pipeline(
            building_path=input_paths['building'],
            parking_path=input_paths['parking_area'],
            poles_path=input_paths['pole_points'],
            camera_csv_path=input_paths['camera_table'],
            offset_csv_path=input_paths['offset_table'],
        )

        # Cleanup
        shutil.rmtree(tmpdir, ignore_errors=True)

        return jsonify({'status': 'success', 'layers': results})

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
@app.route('/api/genset/route', methods=['POST'])
@login_required
def api_genset_route():
    """
    Receives site coords + substations (fetched by browser via Overpass),
    runs OSMnx road-network routing, returns distances + polylines.
    Body: { lat, lng, substations: [{osm_id, name, lat, lng}, ...] }
    """
    data        = request.get_json(force=True)
    lat         = data.get('lat')
    lng         = data.get('lng')
    substations = data.get('substations', [])
    if lat is None or lng is None:
        return jsonify({'error': 'lat and lng required'}), 400
    if not substations:
        return jsonify({'error': 'No substations provided'}), 400
    try:
        result = route_substations(float(lat), float(lng), substations)
        return jsonify(result)
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/geoserver/config')
@api_login_required
def api_geoserver_config():
    """Layer catalogue for the map UI (same auth as other /api routes)."""
    payload = catalog_payload()
    payload["enabled"] = payload["enabled"] and geoserver_enabled()
    return jsonify(payload)


@app.route('/api/geoserver/wms')
@api_login_required
def api_geoserver_wms_proxy():
    """Authenticated WMS proxy — browsers never touch GeoServer credentials."""
    if not geoserver_enabled():
        return jsonify({"error": "GeoServer integration disabled"}), 404
    return proxy_wms_get(request.query_string.decode())


@app.route('/api/dashboard/embed')
@api_login_required
def get_metabase_embed():
    """Generates a secure JWT token for Metabase dashboard embedding"""
    dashboard_id = request.args.get('dashboard_id', type=int)

    if not dashboard_id:
        return jsonify({"error": "Dashboard ID required"}), 400

    try:
        # Build the payload for the JWT token
        payload = {
            "resource": {"dashboard": dashboard_id},
            "params": {},
            "exp": round(time.time()) + (60 * 10) # 10 minute expiration
        }

        # Sign the token using your Metabase Secret Key
        token = jwt.encode(payload, METABASE_SECRET_KEY, algorithm="HS256")

        # --- THE DYNAMIC IP FIX ---
        # Automatically grab the current IP address the user's browser is using
        current_server_ip = request.host.split(':')[0]

        # Construct the final iframe URL using that exact IP
        iframe_url = f"http://{current_server_ip}:3000/embed/dashboard/{token}#bordered=false&titled=false&theme=night"

        return jsonify({"iframeUrl": iframe_url})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================================================
# ATOM — Automated Telecommunication Opportunity Mapping
# =============================================================================

@app.route('/api/atom/run', methods=['POST'])
@api_login_required
def atom_run():
    """
    Trigger a full ATOM pipeline run.
    Accepts optional JSON body: { "region": "KL", "week": "12" }
    Returns GeoJSON clusters + hull polygons + auto-tuned DBSCAN params.
    """
    data    = request.get_json(silent=True) or {}
    region  = data.get('region', 'All')
    week    = data.get('week')
    username = session.get('username', 'system')

    print(f"[ATOM] Run triggered by '{username}' — region={region}, week={week}")

    try:
        result = run_atom_pipeline(
            region=region,
            week=week,
            initiated_by=username,
        )
        if 'error' in result:
            return jsonify({'success': False, 'error': result['error']}), 400

        return jsonify({'success': True, **result})

    except Exception as e:
        import traceback as tb
        tb.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/atom/history')
@api_login_required
def atom_history():
    """Return the last 10 ATOM runs from PostgreSQL."""
    try:
        runs = get_recent_runs(limit=10)
        return jsonify(runs)
    except Exception as e:
        return jsonify([]), 500


@app.route('/api/nova/run', methods=['POST'])
@api_login_required
def nova_run():
    """
    Trigger NOVA pipeline.
    Body: { "complaint_lat": float, "complaint_lng": float,
            "radius_m": float (default 500), "top_k": int (default 3) }
    Returns ranked candidate NPs + GeoJSON for map rendering.
    """
    data     = request.get_json(silent=True) or {}
    username = session.get('username', 'system')

    try:
        complaint_lat = float(data.get('complaint_lat', 0))
        complaint_lng = float(data.get('complaint_lng', 0))
        radius_m      = float(data.get('radius_m', 500))
        top_k         = int(data.get('top_k', 3))
    except (TypeError, ValueError) as e:
        return jsonify({'success': False, 'error': f'Invalid parameters: {e}'}), 400

    if not (-90 <= complaint_lat <= 90) or not (-180 <= complaint_lng <= 180):
        return jsonify({'success': False, 'error': 'complaint_lat/lng out of range'}), 400

    if radius_m <= 0 or radius_m > 50_000:
        return jsonify({'success': False, 'error': 'radius_m must be 1–50000'}), 400

    print(f"[NOVA] Run triggered by '{username}' — "
          f"({complaint_lat},{complaint_lng}), r={radius_m}m, top_k={top_k}")

    try:
        result = run_nova_pipeline(
            complaint_lat=complaint_lat,
            complaint_lng=complaint_lng,
            radius_m=radius_m,
            top_k=top_k,
            initiated_by=username,
        )
        if 'error' in result:
            return jsonify({'success': False, **result}), 400
        return jsonify({'success': True, **result})
    except Exception as e:
        import traceback as tb
        tb.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/nova/history')
@api_login_required
def nova_history():
    """Return the last 10 NOVA runs from PostgreSQL."""
    try:
        runs = get_nova_recent_runs(limit=10)
        return jsonify(runs)
    except Exception:
        return jsonify([]), 500


@app.route('/api/nova/run/<int:run_id>')
@api_login_required
def nova_run_detail(run_id):
    """Return saved candidates for a past NOVA run."""
    try:
        candidates = get_nova_run_candidates(run_id)
        return jsonify({'success': True, 'run_id': run_id, 'candidates': candidates})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── PAVE routes ───────────────────────────────────────────────────────────────

@app.route('/api/pave/run', methods=['POST'])
@api_login_required
def pave_run():
    """
    Trigger PAVE analysis for a single candidate location.
    Body: {
        candidate_lat: float, candidate_lon: float,
        nova_run_id: int (optional), nova_candidate_label: str (optional)
    }
    Returns viewshed GeoJSON + per-site LOS results + terrain profiles.
    """
    data     = request.get_json(silent=True) or {}
    username = session.get('username', 'system')

    try:
        cand_lat = float(data.get('candidate_lat', 0))
        cand_lon = float(data.get('candidate_lon', 0))
    except (TypeError, ValueError) as e:
        return jsonify({'success': False, 'error': f'Invalid coordinates: {e}'}), 400

    if not (-90 <= cand_lat <= 90) or not (-180 <= cand_lon <= 180):
        return jsonify({'success': False, 'error': 'Coordinates out of range'}), 400

    nova_run_id           = data.get('nova_run_id')
    nova_candidate_label  = data.get('nova_candidate_label')

    print(f"[PAVE] Run triggered by '{username}' — ({cand_lat},{cand_lon}) "
          f"nova={nova_run_id}/{nova_candidate_label}")

    try:
        # Bbox query — only sites within ±0.12° (~13 km) of candidate
        # Uses get_cached_dataframe so repeat runs in same area cost nothing
        pad = 0.12
        sites_sql = f"""
            SELECT CAST(site_id   AS VARCHAR)  AS site_id,
                   CAST(latitude  AS DOUBLE)   AS lat,
                   CAST(longitude AS DOUBLE)   AS lng
            FROM site_coordinates
            WHERE latitude  IS NOT NULL AND longitude IS NOT NULL
              AND CAST(latitude  AS DOUBLE) BETWEEN {cand_lat - pad} AND {cand_lat + pad}
              AND CAST(longitude AS DOUBLE) BETWEEN {cand_lon - pad} AND {cand_lon + pad}
        """
        sites_df = get_cached_dataframe(sites_sql)
        all_sites = sites_df.dropna(subset=['lat', 'lng']).to_dict('records')
        print(f"[PAVE] {len(all_sites)} nearby sites loaded from Athena")

        result = run_pave(
            candidate_lat=cand_lat,
            candidate_lon=cand_lon,
            all_sites=all_sites,
            boto_session=aws_session,
            initiated_by=username,
            nova_run_id=nova_run_id,
            nova_candidate_label=nova_candidate_label,
            fast_mode=True,
        )

        if 'error' in result and not result.get('sites'):
            return jsonify({'success': False, **result}), 400

        return jsonify({'success': True, **result})

    except Exception as e:
        import traceback as tb
        tb.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/pave/history')
@api_login_required
def pave_history():
    """Return the last 10 PAVE runs."""
    try:
        runs = get_pave_recent_runs(limit=10)
        return jsonify(runs)
    except Exception:
        return jsonify([]), 500


@app.route('/api/pave/profile', methods=['POST'])
@api_login_required
def pave_profile():
    """
    Terrain profile for one site pair.
    Reads from DB (pre-computed during PAVE run) — falls back to live S3 calc.
    Body: { candidate_lat, candidate_lon, site_lat, site_lng, run_id (optional) }
    """
    import json as _json
    data = request.get_json(silent=True) or {}
    try:
        cand_lat = float(data['candidate_lat'])
        cand_lon = float(data['candidate_lon'])
        site_lat = float(data['site_lat'])
        site_lng = float(data['site_lng'])
    except (KeyError, TypeError, ValueError) as e:
        return jsonify({'success': False, 'error': f'Invalid params: {e}'}), 400

    run_id = data.get('run_id')

    # ── Fast path: read pre-computed profile from DB ──────────────────────────
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST', 'vibe_db'),
            database=os.getenv('DB_NAME', 'vibe_db'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', '1234'),
            port=os.getenv('DB_PORT', '5432'),
        )
        cur = conn.cursor()
        if run_id:
            cur.execute(
                """SELECT profile_json FROM pave_sites
                   WHERE run_id=%s
                   AND ABS(lat-%s)<0.00005 AND ABS(lng-%s)<0.00005
                   LIMIT 1""",
                (run_id, site_lat, site_lng),
            )
        else:
            cur.execute(
                """SELECT ps.profile_json FROM pave_sites ps
                   JOIN pave_runs pr ON ps.run_id=pr.id
                   WHERE ABS(pr.candidate_lat-%s)<0.00005
                   AND ABS(pr.candidate_lon-%s)<0.00005
                   AND ABS(ps.lat-%s)<0.00005
                   AND ABS(ps.lng-%s)<0.00005
                   ORDER BY pr.ran_at DESC LIMIT 1""",
                (cand_lat, cand_lon, site_lat, site_lng),
            )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and row[0]:
            return jsonify({'success': True, 'profile': _json.loads(row[0]), 'source': 'db'})
    except Exception as db_err:
        print(f"[PAVE profile] DB lookup failed: {db_err}")

    # ── Slow fallback: compute live from S3 DEM ───────────────────────────────
    try:
        from pave_pipeline import get_dem, get_profile_data, SEARCH_R, OBS_H, TGT_H
        dem, tf = get_dem(cand_lat, cand_lon, SEARCH_R, aws_session)
        profile = get_profile_data(dem, tf, cand_lat, cand_lon, OBS_H, site_lat, site_lng, TGT_H)
        return jsonify({'success': True, 'profile': profile, 'source': 's3'})
    except Exception as e:
        import traceback as tb
        tb.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# ROLLOUT MODULE
# =============================================================================

import random as _random
import string as _string

ROLLOUT_CHECKPOINTS_DEF = [
    ('CP/MS-1.0',  'Inputs and triggering',   'Pre-work',        1),
    ('CP/MS-1.1',  'Approvals',               'Pre-work',        2),
    ('CP/MS-1.2',  'Sub-Con selection',        'Pre-work',        3),
    ('CP/MS-2.0',  'Review / Approval TSS',   'Pre-work',        4),
    ('CP/MS-2.1',  'Presentation to MNO',     'Pre-work',        5),
    ('CP/MS-2.2',  'Tenancy agreement',        'Pre-work',        6),
    ('CP/MS-2.3',  'OSA',                      'Pre-work',        7),
    ('CP/MS-2.4',  'PBT approval',             'Pre-work',        8),
    ('CP/MS-2.5',  'Soil test',                'Pre-work',        9),
    ('CP/MS-3.0',  'Foundation',               'Implementation',  10),
    ('CP/MS-3.1',  'Tower erection',           'Implementation',  11),
    ('CP/MS-3.2',  'CME',                      'Implementation',  12),
    ('CP/MS-3.3',  'Power system',             'Implementation',  13),
    ('CP/MS-3.4',  'Backhaul readiness',       'Implementation',  14),
    ('CP/MS-3.5',  'Equipment delivery',       'Implementation',  15),
    ('CP/MS-3.6',  'System integration',       'Implementation',  16),
    ('CP/MS-3.7',  'Final acceptance (FAT)',    'Implementation',  17),
    ('CP/MS-3.8',  'RFS',                      'Implementation',  18),
    ('CP/MS-3.9',  'H/O to operations',        'Implementation',  19),
    ('CP/MS-3.10', 'NOC monitoring',           'Implementation',  20),
]

ROLLOUT_ROLES = [
    'Project Manager', 'USPD Approver', 'State Office Approver',
    'Site Engineer', 'Sub-Con', 'NOC Engineer', 'DUSP Approver', 'Observer',
]

ROLLOUT_UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads', 'rollout')
os.makedirs(ROLLOUT_UPLOAD_FOLDER, exist_ok=True)


def _rollout_gen_id():
    ts  = datetime.now().strftime('%y%m%d')
    rnd = ''.join(_random.choices(_string.ascii_uppercase + _string.digits, k=5))
    return f'NP-{ts}-{rnd}'


def _rollout_log(cur, np_id, event_type, note='', cp_code=None, user_id=None, username=None):
    cur.execute(
        """INSERT INTO rollout_events (np_id, event_type, cp_code, note, user_id, username)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (np_id, event_type, cp_code, note, user_id, username),
    )


def _rollout_seed_checkpoints(cur, np_id):
    for cp_code, activity, phase, seq in ROLLOUT_CHECKPOINTS_DEF:
        cur.execute(
            """INSERT INTO rollout_checkpoints
                   (np_id, cp_code, activity, phase, status, seq_order)
               VALUES (%s,%s,%s,%s,'Pending',%s)
               ON CONFLICT (np_id, cp_code) DO NOTHING""",
            (np_id, cp_code, activity, phase, seq),
        )


def _rollout_create_completion_annotation(cur, np_id, user_id, username):
    """Create a visible map annotation when the rollout reaches Completed (idempotent per plan)."""
    cur.execute(
        """
        SELECT site_name, intended_lat, intended_lon, deployed_lat, deployed_lon,
               nova_run_id, nova_candidate_label, completion_annotation_id
        FROM rollout_plans WHERE np_id=%s FOR UPDATE
        """,
        (np_id,),
    )
    row = cur.fetchone()
    if not row:
        return False
    (
        site_name,
        intended_lat,
        intended_lon,
        deployed_lat,
        deployed_lon,
        nova_run_id,
        nova_candidate_label,
        completion_annotation_id,
    ) = row
    if completion_annotation_id:
        return False
    lat = deployed_lat if deployed_lat is not None else intended_lat
    lng = deployed_lon if deployed_lon is not None else intended_lon
    if lat is None or lng is None:
        return False

    display_name = (site_name or '').strip() or np_id
    title = f'New tower site — {display_name}'
    desc_lines = [
        'Rollout completed — this nominal point is recorded as a new / planned tower site on the network map.',
        f'Rollout ID: {np_id}',
    ]
    if nova_run_id is not None:
        suf = f' (NOVA candidate {nova_candidate_label})' if nova_candidate_label else ''
        desc_lines.append(f'Linked NOVA run #{nova_run_id}{suf}.')
    if deployed_lat is not None and deployed_lon is not None:
        desc_lines.append(
            'Location uses deployed (as-built) coordinates from Rollout.'
        )
    else:
        desc_lines.append('Location uses the intended rollout / candidate coordinates.')
    desc_lines.append(f'Coordinates: {float(lat):.6f}, {float(lng):.6f}')

    gj = {'type': 'Point', 'coordinates': [float(lng), float(lat)]}
    gj_str           = json.dumps(gj)
    uname_safe       = (username or 'system').strip() or 'system'
    accent           = '#059669'
    fill_opacity_pts = 0.92

    ann_created_by = None
    if user_id is not None:
        cur.execute('SELECT 1 FROM users WHERE id = %s', (user_id,))
        if cur.fetchone():
            ann_created_by = user_id
    ann_creator_username = uname_safe
    if user_id is not None and ann_created_by is None:
        ann_creator_username = f'{uname_safe} (rollout; user id {user_id} not in DB)'

    cur.execute(
        """
        INSERT INTO map_annotations (
            title, description, shape_type, geojson,
            center_lat, center_lng, radius_meters,
            representative_lat, representative_lng,
            color, fill_color, fill_opacity, stroke_weight,
            created_by, created_by_username,
            assigned_to, assigned_to_username,
            status, priority, is_rollout_completed_site
        )
        VALUES (%s,%s,'point',%s,%s,%s,NULL,%s,%s,
                %s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s,%s)
        RETURNING id
        """,
        (
            title,
            '\n'.join(desc_lines),
            gj_str,
            lat,
            lng,
            lat,
            lng,
            accent,
            accent,
            fill_opacity_pts,
            3,
            ann_created_by,
            ann_creator_username,
            'resolved',
            'normal',
            True,
        ),
    )
    new_ann_id = cur.fetchone()[0]
    cur.execute(
        """UPDATE rollout_plans SET completion_annotation_id=%s, updated_at=NOW()
           WHERE np_id=%s""",
        (new_ann_id, np_id),
    )
    _rollout_log(
        cur,
        np_id,
        'New site annotation',
        f'Map annotation #{new_ann_id} — new tower site marker.',
        None,
        user_id,
        username,
    )
    return True


@app.route('/api/rollout/plans', methods=['GET'])
@api_login_required
def rollout_list_plans():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT np_id, site_name, trigger_type, trigger_ref,
                           intended_lat, intended_lon, region, zone,
                           current_cp, status, target_date, created_by,
                           nova_run_id, nova_candidate_label,
                           created_at, updated_at
                    FROM rollout_plans ORDER BY created_at DESC
                """)
                cols  = [d[0] for d in cur.description]
                today = __import__('datetime').date.today()
                plans = []
                for r in cur.fetchall():
                    p = dict(zip(cols, r))
                    p['created_at'] = p['created_at'].isoformat() if p['created_at'] else None
                    p['updated_at'] = p['updated_at'].isoformat() if p['updated_at'] else None
                    td = p.get('target_date')
                    p['target_date'] = str(td) if td else None
                    p['overdue'] = bool(td and td < today and p['status'] != 'Completed')
                    plans.append(p)
        return jsonify(plans)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/rollout/plans', methods=['POST'])
@api_login_required
def rollout_create_plan():
    data         = request.get_json() or {}
    site_name    = data.get('site_name', '').strip()
    trigger_type = data.get('trigger_type', 'State Request')
    trigger_ref  = data.get('trigger_ref', '')
    region       = data.get('region', '')
    zone         = data.get('zone', '')
    objective    = data.get('objective', '')
    target_date  = data.get('target_date') or None
    nova_run_id  = data.get('nova_run_id') or None
    nova_label   = data.get('nova_candidate_label') or None
    user_id      = session.get('user_id')
    username     = session.get('username', 'system')

    if not site_name:
        return jsonify({'error': 'site_name required'}), 400
    try:
        lat = float(data.get('intended_lat'))
        lon = float(data.get('intended_lon'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Valid intended_lat and intended_lon required'}), 400

    np_id = _rollout_gen_id()
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO rollout_plans
                        (np_id, site_name, trigger_type, trigger_ref,
                         intended_lat, intended_lon, region, zone, objective,
                         target_date, nova_run_id, nova_candidate_label,
                         current_cp, status, created_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'CP/MS-1.0','Active',%s)
                """, (np_id, site_name, trigger_type, trigger_ref,
                      lat, lon, region, zone, objective,
                      target_date, nova_run_id, nova_label, user_id))
                _rollout_seed_checkpoints(cur, np_id)
                _rollout_log(cur, np_id, 'Plan Created',
                             f'Trigger: {trigger_type}. Location: ({lat:.5f}, {lon:.5f}).',
                             'CP/MS-1.0', user_id, username)
                if user_id:
                    cur.execute("""
                        INSERT INTO rollout_members (np_id, user_id, rollout_role, added_by)
                        VALUES (%s,%s,'Project Manager',%s)
                        ON CONFLICT (np_id, user_id) DO NOTHING
                    """, (np_id, user_id, user_id))
        return jsonify({'np_id': np_id, 'status': 'created'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/rollout/plans/<np_id>', methods=['GET'])
@api_login_required
def rollout_get_plan(np_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM rollout_plans WHERE np_id=%s", (np_id,))
                cols = [d[0] for d in cur.description]
                row  = cur.fetchone()
                if not row:
                    return jsonify({'error': 'Not found'}), 404
                plan = dict(zip(cols, row))
                for k in ('created_at', 'updated_at'):
                    if plan.get(k): plan[k] = plan[k].isoformat()
                if plan.get('target_date'): plan['target_date'] = str(plan['target_date'])

                cur.execute("""
                    SELECT cp_code, activity, phase, status, approved_by,
                           approved_at, rejected_reason, notes, seq_order
                    FROM rollout_checkpoints WHERE np_id=%s ORDER BY seq_order
                """, (np_id,))
                cp_cols = [d[0] for d in cur.description]
                checkpoints = []
                for r in cur.fetchall():
                    cp = dict(zip(cp_cols, r))
                    if cp.get('approved_at'): cp['approved_at'] = cp['approved_at'].isoformat()
                    checkpoints.append(cp)

                cur.execute("""
                    SELECT id, event_type, cp_code, note, username, created_at
                    FROM rollout_events WHERE np_id=%s ORDER BY created_at DESC
                """, (np_id,))
                ev_cols = [d[0] for d in cur.description]
                events = []
                for r in cur.fetchall():
                    ev = dict(zip(ev_cols, r))
                    if ev.get('created_at'): ev['created_at'] = ev['created_at'].isoformat()
                    events.append(ev)

                cur.execute("""
                    SELECT id, cp_code, filename, file_size, mime_type,
                           uploaded_by, uploaded_at, description
                    FROM rollout_documents WHERE np_id=%s ORDER BY uploaded_at DESC
                """, (np_id,))
                doc_cols = [d[0] for d in cur.description]
                docs = []
                for r in cur.fetchall():
                    d = dict(zip(doc_cols, r))
                    if d.get('uploaded_at'): d['uploaded_at'] = d['uploaded_at'].isoformat()
                    docs.append(d)

                cur.execute("""
                    SELECT rm.user_id, u.username, u.full_name, rm.rollout_role, rm.added_at
                    FROM rollout_members rm
                    LEFT JOIN users u ON u.id = rm.user_id
                    WHERE rm.np_id=%s
                    ORDER BY CASE rm.rollout_role WHEN 'Project Manager' THEN 0 ELSE 1 END, rm.added_at
                """, (np_id,))
                mem_cols = [d[0] for d in cur.description]
                members = []
                for r in cur.fetchall():
                    m = dict(zip(mem_cols, r))
                    if m.get('added_at'): m['added_at'] = m['added_at'].isoformat()
                    members.append(m)

        return jsonify({
            'plan': plan, 'checkpoints': checkpoints,
            'events': events, 'documents': docs, 'members': members,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/rollout/plans/<np_id>/checkpoint/<path:cp_code>', methods=['POST'])
@api_login_required
def rollout_update_checkpoint(np_id, cp_code):
    data     = request.get_json() or {}
    action   = data.get('action')   # 'approve' | 'reject' | 'reopen' | 'note'
    notes    = data.get('notes', '')
    reason   = data.get('reason', '')
    user_id  = session.get('user_id')
    username = session.get('username', 'system')

    valid_codes = [c[0] for c in ROLLOUT_CHECKPOINTS_DEF]
    if cp_code not in valid_codes:
        return jsonify({'error': 'Invalid cp_code'}), 400

    created_site_ann = False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if action == 'approve':
                    cur.execute("""
                        UPDATE rollout_checkpoints
                        SET status='Approved', approved_by=%s, approved_at=NOW(), notes=%s
                        WHERE np_id=%s AND cp_code=%s
                    """, (user_id, notes, np_id, cp_code))
                    # advance current_cp to the next pending one
                    cur.execute("""
                        SELECT cp_code FROM rollout_checkpoints
                        WHERE np_id=%s AND status='Pending' ORDER BY seq_order LIMIT 1
                    """, (np_id,))
                    nxt = cur.fetchone()
                    if nxt:
                        cur.execute("UPDATE rollout_plans SET current_cp=%s, updated_at=NOW() WHERE np_id=%s",
                                    (nxt[0], np_id))
                    else:
                        cur.execute("UPDATE rollout_plans SET status='Completed', updated_at=NOW() WHERE np_id=%s",
                                    (np_id,))
                        created_site_ann = _rollout_create_completion_annotation(
                            cur, np_id, user_id, username,
                        )
                    _rollout_log(cur, np_id, 'Checkpoint Approved',
                                 f'{cp_code} approved. {notes}', cp_code, user_id, username)

                elif action == 'reject':
                    cur.execute("""
                        UPDATE rollout_checkpoints
                        SET status='Rejected', rejected_reason=%s WHERE np_id=%s AND cp_code=%s
                    """, (reason, np_id, cp_code))
                    cur.execute("UPDATE rollout_plans SET status='Blocked', updated_at=NOW() WHERE np_id=%s", (np_id,))
                    _rollout_log(cur, np_id, 'Checkpoint Rejected',
                                 f'{cp_code} rejected: {reason}', cp_code, user_id, username)

                elif action == 'reopen':
                    cur.execute("""
                        UPDATE rollout_checkpoints
                        SET status='Pending', rejected_reason=NULL WHERE np_id=%s AND cp_code=%s
                    """, (np_id, cp_code))
                    cur.execute("UPDATE rollout_plans SET status='Active', updated_at=NOW() WHERE np_id=%s", (np_id,))
                    _rollout_log(cur, np_id, 'Checkpoint Reopened',
                                 f'{cp_code} reopened for rework', cp_code, user_id, username)

                elif action == 'note':
                    cur.execute("UPDATE rollout_checkpoints SET notes=%s WHERE np_id=%s AND cp_code=%s",
                                (notes, np_id, cp_code))
                    _rollout_log(cur, np_id, 'Note Added', notes, cp_code, user_id, username)

                else:
                    return jsonify({'error': 'Invalid action'}), 400

        return jsonify({'success': True, 'rollout_annotation_created': created_site_ann})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/rollout/plans/<np_id>/deployment', methods=['POST'])
@api_login_required
def rollout_save_deployment(np_id):
    data     = request.get_json() or {}
    user_id  = session.get('user_id')
    username = session.get('username', 'system')
    try:
        dlat = float(data['deployed_lat'])
        dlon = float(data['deployed_lon'])
    except (KeyError, TypeError, ValueError):
        return jsonify({'error': 'deployed_lat and deployed_lon required'}), 400
    try:
        import math as _math
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT intended_lat, intended_lon FROM rollout_plans WHERE np_id=%s", (np_id,))
                row = cur.fetchone()
                if not row: return jsonify({'error': 'Not found'}), 404
                ilat, ilon = row
                R = 6_371_000.0
                dlat_r = _math.radians(dlat - ilat)
                dlon_r = _math.radians(dlon - ilon)
                a = (_math.sin(dlat_r/2)**2 +
                     _math.cos(_math.radians(ilat)) * _math.cos(_math.radians(dlat)) *
                     _math.sin(dlon_r/2)**2)
                dev_m = round(2 * R * _math.asin(_math.sqrt(a)), 1)
                cur.execute("""
                    UPDATE rollout_plans
                    SET deployed_lat=%s, deployed_lon=%s, deviation_m=%s, updated_at=NOW()
                    WHERE np_id=%s
                """, (dlat, dlon, dev_m, np_id))
                _rollout_log(cur, np_id, 'Deployment Recorded',
                             f'Deployed at ({dlat:.5f},{dlon:.5f}), deviation {dev_m} m',
                             None, user_id, username)
        return jsonify({'success': True, 'deviation_m': dev_m})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/rollout/plans/<np_id>/documents', methods=['POST'])
@api_login_required
def rollout_upload_document(np_id):
    cp_code     = request.form.get('cp_code', '')
    description = request.form.get('description', '')
    user_id     = session.get('user_id')
    username    = session.get('username', 'system')
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['file']
    ext         = os.path.splitext(f.filename)[1]
    stored_name = f'{np_id}_{cp_code}_{_rollout_gen_id()}{ext}'
    stored_path = os.path.join(ROLLOUT_UPLOAD_FOLDER, stored_name)
    f.save(stored_path)
    size = os.path.getsize(stored_path)
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO rollout_documents
                        (np_id, cp_code, filename, stored_path, file_size, mime_type,
                         uploaded_by, description)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """, (np_id, cp_code, f.filename, stored_path, size,
                      f.content_type, user_id, description))
                _rollout_log(cur, np_id, 'Document Uploaded',
                             f'{f.filename} for {cp_code}', cp_code, user_id, username)
        return jsonify({'success': True, 'filename': f.filename})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/rollout/plans/<np_id>/documents/<int:doc_id>', methods=['GET'])
@api_login_required
def rollout_download_document(np_id, doc_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT stored_path, filename FROM rollout_documents WHERE id=%s AND np_id=%s",
                            (doc_id, np_id))
                row = cur.fetchone()
        if not row or not os.path.exists(row[0]):
            return jsonify({'error': 'File not found'}), 404
        return send_file(row[0], as_attachment=True, download_name=row[1])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/rollout/plans/<np_id>/members', methods=['POST'])
@api_login_required
def rollout_add_member(np_id):
    data         = request.get_json() or {}
    target_uid   = data.get('user_id')
    role         = data.get('rollout_role', 'Site Engineer')
    adder_uid    = session.get('user_id')
    adder_name   = session.get('username', 'system')
    if not target_uid:
        return jsonify({'error': 'user_id required'}), 400
    if role not in ROLLOUT_ROLES:
        return jsonify({'error': 'Invalid role'}), 400
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO rollout_members (np_id, user_id, rollout_role, added_by)
                    VALUES (%s,%s,%s,%s)
                    ON CONFLICT (np_id, user_id) DO UPDATE SET rollout_role=EXCLUDED.rollout_role
                """, (np_id, target_uid, role, adder_uid))
                _rollout_log(cur, np_id, 'Member Added',
                             f'User {target_uid} added as {role}', None, adder_uid, adder_name)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/rollout/plans/<np_id>/members/<int:uid>', methods=['DELETE'])
@api_login_required
def rollout_remove_member(np_id, uid):
    adder_uid  = session.get('user_id')
    adder_name = session.get('username', 'system')
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM rollout_members WHERE np_id=%s AND user_id=%s", (np_id, uid))
                _rollout_log(cur, np_id, 'Member Removed',
                             f'User {uid} removed', None, adder_uid, adder_name)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/rollout/plans/<np_id>', methods=['DELETE'])
@api_login_required
def rollout_delete_plan(np_id):
    user_id = session.get('user_id')
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM rollout_plans WHERE np_id=%s", (np_id,))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/rollout/map_pins', methods=['GET'])
@api_login_required
def rollout_map_pins():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT np_id, site_name, status, current_cp,
                           intended_lat, intended_lon,
                           deployed_lat, deployed_lon
                    FROM rollout_plans WHERE intended_lat IS NOT NULL
                """)
                cols = [d[0] for d in cur.description]
                features = []
                for r in cur.fetchall():
                    p = dict(zip(cols, r))
                    lat = p['deployed_lat'] or p['intended_lat']
                    lon = p['deployed_lon'] or p['intended_lon']
                    features.append({
                        'type': 'Feature',
                        'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
                        'properties': {k: p[k] for k in p},
                    })
        return jsonify({'type': 'FeatureCollection', 'features': features})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/rollout/users/search', methods=['GET'])
@api_login_required
def rollout_search_users():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, username, full_name FROM users
                    WHERE username ILIKE %s OR full_name ILIKE %s LIMIT 10
                """, (f'%{q}%', f'%{q}%'))
                return jsonify([{'id': r[0], 'username': r[1], 'full_name': r[2]}
                                for r in cur.fetchall()])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =============================================================================

if __name__ == '__main__':
    app.config.update(
        SESSION_COOKIE_SECURE=False,  # Use HTTPS in production
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        
    )
    app.run(debug=True, host='0.0.0.0', port=5000)
    # In app.py, after creating the app
