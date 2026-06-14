from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from auth import authenticate_user, register_user, login_required, role_required

bp = Blueprint('core', __name__)


@bp.route('/')
@login_required
def index():
    return render_template(
        'index.html',
        user_id=session.get('user_id'),
        username=session.get('username', 'User'),
        full_name=session.get('full_name', ''),
        role=session.get('role', 'Staff'),
    )


@bp.route('/iam')
@login_required
@role_required('Admin')
def iam_panel():
    return render_template(
        'iam.html',
        user_id=session.get('user_id'),
        username=session.get('username', 'Admin'),
        full_name=session.get('full_name', ''),
        role=session.get('role', 'Admin'),
    )


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')

    data     = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'success': False, 'message': 'Username and password required'}), 400

    success, user_data, message = authenticate_user(
        username, password,
        request.remote_addr,
        request.headers.get('User-Agent', 'Unknown'),
    )
    if success:
        session['user_id']   = user_data['id']
        session['username']  = user_data['username']
        session['role']      = user_data['role']
        session['full_name'] = user_data['full_name']
        session.permanent    = True
        return jsonify({'success': True, 'message': message, 'redirect': '/'})

    return jsonify({'success': False, 'message': message}), 401


@bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        return render_template('register.html')

    data      = request.json
    username  = data.get('username', '').strip()
    password  = data.get('password', '')
    email     = data.get('email', '').strip()
    full_name = data.get('full_name', '').strip()
    role      = data.get('role', 'Staff')

    if not all([username, password, email, full_name]):
        return jsonify({'success': False, 'message': 'All fields are required'}), 400

    success, message = register_user(username, password, email, full_name, role)
    if success:
        return jsonify({'success': True, 'message': message, 'redirect': '/login'})
    return jsonify({'success': False, 'message': message}), 400


@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('core.login'))
