"""
Rollout lifecycle tracker: CP/MS milestone workflow routes.
"""
import os
import json
import string
import random
import math
from flask import Blueprint, request, jsonify, session, send_file
from app.extensions import api_login_required, get_db_connection
from app.config import ROLLOUT_CHECKPOINTS_DEF, ROLLOUT_ROLES, ROLLOUT_UPLOAD_FOLDER

bp = Blueprint('rollout', __name__)


# ── Private helpers ────────────────────────────────────────────────────────────
def _gen_np_id(cur):
    cur.execute("SELECT nextval('np_id_seq')")
    return f"NP{cur.fetchone()[0]:03d}"


def _gen_doc_id():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))


def _log(cur, np_id, event_type, note='', cp_code=None, user_id=None, username=None):
    cur.execute(
        "INSERT INTO rollout_events (np_id, event_type, cp_code, note, user_id, username) VALUES (%s,%s,%s,%s,%s,%s)",
        (np_id, event_type, cp_code, note, user_id, username),
    )


def _seed_checkpoints(cur, np_id):
    for cp_code, activity, phase, seq in ROLLOUT_CHECKPOINTS_DEF:
        cur.execute(
            "INSERT INTO rollout_checkpoints (np_id, cp_code, activity, phase, status, seq_order) "
            "VALUES (%s,%s,%s,%s,'Pending',%s) ON CONFLICT (np_id, cp_code) DO NOTHING",
            (np_id, cp_code, activity, phase, seq),
        )


def _create_completion_annotation(cur, np_id, user_id, username):
    cur.execute(
        "SELECT site_name, intended_lat, intended_lon, deployed_lat, deployed_lon, nova_run_id, nova_candidate_label, completion_annotation_id FROM rollout_plans WHERE np_id=%s FOR UPDATE",
        (np_id,),
    )
    row = cur.fetchone()
    if not row:
        return False
    site_name, intended_lat, intended_lon, deployed_lat, deployed_lon, nova_run_id, nova_label, completion_annotation_id = row
    if completion_annotation_id:
        return False
    lat = deployed_lat if deployed_lat is not None else intended_lat
    lng = deployed_lon if deployed_lon is not None else intended_lon
    if lat is None or lng is None:
        return False

    display_name = (site_name or '').strip() or np_id
    desc_lines   = [
        'Rollout completed — this nominal point is recorded as a new / planned tower site on the network map.',
        f'Rollout ID: {np_id}',
    ]
    if nova_run_id:
        suf = f' (NOVA candidate {nova_label})' if nova_label else ''
        desc_lines.append(f'Linked NOVA run #{nova_run_id}{suf}.')
    if deployed_lat is not None:
        desc_lines.append('Location uses deployed (as-built) coordinates from Rollout.')
    else:
        desc_lines.append('Location uses the intended rollout / candidate coordinates.')
    desc_lines.append(f'Coordinates: {float(lat):.6f}, {float(lng):.6f}')

    gj_str = json.dumps({'type': 'Point', 'coordinates': [float(lng), float(lat)]})
    ann_created_by = None
    if user_id is not None:
        cur.execute('SELECT 1 FROM users WHERE id = %s', (user_id,))
        if cur.fetchone():
            ann_created_by = user_id
    uname_safe = (username or 'system').strip() or 'system'

    cur.execute(
        """INSERT INTO map_annotations
               (title, description, shape_type, geojson,
                center_lat, center_lng, radius_meters,
                representative_lat, representative_lng,
                color, fill_color, fill_opacity, stroke_weight,
                created_by, created_by_username, assigned_to, assigned_to_username,
                status, priority, is_rollout_completed_site)
           VALUES (%s,%s,'point',%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s,%s)
           RETURNING id""",
        (f'New tower site — {display_name}', '\n'.join(desc_lines), gj_str,
         lat, lng, lat, lng, '#059669', '#059669', 0.92, 3,
         ann_created_by, uname_safe, 'resolved', 'normal', True),
    )
    new_ann_id = cur.fetchone()[0]
    cur.execute("UPDATE rollout_plans SET completion_annotation_id=%s, updated_at=NOW() WHERE np_id=%s", (new_ann_id, np_id))
    _log(cur, np_id, 'New site annotation', f'Map annotation #{new_ann_id} — new tower site marker.', None, user_id, username)
    return True


