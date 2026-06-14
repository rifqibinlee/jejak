"""
PostgreSQL-backed per-thread chat memory for the LangGraph agent.
"""
import json
import os
import psycopg2
from langchain_core.messages import SystemMessage, HumanMessage, messages_to_dict, messages_from_dict

from agent.prompt import SYSTEM_PROMPT

DB_KWARGS = dict(
    host=os.getenv('DB_HOST',     'vibe_db'),
    database=os.getenv('DB_NAME', 'vibe_db'),
    user=os.getenv('DB_USER',     'postgres'),
    password=os.getenv('DB_PASSWORD'),
    port=os.getenv('DB_PORT',     '5432'),
)


def init_memory_db():
    """Create the chat_memory table on boot if it does not exist."""
    try:
        conn = psycopg2.connect(**DB_KWARGS)
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_memory (
                thread_id VARCHAR(255) PRIMARY KEY,
                history   JSONB
            );
        """)
        cursor.close()
        conn.close()
        print("[System] Chat memory database initialized successfully.")
    except Exception as e:
        print(f"[Memory DB Error] {e}")


def load_history(thread_id: str):
    """Load conversation history for a thread. Returns list of LangChain messages."""
    try:
        conn   = psycopg2.connect(**DB_KWARGS)
        cursor = conn.cursor()
        cursor.execute("SELECT history FROM chat_memory WHERE thread_id = %s", (thread_id,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()

        if result:
            raw = result[0]
            if isinstance(raw, str):
                raw = json.loads(raw)
            return messages_from_dict(raw)
    except Exception as e:
        print(f"[Memory load error] {e}")

    return [SystemMessage(content=SYSTEM_PROMPT)]


def save_history(thread_id: str, messages: list, conn=None):
    """Persist updated conversation history back to PostgreSQL."""
    history_json = json.dumps(messages_to_dict(messages))
    _close = False
    if conn is None:
        conn   = psycopg2.connect(**DB_KWARGS)
        _close = True
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO chat_memory (thread_id, history)
            VALUES (%s, %s)
            ON CONFLICT (thread_id) DO UPDATE SET history = EXCLUDED.history;
        """, (thread_id, history_json))
        conn.commit()
        cursor.close()
    finally:
        if _close:
            conn.close()
