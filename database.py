import sqlite3
from datetime import date
import uuid

DB_NAME = "db.sqlite3"

# -----------------------------
# Connection
# -----------------------------
def get_connection():
    return sqlite3.connect(DB_NAME)

# -----------------------------
# Initialize database
# -----------------------------
def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS businesses (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        plan TEXT NOT NULL,
        active INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS business_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        enabled INTEGER NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usage (
        business_id TEXT NOT NULL,
        date TEXT NOT NULL,
        count INTEGER NOT NULL,
        PRIMARY KEY (business_id, date)
    )
    """)

    conn.commit()
    conn.close()

# -----------------------------
# Business helpers
# -----------------------------
def create_business(name):
    business_id = str(uuid.uuid4())
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO businesses (id, name, plan, active, created_at)
    VALUES (?, ?, ?, ?, ?)
    """, (business_id, name, "free", 1, str(date.today())))

    conn.commit()
    conn.close()
    return business_id

def get_business(business_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT id, name, plan, active
    FROM businesses
    WHERE id = ?
    """, (business_id,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row[0],
        "name": row[1],
        "plan": row[2],
        "active": bool(row[3])
    }

# -----------------------------
# Business data helpers
# -----------------------------
def save_business_data(business_id, data: dict):
    conn = get_connection()
    cursor = conn.cursor()

    # Remove old data
    cursor.execute("""
    DELETE FROM business_data WHERE business_id = ?
    """, (business_id,))

    # Insert new data
    for key, item in data.items():
        cursor.execute("""
        INSERT INTO business_data (business_id, key, value, enabled)
        VALUES (?, ?, ?, ?)
        """, (
            business_id,
            key,
            item["value"],
            1 if item["enabled"] else 0
        ))

    conn.commit()
    conn.close()

def get_enabled_business_data(business_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT key, value
    FROM business_data
    WHERE business_id = ? AND enabled = 1
    """, (business_id,))

    rows = cursor.fetchall()
    conn.close()

    return {key: value for key, value in rows}

# -----------------------------
# Usage helpers
# -----------------------------
def can_use_service(business_id, limit):
    today = str(date.today())
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT count FROM usage
    WHERE business_id = ? AND date = ?
    """, (business_id, today))

    row = cursor.fetchone()
    conn.close()

    if not row:
        return True

    return row[0] < limit

def increment_usage(business_id):
    today = str(date.today())
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT count FROM usage
    WHERE business_id = ? AND date = ?
    """, (business_id, today))

    row = cursor.fetchone()

    if row:
        cursor.execute("""
        UPDATE usage
        SET count = count + 1
        WHERE business_id = ? AND date = ?
        """, (business_id, today))
    else:
        cursor.execute("""
        INSERT INTO usage (business_id, date, count)
        VALUES (?, ?, 1)
        """, (business_id, today))

    conn.commit()
    conn.close()
