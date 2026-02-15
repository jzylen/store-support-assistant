import sqlite3
from datetime import datetime

DB_NAME = "data.db"

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Businesses table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS businesses (
            id TEXT PRIMARY KEY,
            name TEXT,
            data TEXT,
            plan TEXT DEFAULT 'starter',
            message_count INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            business_id TEXT NOT NULL,
            created_at TEXT,
            FOREIGN KEY (business_id) REFERENCES businesses (id)
        )
    """)

    conn.commit()
    conn.close()
