from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI
import sqlite3
from datetime import datetime
import uuid

app = FastAPI()
client = OpenAI()

DB_PATH = "data.db"

PLANS = {
    "starter": 1000,
    "growth": 5000,
    "pro": 15000
}

# ---------- CORS ----------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- DATABASE ----------

def get_db():
    return sqlite3.connect(DB_PATH)

def init_db():
    db = get_db()
    cur = db.cursor()

    # Businesses table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS businesses (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        plan TEXT NOT NULL DEFAULT 'starter',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """)

    # Business data table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS business_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1
    )
    """)

    # Usage table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id TEXT NOT NULL,
        month TEXT NOT NULL
    )
    """)

    db.commit()
    db.close()

# Initialize DB at startup
init_db()

# ---------- MODELS ----------

class CreateBusinessRequest(BaseModel):
    name: str

class BusinessDataRequest(BaseModel):
    business_id: str
    data: dict

class ChatRequest(BaseModel):
    business_id: str
    message: str

# ---------- ONBOARDING ----------

@app.post("/onboard/business")
def create_business(request: CreateBusinessRequest):
    business_id = str(uuid.uuid4())

    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        INSERT INTO businesses (id, name, plan, active, created_at)
        VALUES (?, ?, 'starter', 1, ?)
        """,
        (business_id, request.name, datetime.utcnow().isoformat())
    )
    db.commit()
    db.close()

    return {
        "business_id": business_id,
        "name": request.name
    }

@app.post("/onboard/data")
def save_business_data(request: BusinessDataRequest):
    db = get_db()
    cur = db.cursor()

    for key, entry in request.data.items():
        cur.execute(
            """
            INSERT INTO business_data (business_id, key, value, enabled)
            VALUES (?, ?, ?, ?)
            """,
            (
                request.business_id,
                key,
                entry.get("value", ""),
                1 if entry.get("enabled") else 0
            )
        )

    db.commit()
    db.close()
    return {"status": "saved"}

# ---------- HELPERS ----------

def get_business(business_id):
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT id, plan, active FROM businesses WHERE id = ?",
        (business_id,)
    )
    row = cur.fetchone()
    db.close()
    return row

def get_monthly_usage(business_id):
    month = datetime.utcnow().strftime("%Y-%m")
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM usage WHERE business_id = ? AND month = ?",
        (business_id, month)
    )
    count = cur.fetchone()[0]
    db.close()
    return count

def log_usage(business_id):
    month = datetime.utcnow().strftime("%Y-%m")
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "INSERT INTO usage (business_id, month) VALUES (?, ?)",
        (business_id, month)
    )
    db.commit()
    db.close()

def get_enabled_business_data(business_id):
    db = get_db()
    cur = db.cursor()
    cur.execute(
        """
        SELECT key, value
        FROM business_data
        WHERE business_id = ? AND enabled = 1
        """,
        (business_id,)
    )
    rows = cur.fetchall()
    db.close()
    return rows

# ---------- CHAT ----------

@app.post("/chat")
def chat(request: ChatRequest):
    business = get_business(request.business_id)

    if not business:
        return {"reply": "Business not found."}

    business_id, plan, active = business

    if not active:
        return {"reply": "This assistant is currently inactive."}

    limit = PLANS.get(plan, PLANS["starter"])
    usage = get_monthly_usage(business_id)

    if usage >= limit:
        return {
            "reply": "This store has reached its monthly support limit. Please contact support."
        }

    data = get_enabled_business_data(business_id)

    if not data:
        return {
            "reply": "No business data configured yet. Please contact support."
        }

    context = "\n".join([f"{k}: {v}" for k, v in data])

    try:
        response = client.responses.create(
            model="gpt-4o-mini",
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are a customer support assistant for an online store.\n"
                        "Only answer using the provided business information.\n"
                        "If the answer is not available, politely direct the user to human support.\n\n"
                        f"Business info:\n{context}"
                    )
                },
                {
                    "role": "user",
                    "content": request.message
                }
            ]
        )

        reply = response.output_text
        log_usage(business_id)

        return {"reply": reply}

    except Exception:
        return {"reply": "Sorry, something went wrong. Please try again later."}
