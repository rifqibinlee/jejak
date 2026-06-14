import os
import json
import tempfile
import shutil
import traceback
from flask import Blueprint, request, jsonify, session
from app.extensions import api_login_required, get_db_connection
from pipelines.atom   import run_atom_pipeline, get_recent_runs
from pipelines.nova   import run_nova_pipeline, get_nova_recent_runs, get_nova_run_candidates, get_nova_candidates_by_np_id
from pipelines.pave   import run_pave, get_pave_recent_runs
from pipelines.cctv   import run_cctv_pipeline
from pipelines.genset import route_substations
from services.geoserver import catalog_payload, geoserver_enabled, proxy_wms_get

bp = Blueprint('pipelines', __name__)


# ── ATOM ───────────────────────────────────────────────────────────────────────
@bp.route('/api/atom/run', methods=['POST'])
@api_login_required
def atom_run():
    data     = request.get_json(silent=True) or {}
    region   = data.get('region', 'All')
    week     = data.get('week')
    username = session.get('username', 'system')
    print(f"[ATOM] Run triggered by '{username}' — region={region}, week={week}")
    try:
        result = run_atom_pipeline(region=region, week=week, triggered_by=username)
        return jsonify(result)
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/atom/history')
@api_login_required
def atom_history():
    try:
        return jsonify(get_recent_runs())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/atom/clusters')
@api_login_required
def atom_clusters():
    run_id = request.args.get('run_id', type=int)
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if run_id:
                    cur.execute("SELECT cluster_data FROM atom_runs WHERE id = %s", (run_id,))
                else:
                    cur.execute("SELECT cluster_data FROM atom_runs ORDER BY ran_at DESC LIMIT 1")
                row = cur.fetchone()
        if not row:
            return jsonify({'clusters': [], 'hulls': []})
        data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── NOVA ───────────────────────────────────────────────────────────────────────
@bp.route('/api/nova/run', methods=['POST'])
@api_login_required
def nova_run():
    data     = request.get_json(silent=True) or {}
    username = session.get('username', 'system')
    try:
        result = run_nova_pipeline(**data, triggered_by=username)
        return jsonify(result)
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/nova/history')
@api_login_required
def nova_history():
    try:
        return jsonify(get_nova_recent_runs())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/nova/run/<int:run_id>')
@api_login_required
def nova_run_detail(run_id):
    try:
        return jsonify(get_nova_run_candidates(run_id))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/nova/candidates/np/<np_id>')
@api_login_required
def nova_candidates_by_np(np_id):
    try:
        return jsonify(get_nova_candidates_by_np_id(np_id))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── PAVE ───────────────────────────────────────────────────────────────────────
@bp.route('/api/pave/run', methods=['POST'])
@api_login_required
def pave_run():
    data     = request.get_json(silent=True) or {}
    username = session.get('username', 'system')
    try:
        result = run_pave(**data, triggered_by=username)
        return jsonify(result)
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/pave/history')
@api_login_required
def pave_history():
    try:
        return jsonify(get_pave_recent_runs())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/pave/profile', methods=['POST'])
@api_login_required
def pave_profile():
    import json as _json
    import psycopg2
    data = request.get_json(silent=True) or {}
    try:
        cand_lat = float(data['candidate_lat']); cand_lon = float(data['candidate_lon'])
        site_lat = float(data['site_lat']);       site_lng = float(data['site_lng'])
    except (KeyError, TypeError, ValueError) as e:
        return jsonify({'success': False, 'error': f'Invalid params: {e}'}), 400

    run_id = data.get('run_id')
    try:
        conn = psycopg2.connect(host=os.getenv('DB_HOST','vibe_db'), database=os.getenv('DB_NAME','vibe_db'), user=os.getenv('DB_USER','postgres'), password=os.getenv('DB_PASSWORD'), port=os.getenv('DB_PORT','5432'))
        cur  = conn.cursor()
        if run_id:
            cur.execute("SELECT profile_json FROM pave_sites WHERE run_id=%s AND ABS(lat-%s)<0.00005 AND ABS(lng-%s)<0.00005 LIMIT 1", (run_id, site_lat, site_lng))
        else:
            cur.execute("SELECT ps.profile_json FROM pave_sites ps JOIN pave_runs pr ON ps.run_id=pr.id WHERE ABS(pr.candidate_lat-%s)<0.00005 AND ABS(pr.candidate_lon-%s)<0.00005 AND ABS(ps.lat-%s)<0.00005 AND ABS(ps.lng-%s)<0.00005 ORDER BY pr.ran_at DESC LIMIT 1", (cand_lat, cand_lon, site_lat, site_lng))
        row = cur.fetchone(); cur.close(); conn.close()
        if row and row[0]:
            return jsonify({'success': True, 'profile': _json.loads(row[0]), 'source': 'db'})
    except Exception as db_err:
        print(f"[PAVE profile] DB lookup failed: {db_err}")

    try:
        from pave_pipeline import get_dem, get_profile_data, SEARCH_R, OBS_H, TGT_H
        from app.extensions import aws_session as _aws
        dem, tf  = get_dem(cand_lat, cand_lon, SEARCH_R, _aws)
        profile  = get_profile_data(dem, tf, cand_lat, cand_lon, OBS_H, site_lat, site_lng, TGT_H)
        return jsonify({'success': True, 'profile': profile, 'source': 's3'})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


# ── CCTV ───────────────────────────────────────────────────────────────────────
@bp.route('/api/cctv/run', methods=['POST'])
@api_login_required
def api_cctv_run():
    try:
        tmpdir      = tempfile.mkdtemp(prefix='cctv_')
        input_paths = {}
        for key in ['building', 'parking_area', 'pole_points']:
            if key not in request.files:
                return jsonify({'error': f'Missing required input: {key}'}), 400
            f = request.files[key]; path = os.path.join(tmpdir, f'{key}.geojson'); f.save(path); input_paths[key] = path
        for key in ['camera_table', 'offset_table']:
            if key not in request.files:
                return jsonify({'error': f'Missing required input: {key}'}), 400
            f = request.files[key]; path = os.path.join(tmpdir, f'{key}.csv'); f.save(path); input_paths[key] = path

        results = run_cctv_pipeline(
            building_path=input_paths['building'], parking_path=input_paths['parking_area'],
            poles_path=input_paths['pole_points'], camera_csv_path=input_paths['camera_table'],
            offset_csv_path=input_paths['offset_table'],
        )
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({'status': 'success', 'layers': results})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


# ── Genset ─────────────────────────────────────────────────────────────────────
@bp.route('/api/genset/route', methods=['POST'])
@api_login_required
def api_genset_route():
    data        = request.get_json(force=True)
    lat         = data.get('lat')
    lng         = data.get('lng')
    substations = data.get('substations', [])
    if lat is None or lng is None:
        return jsonify({'error': 'lat and lng required'}), 400
    if not substations:
        return jsonify({'error': 'No substations provided'}), 400
    try:
        return jsonify(route_substations(float(lat), float(lng), substations))
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


# ── GeoServer ──────────────────────────────────────────────────────────────────
@bp.route('/api/geoserver/config')
@api_login_required
def api_geoserver_config():
    payload           = catalog_payload()
    payload["enabled"] = payload["enabled"] and geoserver_enabled()
    return jsonify(payload)


@bp.route('/api/geoserver/wms')
@api_login_required
def api_geoserver_wms_proxy():
    if not geoserver_enabled():
        return jsonify({"error": "GeoServer integration disabled"}), 404
    return proxy_wms_get(request.query_string.decode())
