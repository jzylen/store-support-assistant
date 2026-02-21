import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require",
        cursor_factory=RealDictCursor
    )

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Drop tables (SAFE because no production data yet)
    cursor.execute("DROP TABLE IF EXISTS users CASCADE;")
    cursor.execute("DROP TABLE IF EXISTS businesses CASCADE;")

    cursor.execute("""
        CREATE TABLE businesses (
            id TEXT PRIMARY KEY,
            name TEXT,
            data TEXT,
            plan TEXT DEFAULT 'starter',
            message_count INTEGER DEFAULT 0,
            subscription_status TEXT DEFAULT 'inactive',
            trial_ends_at TIMESTAMP,
            paddle_subscription_id TEXT,
            created_at TEXT
        );
    """)

    cursor.execute("""
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            business_id TEXT NOT NULL,
            created_at TEXT
        );
    """)

    conn.commit()
    cursor.close()
    conn.close()