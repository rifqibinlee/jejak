import traceback
from flask import Blueprint, request, jsonify, session
from auth import login_required
from agent import run_netalytics_agent

bp = Blueprint('chat', __name__)


@bp.route('/api/chat', methods=['POST'])
@login_required
def chat_endpoint():
    data        = request.json
    user_prompt = data.get('message', '').strip()
    week        = data.get('week',     'All')
    region      = data.get('region',   'All')
    operator    = data.get('operator', 'All')
    cluster     = data.get('cluster',  'All')
    thread_id   = str(session.get('user_id', 'default_session'))

    if not user_prompt:
        return jsonify({"error": "Empty message"}), 400

    print(f"[AI] Routing prompt to LangGraph Agent (Thread: {thread_id})...")
    try:
        ai_response = run_netalytics_agent(user_prompt, week, region, operator, cluster, thread_id)
        return jsonify({"reply": ai_response, "cached": False})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"error": "My analytical engine encountered an error. Please try again."}), 500
