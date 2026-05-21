import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set!")
    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require",
        cursor_factory=RealDictCursor
    )

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Temporarily dropping to recreate with new columns
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
            created_at TIMESTAMP DEFAULT NOW(),
            last_reset_at TIMESTAMP DEFAULT NOW()
        );
    """)

    cursor.execute("""
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            business_id TEXT REFERENCES businesses(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    conn.commit()
    cursor.close()
    conn.close()