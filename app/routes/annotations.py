import json
from flask import Blueprint, request, jsonify, session
from app.extensions import api_login_required, get_db_connection

bp = Blueprint('annotations', __name__)


def _compute_representative_point(shape_type, geojson_str, center_lat=None, center_lng=None):
    """Calculates the exact (lat, lng) center point for any shape."""
    try:
        if shape_type in ('circle', 'buffer') and center_lat is not None and center_lng is not None:
            return center_lat, center_lng

        geo = json.loads(geojson_str) if isinstance(geojson_str, str) else geojson_str
        if geo.get('type') == 'FeatureCollection':
            features = geo.get('features', [])
            geo = features[0].get('geometry', {}) if features else {}
        elif geo.get('type') == 'Feature':
            geo = geo.get('geometry', {})

        gtype  = geo.get('type', '')
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


@bp.route('/api/annotations', methods=['GET'])
@api_login_required
def get_annotations():
    try:
        status_filter = request.args.get('status', '')
        user_id       = session['user_id']

        base_q = """
            SELECT DISTINCT
                a.id, a.title, a.description, a.shape_type, a.geojson,
                a.center_lat, a.center_lng, a.radius_meters,
                a.representative_lat, a.representative_lng,
                a.color, a.fill_color, a.fill_opacity, a.stroke_weight,
                a.created_by, a.created_by_username,
                a.assigned_to, a.assigned_to_username,
                a.status, a.priority, a.created_at, a.updated_at,
                a.closed_at, a.days_open,
                (SELECT COUNT(*) FROM annotation_comments c WHERE c.annotation_id = a.id) AS comment_count,
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
            'id','title','description','shape_type','geojson',
            'center_lat','center_lng','radius_meters',
            'representative_lat','representative_lng',
            'color','fill_color','fill_opacity','stroke_weight',
            'created_by','created_by_username','assigned_to','assigned_to_username',
            'status','priority','created_at','updated_at','closed_at','days_open',
            'comment_count','is_rollout_completed_site',
        ]

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(base_q, params)
                rows    = cur.fetchall()
                ann_ids = [r[0] for r in rows]
                assignees_map = {}
                if ann_ids:
                    cur.execute("""
                        SELECT aa.annotation_id, u.id, u.username, u.full_name
                        FROM annotation_assignees aa JOIN users u ON u.id = aa.user_id
                        WHERE aa.annotation_id = ANY(%s) ORDER BY aa.annotation_id, u.full_name
                    """, (ann_ids,))
                    for ann_id, uid, uname, fname in cur.fetchall():
                        assignees_map.setdefault(ann_id, []).append({'id': uid, 'username': uname, 'full_name': fname or uname})

        result = []
        for row in rows:
            d = dict(zip(cols, row))
            d['created_at'] = d['created_at'].isoformat() if d['created_at'] else None
            d['updated_at'] = d['updated_at'].isoformat() if d['updated_at'] else None
            d['closed_at']  = d['closed_at'].isoformat()  if d['closed_at']  else None
            d['assignees']  = assignees_map.get(d['id'], [])
            if d['assignees']:
                d['assigned_to_username'] = ', '.join(a['full_name'] for a in d['assignees'])
            result.append(d)

        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/annotations', methods=['POST'])
@api_login_required
def create_annotation():
    try:
        data         = request.get_json()
        user_id      = session['user_id']
        username     = session['username']
        assigned_ids = data.get('assigned_to_ids') or []
        if not assigned_ids and data.get('assigned_to'):
            assigned_ids = [int(data['assigned_to'])]
        assigned_ids = [int(x) for x in assigned_ids if x]
        assigned_to  = assigned_ids[0] if assigned_ids else None

        geojson = data.get('geojson')
        if isinstance(geojson, dict):
            geojson = json.dumps(geojson)

        shape_type           = data.get('shape_type', 'polygon')
        rep_lat, rep_lng     = _compute_representative_point(shape_type, geojson, data.get('center_lat'), data.get('center_lng'))
        assigned_to_username = None

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
                         assigned_to, assigned_to_username, status, priority)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id, created_at
                """, (
                    data.get('title', 'Untitled'), data.get('description', ''), shape_type, geojson,
                    data.get('center_lat'), data.get('center_lng'), data.get('radius_meters'),
                    rep_lat, rep_lng,
                    data.get('color', '#2563eb'), data.get('fill_color', '#2563eb'),
                    data.get('fill_opacity', 0.2), data.get('stroke_weight', 2),
                    user_id, username, assigned_to, assigned_to_username,
                    data.get('status', 'open'), data.get('priority', 'normal'),
                ))
                new_id, created_at = cur.fetchone()
                for aid in assigned_ids:
                    cur.execute("INSERT INTO annotation_assignees (annotation_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (new_id, aid))

        return jsonify({'id': new_id, 'created_at': created_at.isoformat(), 'representative_lat': rep_lat, 'representative_lng': rep_lng}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/annotations/<int:ann_id>', methods=['PUT'])
@api_login_required
def update_annotation(ann_id):
    try:
        data         = request.get_json()
        assigned_ids = data.get('assigned_to_ids') or []
        if not assigned_ids and data.get('assigned_to'):
            assigned_ids = [int(data['assigned_to'])]
        assigned_ids         = [int(x) for x in assigned_ids if x]
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
                        title=%s, description=%s, assigned_to=%s, assigned_to_username=%s,
                        status=%s, priority=%s, color=%s, fill_color=%s
                    WHERE id=%s
                """, (data.get('title'), data.get('description'), assigned_to, assigned_to_username,
                      data.get('status'), data.get('priority'),
                      data.get('color', '#2563eb'), data.get('fill_color', '#2563eb'), ann_id))
                cur.execute("DELETE FROM annotation_assignees WHERE annotation_id = %s", (ann_id,))
                for aid in assigned_ids:
                    cur.execute("INSERT INTO annotation_assignees (annotation_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (ann_id, aid))

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/annotations/<int:ann_id>', methods=['DELETE'])
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


@bp.route('/api/annotations/<int:ann_id>/comments', methods=['GET', 'POST'])
@api_login_required
def handle_annotation_comments(ann_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if request.method == 'GET':
                    cur.execute("SELECT id, author_id, author_username, body, created_at FROM annotation_comments WHERE annotation_id = %s ORDER BY created_at ASC", (ann_id,))
                    return jsonify([{'id': r[0], 'author_id': r[1], 'author_username': r[2], 'body': r[3], 'created_at': r[4].isoformat()} for r in cur.fetchall()])
                data = request.get_json()
                cur.execute("INSERT INTO annotation_comments (annotation_id, author_id, author_username, body) VALUES (%s,%s,%s,%s) RETURNING id, created_at",
                            (ann_id, session['user_id'], session['username'], data.get('body', '')))
                new_id, created_at = cur.fetchone()
        return jsonify({'id': new_id, 'created_at': created_at.isoformat()}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500
