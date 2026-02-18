from fastapi import FastAPI, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
from openai import OpenAI
import sqlite3
import uuid

# =========================
# APP SETUP
# =========================

app = FastAPI(title="Relixo API", version="1.0.0")
security = HTTPBearer()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # safe for development stage
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI()

SECRET_KEY = "relixo_super_secret_key_123456789"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto"
)

DB_NAME = "data.db"

PLAN_LIMITS = {
    "starter": 1000,
    "growth": 5000,
    "pro": 15000
}

# =========================
# DATABASE INIT (SAFE RESET)
# =========================

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE businesses (
            id TEXT PRIMARY KEY,
            name TEXT,
            data TEXT,
            plan TEXT DEFAULT 'starter',
            message_count INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            business_id TEXT NOT NULL,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()

init_db()

# =========================
# MODELS
# =========================

class RegisterRequest(BaseModel):
    email: str
    password: str
    business_name: str

class LoginRequest(BaseModel):
    email: str
    password: str

class ChatRequest(BaseModel):
    message: str

class BusinessSetupRequest(BaseModel):
    data: str

# =========================
# AUTH HELPERS
# =========================

def hash_password(password):
    return pwd_context.hash(password)

def verify_password(password, hashed):
    return pwd_context.verify(password, hashed)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return email
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# =========================
# AUTH ROUTES
# =========================

@app.post("/auth/register")
def register(data: RegisterRequest):
    conn = get_connection()
    cursor = conn.cursor()

    business_id = str(uuid.uuid4())

    try:
        cursor.execute("""
            INSERT INTO businesses (id, name, created_at, plan, message_count)
            VALUES (?, ?, ?, ?, ?)
        """, (
            business_id,
            data.business_name,
            datetime.utcnow().isoformat(),
            "starter",
            0
        ))

        cursor.execute("""
            INSERT INTO users (email, password_hash, business_id, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            data.email,
            hash_password(data.password),
            business_id,
            datetime.utcnow().isoformat()
        ))

        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

    conn.close()
    token = create_access_token({"sub": data.email})
    return {"access_token": token}

@app.post("/auth/login")
def login(data: LoginRequest):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE email = ?", (data.email,))
    user = cursor.fetchone()

    conn.close()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid credentials")

    if not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Invalid credentials")

    token = create_access_token({"sub": data.email})
    return {"access_token": token}

# =========================
# BUSINESS SETUP
# =========================

@app.post("/business/setup")
def setup_business(
    request: BusinessSetupRequest,
    credentials: HTTPAuthorizationCredentials = Security(security)
):
    token = credentials.credentials
    email = get_current_user(token)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT business_id FROM users WHERE email = ?
    """, (email,))
    result = cursor.fetchone()

    if not result:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    business_id = result["business_id"]

    cursor.execute("""
        UPDATE businesses
        SET data = ?
        WHERE id = ?
    """, (request.data, business_id))

    conn.commit()
    conn.close()

    return {"message": "Business data saved successfully"}

# =========================
# USAGE ENDPOINT
# =========================

@app.get("/business/usage")
def get_usage(
    credentials: HTTPAuthorizationCredentials = Security(security)
):
    token = credentials.credentials
    email = get_current_user(token)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT b.plan, b.message_count
        FROM users u
        JOIN businesses b ON u.business_id = b.id
        WHERE u.email = ?
    """, (email,))
    
    result = cursor.fetchone()
    conn.close()

    if not result:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "plan": result["plan"],
        "message_count": result["message_count"],
        "limit": PLAN_LIMITS.get(result["plan"], 1000)
    }

# =========================
# CHAT WITH LIMIT
# =========================

@app.post("/chat")
def chat(
    data: ChatRequest,
    credentials: HTTPAuthorizationCredentials = Security(security)
):
    token = credentials.credentials
    email = get_current_user(token)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT b.id, b.data, b.plan, b.message_count
        FROM users u
        JOIN businesses b ON u.business_id = b.id
        WHERE u.email = ?
    """, (email,))
    
    result = cursor.fetchone()

    if not result:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    business_id = result["id"]
    business_data = result["data"]
    plan = result["plan"]
    message_count = result["message_count"]

    limit = PLAN_LIMITS.get(plan, 1000)

    if message_count >= limit:
        conn.close()
        return {
            "reply": f"You have reached your {plan} plan limit of {limit} messages this month."
        }

    if not business_data:
        conn.close()
        return {"reply": "No business data configured yet."}

    response = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": business_data},
            {"role": "user", "content": data.message}
        ]
    )

    cursor.execute("""
        UPDATE businesses
        SET message_count = message_count + 1
        WHERE id = ?
    """, (business_id,))
    conn.commit()
    conn.close()

    return {"reply": response.output_text}
