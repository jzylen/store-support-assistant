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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS businesses (
            id TEXT PRIMARY KEY,
            name TEXT,
            business_type TEXT DEFAULT 'Other',
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
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            business_id TEXT REFERENCES businesses(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # Add business_type column if it doesn't exist
    cursor.execute("""
        ALTER TABLE businesses
        ADD COLUMN IF NOT EXISTS business_type TEXT DEFAULT 'Other';
    """)

    # Add is_admin column if it doesn't exist
    cursor.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;
    """)

    conn.commit()
    cursor.close()
    conn.close()