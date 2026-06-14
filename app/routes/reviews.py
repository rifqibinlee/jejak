import re
import collections
from flask import Blueprint, request, jsonify, session
from app.extensions import api_login_required, get_db_connection

bp = Blueprint('reviews', __name__)

_STOP_WORDS = {
    'the','a','an','and','or','but','in','on','at','to','for','of','with',
    'is','it','its','was','are','be','been','have','has','i','my','we','our',
    'this','that','they','their','you','your','not','no','so','as','by','if',
    'all','can','get','more','very','just','from','about','also','up','do',
    'there','been','will','would','could','should','some','any',
}


@bp.route('/api/reviews', methods=['GET', 'POST'])
@api_login_required
def handle_reviews():
    if request.method == 'GET':
        with get_db_connection() as conn:
            cursor = conn.cursor()
            query  = "SELECT id, user_id, username, category, rating, title, body, is_anonymous, created_at, updated_at FROM reviews"
            params = []
            if request.args.get('category'):
                query += " WHERE category = %s"
                params.append(request.args.get('category'))
            cursor.execute(query + " ORDER BY created_at DESC LIMIT %s", params + [int(request.args.get('limit', 50))])
            cols   = ['id','user_id','username','category','rating','title','body','is_anonymous','created_at','updated_at']
            result = []
            for row in cursor.fetchall():
                d = dict(zip(cols, row))
                if d['is_anonymous'] and session.get('role') != 'Admin':
                    d['username'] = 'Anonymous'
                d['created_at'] = d['created_at'].isoformat() if d['created_at'] else None
                result.append(d)
            return jsonify(result)

    data = request.get_json()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO reviews (user_id, username, category, rating, title, body, is_anonymous) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at",
            (session['user_id'], session['username'],
             data.get('category', 'General'), int(data.get('rating', 0)),
             data.get('title', ''), data.get('body', ''),
             bool(data.get('is_anonymous', False))),
        )
        row = cursor.fetchone()
    return jsonify({'success': True, 'id': row[0], 'created_at': row[1].isoformat()}), 201


@bp.route('/api/reviews/<int:review_id>', methods=['DELETE'])
@api_login_required
def delete_review(review_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM reviews WHERE id = %s", (review_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        if row[0] != session['user_id'] and session.get('role') != 'Admin':
            return jsonify({'error': 'Denied'}), 403
        cursor.execute("DELETE FROM reviews WHERE id = %s", (review_id,))
    return jsonify({'success': True})


@bp.route('/api/reviews/<int:review_id>/comments', methods=['GET', 'POST'])
@api_login_required
def review_comments(review_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if request.method == 'GET':
            cursor.execute(
                "SELECT id, user_id, username, body, created_at FROM review_comments "
                "WHERE review_id = %s ORDER BY created_at ASC", (review_id,),
            )
            rows = [{'id': r[0], 'user_id': r[1], 'username': r[2], 'body': r[3], 'created_at': r[4].isoformat()} for r in cursor.fetchall()]
            return jsonify(rows)

        data = request.get_json()
        body = (data.get('body') or '').strip()
        if not body:
            return jsonify({'error': 'Comment body required'}), 400
        cursor.execute(
            "INSERT INTO review_comments (review_id, user_id, username, body) "
            "VALUES (%s, %s, %s, %s) RETURNING id, created_at",
            (review_id, session['user_id'], session['username'], body),
        )
        row = cursor.fetchone()
    return jsonify({'success': True, 'id': row[0], 'created_at': row[1].isoformat()}), 201


@bp.route('/api/reviews/<int:review_id>/comments/<int:comment_id>', methods=['DELETE'])
@api_login_required
def delete_review_comment(review_id, comment_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM review_comments WHERE id = %s AND review_id = %s", (comment_id, review_id))
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        if row[0] != session['user_id'] and session.get('role') != 'Admin':
            return jsonify({'error': 'Denied'}), 403
        cursor.execute("DELETE FROM review_comments WHERE id = %s", (comment_id,))
    return jsonify({'success': True})


@bp.route('/api/reviews/<int:review_id>/react', methods=['POST'])
@api_login_required
def react_review(review_id):
    data     = request.get_json()
    reaction = data.get('reaction')
    if reaction not in ('like', 'dislike'):
        return jsonify({'error': 'Invalid reaction'}), 400

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, reaction FROM review_reactions WHERE review_id=%s AND user_id=%s", (review_id, session['user_id']))
        existing = cursor.fetchone()
        if existing:
            if existing[1] == reaction:
                cursor.execute("DELETE FROM review_reactions WHERE id=%s", (existing[0],))
            else:
                cursor.execute("UPDATE review_reactions SET reaction=%s WHERE id=%s", (reaction, existing[0]))
        else:
            cursor.execute("INSERT INTO review_reactions (review_id, user_id, reaction) VALUES (%s,%s,%s)", (review_id, session['user_id'], reaction))

        cursor.execute("SELECT reaction, COUNT(*) FROM review_reactions WHERE review_id=%s GROUP BY reaction", (review_id,))
        counts = {'like': 0, 'dislike': 0}
        for rec_reaction, cnt in cursor.fetchall():
            counts[rec_reaction] = cnt

        cursor.execute("SELECT reaction FROM review_reactions WHERE review_id=%s AND user_id=%s", (review_id, session['user_id']))
        mine = cursor.fetchone()

    return jsonify({'success': True, 'likes': counts['like'], 'dislikes': counts['dislike'], 'my_reaction': mine[0] if mine else None})


@bp.route('/api/reviews/keywords', methods=['GET'])
@api_login_required
def review_keywords():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT body FROM reviews")
        words = []
        for (body,) in cursor.fetchall():
            words += re.findall(r"[a-zA-Z]{3,}", body.lower())
    freq = collections.Counter(w for w in words if w not in _STOP_WORDS)
    return jsonify([{'word': w, 'count': c} for w, c in freq.most_common(20)])
