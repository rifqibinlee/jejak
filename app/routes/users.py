import pandas as pd
from flask import Blueprint, request, jsonify, session
from auth import (
    role_required, get_all_users, get_login_history,
    update_user, delete_user, change_password,
    get_user_permissions,
)
from app.extensions import api_login_required, get_db_connection

bp = Blueprint('users', __name__)


@bp.route('/api/iam/users', methods=['GET'])
@api_login_required
@role_required('Admin')
def get_users():
    return jsonify(get_all_users())


@bp.route('/api/iam/users/<int:user_id>', methods=['PUT', 'DELETE'])
@api_login_required
@role_required('Admin')
def manage_user(user_id):
    if request.method == 'PUT':
        success, message = update_user(user_id, **request.json)
    else:
        success, message = delete_user(user_id)
    return jsonify({'success': success, 'message': message})


@bp.route('/api/iam/login-history', methods=['GET'])
@api_login_required
@role_required('Admin')
def get_login_history_route():
    return jsonify(get_login_history())


@bp.route('/api/iam/activity', methods=['GET'])
@api_login_required
@role_required('Admin')
def get_user_activity():
    filter_type = request.args.get('filter', 'all')
    offset      = request.args.get('offset', 0,  type=int)
    limit       = request.args.get('limit',  20, type=int)

    try:
        with get_db_connection() as conn:
            parts, params = [], []
            if filter_type in ('all', 'annotation'):
                parts.append(
                    "SELECT 'annotation' AS type, ma.created_by_username AS username, "
                    "ma.created_at AS timestamp, ma.title AS title, ma.shape_type AS shape_type, "
                    "ma.priority AS priority, ma.status AS ann_status, NULL::TEXT AS partner_name, "
                    "NULL::TEXT AS preview FROM map_annotations ma"
                )
            if filter_type in ('all', 'message'):
                parts.append(
                    "SELECT 'message' AS type, sender.username AS username, m.sent_at AS timestamp, "
                    "NULL::TEXT AS title, NULL::TEXT AS shape_type, NULL::TEXT AS priority, "
                    "NULL::TEXT AS ann_status, partner.username AS partner_name, "
                    "LEFT(m.content, 80) AS preview FROM messages m "
                    "JOIN users sender ON m.sender_id = sender.id "
                    "JOIN conversations c ON m.conversation_id = c.id "
                    "JOIN conversation_participants cp ON cp.conversation_id = c.id AND cp.user_id != m.sender_id "
                    "JOIN users partner ON cp.user_id = partner.id"
                )
            if not parts:
                return jsonify([])

            final_sql  = f"SELECT * FROM ({' UNION ALL '.join(parts)}) AS activity ORDER BY timestamp DESC LIMIT %s OFFSET %s"
            params    += [limit, offset]
            df         = pd.read_sql(final_sql, conn, params=params)
            df['timestamp'] = df['timestamp'].apply(lambda x: x.isoformat() if pd.notna(x) and x is not None else None)
            return jsonify(df.replace({float('nan'): None}).to_dict('records'))

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/user/permissions', methods=['GET'])
@api_login_required
def get_permissions():
    return jsonify(get_user_permissions(session.get('role', 'Staff')))


@bp.route('/api/user/change-password', methods=['POST'])
@api_login_required
def change_user_password():
    new_password = request.json.get('new_password', '')
    if not new_password or len(new_password) < 6:
        return jsonify({'success': False, 'message': 'Password must be at least 6 characters'}), 400
    success, message = change_password(session.get('user_id'), new_password)
    return jsonify({'success': success, 'message': message})


@bp.route('/api/user/profile', methods=['GET', 'PUT'])
@api_login_required
def user_profile():
    user_id = session.get('user_id')
    if request.method == 'GET':
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, email, full_name, role FROM users WHERE id = %s", (user_id,))
            row = cursor.fetchone()
            if not row:
                return jsonify({'error': 'User not found'}), 404
            return jsonify({'id': row[0], 'username': row[1], 'email': row[2], 'full_name': row[3], 'role': row[4]})

    data      = request.json
    full_name = data.get('full_name', '').strip()
    email     = data.get('email', '').strip()
    if not full_name or not email:
        return jsonify({'success': False, 'message': 'Full name and email are required'}), 400
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM users WHERE email = %s AND id != %s", (email, user_id))
            if cursor.fetchone():
                return jsonify({'success': False, 'message': 'Email already in use'}), 400
            cursor.execute("UPDATE users SET full_name = %s, email = %s WHERE id = %s", (full_name, email, user_id))
        session['full_name'] = full_name
        return jsonify({'success': True, 'message': 'Profile updated successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/users/list', methods=['GET'])
@api_login_required
def list_users_for_assign():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, full_name, role FROM users WHERE is_active = TRUE ORDER BY full_name")
        return jsonify([{'id': r[0], 'username': r[1], 'full_name': r[2], 'role': r[3]} for r in cursor.fetchall()])
