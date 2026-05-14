"""
Authentication and Authorization Module for Network Analytics Platform
Handles user login, session management, and role-based access control
"""

import psycopg2
import bcrypt
from functools import wraps
from flask import session, redirect, url_for, request, jsonify
from datetime import datetime
import os

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'vibe_db'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', '1234'),
    'port': os.getenv('DB_PORT', '5432')
}

def get_db_connection():
    """Create a database connection"""
    return psycopg2.connect(**DB_CONFIG)

def hash_password(password):
    """Hash a password using bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password, password_hash):
    """Verify a password against its hash"""
    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))

def authenticate_user(username, password, ip_address=None, user_agent=None):
    """
    Authenticate a user and log the attempt
    Returns: (success: bool, user_data: dict or None, message: str)
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, username, password_hash, email, full_name, role, is_active
            FROM users
            WHERE username = %s
        """, (username,))

        user = cursor.fetchone()

        if not user:
            cursor.execute("""
                INSERT INTO login_history (username, login_time, ip_address, user_agent, status)
                VALUES (%s, %s, %s, %s, 'failed')
            """, (username, datetime.now(), ip_address, user_agent))
            conn.commit()
            return False, None, "Invalid username or password"

        user_id, username, password_hash, email, full_name, role, is_active = user

        if not is_active:
            cursor.execute("""
                INSERT INTO login_history (user_id, username, login_time, ip_address, user_agent, status)
                VALUES (%s, %s, %s, %s, %s, 'failed')
            """, (user_id, username, datetime.now(), ip_address, user_agent))
            conn.commit()
            return False, None, "Account is disabled"

        if not verify_password(password, password_hash):
            cursor.execute("""
                INSERT INTO login_history (user_id, username, login_time, ip_address, user_agent, status)
                VALUES (%s, %s, %s, %s, %s, 'failed')
            """, (user_id, username, datetime.now(), ip_address, user_agent))
            conn.commit()
            return False, None, "Invalid username or password"

        cursor.execute("""
            UPDATE users SET last_login = %s WHERE id = %s
        """, (datetime.now(), user_id))

        cursor.execute("""
            INSERT INTO login_history (user_id, username, login_time, ip_address, user_agent, status)
            VALUES (%s, %s, %s, %s, %s, 'success')
        """, (user_id, username, datetime.now(), ip_address, user_agent))

        conn.commit()

        user_data = {
            'id': user_id,
            'username': username,
            'email': email,
            'full_name': full_name,
            'role': role
        }

        return True, user_data, "Login successful"

    except Exception as e:
        print(f"Authentication error: {e}")
        return False, None, "Authentication error occurred"
    finally:
        if conn:
            conn.close()

def register_user(username, password, email, full_name, role='Staff'):
    """
    Register a new user
    Returns: (success: bool, message: str)
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            return False, "Username already exists"

        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            return False, "Email already exists"

        password_hash = hash_password(password)
        cursor.execute("""
            INSERT INTO users (username, password_hash, email, full_name, role)
            VALUES (%s, %s, %s, %s, %s)
        """, (username, password_hash, email, full_name, role))

        conn.commit()
        return True, "User registered successfully"

    except Exception as e:
        print(f"Registration error: {e}")
        return False, f"Registration error: {str(e)}"
    finally:
        if conn:
            conn.close()

def login_required(f):
    """Decorator to require login for a route"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*roles):
    """Decorator to require specific role(s) for a route"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))

            if 'role' not in session or session['role'] not in roles:
                return jsonify({'error': 'Unauthorized access'}), 403

            return f(*args, **kwargs)
        return decorated_function
    return decorator

def get_user_permissions(role):
    """Get permissions for a specific role"""
    permissions = {
        'Admin': {
            'view_dashboard': True,
            'export_unified_cd': True,
            'view_pricing': True,
            'edit_pricing': True,
            'view_map': True,
            'view_iam_panel': True,
            'manage_users': True,
            'view_login_history': True
        },
        'Planner': {
            'view_dashboard': True,
            'export_unified_cd': True,
            'view_pricing': True,
            'edit_pricing': True,
            'view_map': True,
            'view_iam_panel': False,
            'manage_users': False,
            'view_login_history': False
        },
        'Staff': {
            'view_dashboard': True,
            'export_unified_cd': False,
            'view_pricing': True,
            'edit_pricing': False,
            'view_map': True,
            'view_iam_panel': False,
            'manage_users': False,
            'view_login_history': False
        }
    }
    return permissions.get(role, permissions['Staff'])

def get_all_users():
    """Get all users from database"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, username, email, full_name, role, is_active, created_at, last_login
            FROM users
            ORDER BY created_at DESC
        """)

        users = []
        for row in cursor.fetchall():
            users.append({
                'id': row[0],
                'username': row[1],
                'email': row[2],
                'full_name': row[3],
                'role': row[4],
                'is_active': row[5],
                'created_at': row[6].isoformat() if row[6] else None,
                'last_login': row[7].isoformat() if row[7] else None
            })

        return users
    except Exception as e:
        print(f"Error getting users: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_login_history(limit=100):
    """Get login history from database"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT lh.id, lh.username, lh.login_time, lh.ip_address, lh.status, u.role
            FROM login_history lh
            LEFT JOIN users u ON lh.user_id = u.id
            ORDER BY lh.login_time DESC
            LIMIT %s
        """, (limit,))

        history = []
        for row in cursor.fetchall():
            history.append({
                'id': row[0],
                'username': row[1],
                'login_time': row[2].isoformat() if row[2] else None,
                'ip_address': row[3],
                'status': row[4],
                'role': row[5]
            })

        return history
    except Exception as e:
        print(f"Error getting login history: {e}")
        return []
    finally:
        if conn:
            conn.close()

def update_user(user_id, **kwargs):
    """Update user information"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        allowed_fields = ['email', 'full_name', 'role', 'is_active']
        updates = []
        values = []

        for field, value in kwargs.items():
            if field in allowed_fields:
                updates.append(f"{field} = %s")
                values.append(value)

        if not updates:
            return False, "No valid fields to update"

        values.append(user_id)
        query = f"UPDATE users SET {', '.join(updates)} WHERE id = %s"
        cursor.execute(query, values)
        conn.commit()

        return True, "User updated successfully"
    except Exception as e:
        print(f"Error updating user: {e}")
        return False, str(e)
    finally:
        if conn:
            conn.close()

def delete_user(user_id):
    """Delete a user"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if user and user[0] == 'admin':
            return False, "Cannot delete admin user"

        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()

        return True, "User deleted successfully"
    except Exception as e:
        print(f"Error deleting user: {e}")
        return False, str(e)
    finally:
        if conn:
            conn.close()

def change_password(user_id, new_password):
    """Change user password"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        password_hash = hash_password(new_password)
        cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id))
        conn.commit()

        return True, "Password changed successfully"
    except Exception as e:
        print(f"Error changing password: {e}")
        return False, str(e)
    finally:
        if conn:
            conn.close()
