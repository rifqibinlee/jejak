import sys
import os
import psycopg2
import bcrypt

DB_CONFIG = {
    'host':     os.getenv('DB_HOST',     'localhost'),
    'port':     int(os.getenv('DB_PORT', '5432')),
    'database': os.getenv('DB_NAME',     'vibe_db'),
    'user':     os.getenv('DB_USER',     'postgres'),
    'password': os.getenv('DB_PASSWORD', '1234')
}

def recreate_admin():
    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True
        cursor = conn.cursor()

        # Check if admin already exists
        cursor.execute("SELECT id FROM users WHERE username = 'admin'")
        if cursor.fetchone():
            cursor.execute("DELETE FROM users WHERE username = 'admin'")
            print("[Admin Check] Existing admin user cleared.")

        # Hash password 'admin123'
        password_hash = bcrypt.hashpw('admin123'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        # Insert admin user
        cursor.execute("""
            INSERT INTO users (username, password_hash, email, full_name, role, is_active)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, ('admin', password_hash, 'admin@system.local', 'System Administrator', 'Admin', True))

        print("[Admin Check] SUCCESS: Admin account active (admin / admin123).")
        return True

    except Exception as e:
        print(f"Error recreating admin: {e}")
        return False
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    recreate_admin()