# ── Routes ─────────────────────────────────────────────────────────────────────
@bp.route('/api/rollout/plans', methods=['GET'])
@api_login_required
def rollout_list_plans():
    try:
        import datetime as _dt
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT np_id, site_name, trigger_type, trigger_ref, intended_lat, intended_lon, region, zone, current_cp, status, target_date, created_by, nova_run_id, nova_candidate_label, created_at, updated_at FROM rollout_plans ORDER BY created_at DESC")
                cols  = [d[0] for d in cur.description]
                today = _dt.date.today()
                plans = []
                for r in cur.fetchall():
                    p = dict(zip(cols, r))
                    p['created_at'] = p['created_at'].isoformat() if p['created_at'] else None
                    p['updated_at'] = p['updated_at'].isoformat() if p['updated_at'] else None
                    td = p.get('target_date')
                    p['target_date'] = str(td) if td else None
                    p['overdue']     = bool(td and td < today and p['status'] != 'Completed')
                    plans.append(p)
        return jsonify(plans)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rollout/next_np_id', methods=['GET'])
@api_login_required
def rollout_peek_next_np_id():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT last_value + 1 FROM np_id_seq")
                nxt = cur.fetchone()[0]
        return jsonify({'next_np_id': f'NP{nxt:03d}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rollout/plans', methods=['POST'])
@api_login_required
def rollout_create_plan():
    data            = request.get_json() or {}
    site_name       = data.get('site_name', '').strip()
    user_id         = session.get('user_id')
    username        = session.get('username', 'system')
    supplied_np_id  = (data.get('np_id') or '').strip() or None
    if not site_name:
        return jsonify({'error': 'site_name required'}), 400
    try:
        lat = float(data.get('intended_lat')); lon = float(data.get('intended_lon'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Valid intended_lat and intended_lon required'}), 400
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                np_id = supplied_np_id if supplied_np_id else _gen_np_id(cur)
                cur.execute(
                    "INSERT INTO rollout_plans (np_id, site_name, trigger_type, trigger_ref, intended_lat, intended_lon, region, zone, objective, target_date, nova_run_id, nova_candidate_label, atom_cluster_id, current_cp, status, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'CP/MS-1.0','Active',%s)",
                    (np_id, site_name, data.get('trigger_type','State Request'), data.get('trigger_ref',''), lat, lon,
                     data.get('region',''), data.get('zone',''), data.get('objective',''),
                     data.get('target_date') or None, data.get('nova_run_id') or None,
                     data.get('nova_candidate_label') or None, data.get('atom_cluster_id') or None, user_id),
                )
                _seed_checkpoints(cur, np_id)
                _log(cur, np_id, 'Plan Created', f'Trigger: {data.get("trigger_type","State Request")}. Location: ({lat:.5f}, {lon:.5f}).', 'CP/MS-1.0', user_id, username)
                if user_id:
                    cur.execute("INSERT INTO rollout_members (np_id, user_id, rollout_role, added_by) VALUES (%s,%s,'Project Manager',%s) ON CONFLICT (np_id, user_id) DO NOTHING", (np_id, user_id, user_id))
        return jsonify({'np_id': np_id, 'status': 'created'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rollout/plans/<np_id>', methods=['GET'])
@api_login_required
def rollout_get_plan(np_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM rollout_plans WHERE np_id=%s", (np_id,))
                cols = [d[0] for d in cur.description]; row = cur.fetchone()
                if not row: return jsonify({'error': 'Not found'}), 404
                plan = dict(zip(cols, row))
                for k in ('created_at','updated_at'):
                    if plan.get(k): plan[k] = plan[k].isoformat()
                if plan.get('target_date'): plan['target_date'] = str(plan['target_date'])

                cur.execute("SELECT cp_code, activity, phase, status, approved_by, approved_at, rejected_reason, notes, seq_order FROM rollout_checkpoints WHERE np_id=%s ORDER BY seq_order", (np_id,))
                cp_cols = [d[0] for d in cur.description]
                checkpoints = []
                for r in cur.fetchall():
                    cp = dict(zip(cp_cols, r))
                    if cp.get('approved_at'): cp['approved_at'] = cp['approved_at'].isoformat()
                    checkpoints.append(cp)

                cur.execute("SELECT id, event_type, cp_code, note, username, created_at FROM rollout_events WHERE np_id=%s ORDER BY created_at DESC", (np_id,))
                ev_cols = [d[0] for d in cur.description]
                events  = []
                for r in cur.fetchall():
                    ev = dict(zip(ev_cols, r))
                    if ev.get('created_at'): ev['created_at'] = ev['created_at'].isoformat()
                    events.append(ev)

                cur.execute("SELECT id, cp_code, filename, file_size, mime_type, uploaded_by, uploaded_at, description FROM rollout_documents WHERE np_id=%s ORDER BY uploaded_at DESC", (np_id,))
                doc_cols = [d[0] for d in cur.description]
                docs     = []
                for r in cur.fetchall():
                    d = dict(zip(doc_cols, r))
                    if d.get('uploaded_at'): d['uploaded_at'] = d['uploaded_at'].isoformat()
                    docs.append(d)

                cur.execute("SELECT rm.user_id, u.username, u.full_name, rm.rollout_role, rm.added_at FROM rollout_members rm LEFT JOIN users u ON u.id=rm.user_id WHERE rm.np_id=%s ORDER BY CASE rm.rollout_role WHEN 'Project Manager' THEN 0 ELSE 1 END, rm.added_at", (np_id,))
                mem_cols = [d[0] for d in cur.description]
                members  = []
                for r in cur.fetchall():
                    m = dict(zip(mem_cols, r))
                    if m.get('added_at'): m['added_at'] = m['added_at'].isoformat()
                    members.append(m)

        return jsonify({'plan': plan, 'checkpoints': checkpoints, 'events': events, 'documents': docs, 'members': members})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rollout/plans/<np_id>/checkpoint/<path:cp_code>', methods=['POST'])
@api_login_required
def rollout_update_checkpoint(np_id, cp_code):
    data     = request.get_json() or {}
    action   = data.get('action')
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
                    cur.execute("UPDATE rollout_checkpoints SET status='Approved', approved_by=%s, approved_at=NOW(), notes=%s WHERE np_id=%s AND cp_code=%s", (user_id, notes, np_id, cp_code))
                    cur.execute("SELECT cp_code FROM rollout_checkpoints WHERE np_id=%s AND status='Pending' ORDER BY seq_order LIMIT 1", (np_id,))
                    nxt = cur.fetchone()
                    if nxt:
                        cur.execute("UPDATE rollout_plans SET current_cp=%s, updated_at=NOW() WHERE np_id=%s", (nxt[0], np_id))
                    else:
                        cur.execute("UPDATE rollout_plans SET status='Completed', updated_at=NOW() WHERE np_id=%s", (np_id,))
                        created_site_ann = _create_completion_annotation(cur, np_id, user_id, username)
                    _log(cur, np_id, 'Checkpoint Approved', f'{cp_code} approved. {notes}', cp_code, user_id, username)
                elif action == 'reject':
                    cur.execute("UPDATE rollout_checkpoints SET status='Rejected', rejected_reason=%s WHERE np_id=%s AND cp_code=%s", (reason, np_id, cp_code))
                    cur.execute("UPDATE rollout_plans SET status='Blocked', updated_at=NOW() WHERE np_id=%s", (np_id,))
                    _log(cur, np_id, 'Checkpoint Rejected', f'{cp_code} rejected: {reason}', cp_code, user_id, username)
                elif action == 'reopen':
                    cur.execute("UPDATE rollout_checkpoints SET status='Pending', rejected_reason=NULL WHERE np_id=%s AND cp_code=%s", (np_id, cp_code))
                    cur.execute("UPDATE rollout_plans SET status='Active', updated_at=NOW() WHERE np_id=%s", (np_id,))
                    _log(cur, np_id, 'Checkpoint Reopened', f'{cp_code} reopened for rework', cp_code, user_id, username)
                elif action == 'note':
                    cur.execute("UPDATE rollout_checkpoints SET notes=%s WHERE np_id=%s AND cp_code=%s", (notes, np_id, cp_code))
                    _log(cur, np_id, 'Note Added', notes, cp_code, user_id, username)
                else:
                    return jsonify({'error': 'Invalid action'}), 400
        return jsonify({'success': True, 'rollout_annotation_created': created_site_ann})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rollout/plans/<np_id>/deployment', methods=['POST'])
@api_login_required
def rollout_save_deployment(np_id):
    data    = request.get_json() or {}
    user_id = session.get('user_id'); username = session.get('username', 'system')
    try:
        dlat = float(data['deployed_lat']); dlon = float(data['deployed_lon'])
    except (KeyError, TypeError, ValueError):
        return jsonify({'error': 'deployed_lat and deployed_lon required'}), 400
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT intended_lat, intended_lon FROM rollout_plans WHERE np_id=%s", (np_id,))
                row = cur.fetchone()
                if not row: return jsonify({'error': 'Not found'}), 404
                ilat, ilon = row
                R = 6_371_000.0
                dlat_r = math.radians(dlat - ilat); dlon_r = math.radians(dlon - ilon)
                a = math.sin(dlat_r/2)**2 + math.cos(math.radians(ilat)) * math.cos(math.radians(dlat)) * math.sin(dlon_r/2)**2
                dev_m = round(2 * R * math.asin(math.sqrt(a)), 1)
                cur.execute("UPDATE rollout_plans SET deployed_lat=%s, deployed_lon=%s, deviation_m=%s, updated_at=NOW() WHERE np_id=%s", (dlat, dlon, dev_m, np_id))
                _log(cur, np_id, 'Deployment Recorded', f'Deployed at ({dlat:.5f},{dlon:.5f}), deviation {dev_m} m', None, user_id, username)
        return jsonify({'success': True, 'deviation_m': dev_m})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rollout/plans/<np_id>/documents', methods=['POST'])
@api_login_required
def rollout_upload_document(np_id):
    cp_code = request.form.get('cp_code', ''); description = request.form.get('description', '')
    user_id = session.get('user_id'); username = session.get('username', 'system')
    if 'file' not in request.files: return jsonify({'error': 'No file'}), 400
    f = request.files['file']
    ext         = os.path.splitext(f.filename)[1]
    stored_name = f'{np_id}_{cp_code}_{_gen_doc_id()}{ext}'
    stored_path = os.path.join(ROLLOUT_UPLOAD_FOLDER, stored_name)
    f.save(stored_path)
    size = os.path.getsize(stored_path)
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO rollout_documents (np_id, cp_code, filename, stored_path, file_size, mime_type, uploaded_by, description) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                            (np_id, cp_code, f.filename, stored_path, size, f.content_type, user_id, description))
                _log(cur, np_id, 'Document Uploaded', f'{f.filename} for {cp_code}', cp_code, user_id, username)
        return jsonify({'success': True, 'filename': f.filename})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rollout/plans/<np_id>/documents/<int:doc_id>', methods=['GET'])
@api_login_required
def rollout_download_document(np_id, doc_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT stored_path, filename FROM rollout_documents WHERE id=%s AND np_id=%s", (doc_id, np_id))
                row = cur.fetchone()
        if not row or not os.path.exists(row[0]): return jsonify({'error': 'File not found'}), 404
        return send_file(row[0], as_attachment=True, download_name=row[1])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rollout/plans/<np_id>/members', methods=['POST'])
@api_login_required
def rollout_add_member(np_id):
    data       = request.get_json() or {}
    target_uid = data.get('user_id'); role = data.get('rollout_role', 'Site Engineer')
    adder_uid  = session.get('user_id'); adder_name = session.get('username', 'system')
    if not target_uid: return jsonify({'error': 'user_id required'}), 400
    if role not in ROLLOUT_ROLES: return jsonify({'error': 'Invalid role'}), 400
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO rollout_members (np_id, user_id, rollout_role, added_by) VALUES (%s,%s,%s,%s) ON CONFLICT (np_id, user_id) DO UPDATE SET rollout_role=EXCLUDED.rollout_role", (np_id, target_uid, role, adder_uid))
                _log(cur, np_id, 'Member Added', f'User {target_uid} added as {role}', None, adder_uid, adder_name)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rollout/plans/<np_id>/members/<int:uid>', methods=['DELETE'])
@api_login_required
def rollout_remove_member(np_id, uid):
    adder_uid = session.get('user_id'); adder_name = session.get('username', 'system')
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM rollout_members WHERE np_id=%s AND user_id=%s", (np_id, uid))
                _log(cur, np_id, 'Member Removed', f'User {uid} removed', None, adder_uid, adder_name)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rollout/plans/<np_id>', methods=['DELETE'])
@api_login_required
def rollout_delete_plan(np_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM rollout_plans WHERE np_id=%s", (np_id,))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rollout/map_pins', methods=['GET'])
@api_login_required
def rollout_map_pins():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT np_id, site_name, status, current_cp, intended_lat, intended_lon, deployed_lat, deployed_lon FROM rollout_plans WHERE intended_lat IS NOT NULL")
                cols = [d[0] for d in cur.description]; features = []
                for r in cur.fetchall():
                    p = dict(zip(cols, r))
                    lat = p['deployed_lat'] or p['intended_lat']; lon = p['deployed_lon'] or p['intended_lon']
                    features.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [lon, lat]}, 'properties': {k: p[k] for k in p}})
        return jsonify({'type': 'FeatureCollection', 'features': features})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/rollout/users/search', methods=['GET'])
@api_login_required
def rollout_search_users():
    q = request.args.get('q', '').strip()
    if len(q) < 2: return jsonify([])
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, username, full_name FROM users WHERE username ILIKE %s OR full_name ILIKE %s LIMIT 10", (f'%{q}%', f'%{q}%'))
                return jsonify([{'id': r[0], 'username': r[1], 'full_name': r[2]} for r in cur.fetchall()])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
