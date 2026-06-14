from flask import Blueprint, request, jsonify, session
from app.extensions import api_login_required, get_db_connection

bp = Blueprint('messages', __name__)


def _get_or_create_conversation(user_id, other_user_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT cp1.conversation_id FROM conversation_participants cp1
            JOIN conversation_participants cp2 ON cp1.conversation_id = cp2.conversation_id
            JOIN conversations c ON c.id = cp1.conversation_id
            WHERE cp1.user_id = %s AND cp2.user_id = %s AND c.is_group = FALSE
        """, (user_id, other_user_id))
        row = cursor.fetchone()
        if row:
            return row[0]
        cursor.execute("INSERT INTO conversations (created_by, is_group) VALUES (%s, FALSE) RETURNING id", (user_id,))
        conv_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO conversation_participants (conversation_id, user_id) VALUES (%s, %s), (%s, %s)",
            (conv_id, user_id, conv_id, other_user_id),
        )
        return conv_id


@bp.route('/api/messages/conversations', methods=['GET'])
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
                result.append({
                    'id': r[0], 'title': display_name, 'is_group': r[2],
                    'member_names': r[3] or [], 'member_usernames': r[4] or [],
                    'partner_name': display_name, 'last_message': r[5],
                    'last_time': r[6].isoformat() if r[6] else None,
                    'unread_count': int(r[7]),
                })
            return jsonify(result)
    except Exception:
        return jsonify([])


@bp.route('/api/messages/conversation/<int:conv_id>', methods=['GET'])
@api_login_required
def get_conversation_messages(conv_id):
    user_id = session.get('user_id')
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM conversation_participants WHERE conversation_id = %s AND user_id = %s", (conv_id, user_id))
        if not cursor.fetchone():
            return jsonify({'error': 'Unauthorized'}), 403
        cursor.execute("""
            INSERT INTO message_reads (message_id, user_id)
            SELECT m.id, %s FROM messages m WHERE m.conversation_id = %s AND m.sender_id != %s
            AND NOT EXISTS (SELECT 1 FROM message_reads mr WHERE mr.message_id = m.id AND mr.user_id = %s)
            ON CONFLICT DO NOTHING
        """, (user_id, conv_id, user_id, user_id))
        cursor.execute(
            "SELECT m.id, m.sender_id, u.full_name, m.content, m.sent_at, (m.sender_id = %s) "
            "FROM messages m JOIN users u ON u.id = m.sender_id "
            "WHERE m.conversation_id = %s ORDER BY m.sent_at ASC",
            (user_id, conv_id),
        )
        return jsonify([{'id': r[0], 'sender_id': r[1], 'sender_name': r[2], 'content': r[3], 'sent_at': r[4].isoformat(), 'is_mine': r[5]} for r in cursor.fetchall()])


@bp.route('/api/messages/send', methods=['POST'])
@api_login_required
def send_message():
    user_id = session.get('user_id')
    data    = request.json
    conv_id = data.get('conversation_id')
    content = data.get('content', '').strip()
    if not conv_id or not content:
        return jsonify({'success': False}), 400
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM conversation_participants WHERE conversation_id = %s AND user_id = %s", (conv_id, user_id))
        if not cursor.fetchone():
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        cursor.execute("INSERT INTO messages (conversation_id, sender_id, content) VALUES (%s, %s, %s)", (conv_id, user_id, content))
        cursor.execute("INSERT INTO message_reads (message_id, user_id) SELECT currval('messages_id_seq'), %s ON CONFLICT DO NOTHING", (user_id,))
        return jsonify({'success': True})


@bp.route('/api/messages/new', methods=['POST'])
@api_login_required
def start_new_conversation():
    user_id      = session.get('user_id')
    data         = request.json
    recipient_id = data.get('recipient_id')
    content      = data.get('content', '').strip()
    if not recipient_id or not content or recipient_id == user_id:
        return jsonify({'success': False}), 400
    conv_id = _get_or_create_conversation(user_id, recipient_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO messages (conversation_id, sender_id, content) VALUES (%s, %s, %s)", (conv_id, user_id, content))
        cursor.execute("SELECT full_name FROM users WHERE id = %s", (recipient_id,))
        return jsonify({'success': True, 'conversation_id': conv_id, 'partner_name': cursor.fetchone()[0]})


@bp.route('/api/messages/group/new', methods=['POST'])
@api_login_required
def start_group_conversation():
    user_id    = session.get('user_id')
    data       = request.json
    member_ids = data.get('member_ids', [])
    if len(member_ids) < 2:
        return jsonify({'success': False}), 400
    title = data.get('title', '').strip() or 'Group Chat'
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO conversations (title, created_by, is_group) VALUES (%s, %s, TRUE) RETURNING id", (title, user_id))
        conv_id = cursor.fetchone()[0]
        for uid in list(set([user_id] + member_ids)):
            cursor.execute("INSERT INTO conversation_participants (conversation_id, user_id, is_admin) VALUES (%s, %s, %s)", (conv_id, uid, uid == user_id))
        return jsonify({'success': True, 'conversation_id': conv_id, 'title': title})


@bp.route('/api/messages/group/<int:conv_id>/<action>', methods=['POST'])
@api_login_required
def manage_group(conv_id, action):
    user_id = session.get('user_id')
    data    = request.json or {}
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_group FROM conversations WHERE id = %s", (conv_id,))
        if not cursor.fetchone()[0]:
            return jsonify({'success': False}), 400
        cursor.execute("SELECT is_admin FROM conversation_participants WHERE conversation_id = %s AND user_id = %s", (conv_id, user_id))
        admin_check = cursor.fetchone()
        if action in ['add', 'remove', 'rename', 'delete'] and not (admin_check and admin_check[0]):
            return jsonify({'success': False}), 403
        if action == 'leave':   cursor.execute("DELETE FROM conversation_participants WHERE conversation_id = %s AND user_id = %s", (conv_id, user_id))
        elif action == 'add':   cursor.execute("INSERT INTO conversation_participants (conversation_id, user_id, is_admin) VALUES (%s, %s, FALSE) ON CONFLICT DO NOTHING", (conv_id, data.get('user_id')))
        elif action == 'remove':cursor.execute("DELETE FROM conversation_participants WHERE conversation_id = %s AND user_id = %s", (conv_id, data.get('user_id')))
        elif action == 'rename':cursor.execute("UPDATE conversations SET title = %s WHERE id = %s", (data.get('title'), conv_id))
        elif action == 'delete':cursor.execute("DELETE FROM conversations WHERE id = %s", (conv_id,))
        return jsonify({'success': True})


@bp.route('/api/messages/group/<int:conv_id>/members', methods=['GET'])
@api_login_required
def get_group_members(conv_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT u.id, u.full_name, u.username, u.role, cp.is_admin, cp.joined_at "
            "FROM conversation_participants cp JOIN users u ON u.id = cp.user_id "
            "WHERE cp.conversation_id = %s ORDER BY cp.is_admin DESC, cp.joined_at ASC",
            (conv_id,),
        )
        return jsonify([{'id': r[0], 'full_name': r[1], 'username': r[2], 'role': r[3], 'is_admin': r[4]} for r in cursor.fetchall()])


@bp.route('/api/messages/users', methods=['GET'])
@api_login_required
def get_users_for_messaging():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, full_name, username FROM users WHERE is_active = TRUE ORDER BY full_name")
        return jsonify([{'id': r[0], 'full_name': r[1], 'username': r[2]} for r in cursor.fetchall()])


@bp.route('/api/messages/unread-count', methods=['GET'])
@api_login_required
def get_unread_count():
    uid = session.get('user_id')
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM messages m
            JOIN conversation_participants cp ON cp.conversation_id = m.conversation_id
            WHERE cp.user_id = %s AND m.sender_id != %s
            AND NOT EXISTS (SELECT 1 FROM message_reads mr WHERE mr.message_id = m.id AND mr.user_id = %s)
        """, (uid, uid, uid))
        return jsonify({'count': cursor.fetchone()[0]})
