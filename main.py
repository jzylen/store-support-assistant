from fastapi import FastAPI, HTTPException, Security, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
from openai import OpenAI
from database import get_connection, init_db
from contextlib import asynccontextmanager
import uuid
import os
import hmac
import hashlib
from dotenv import load_dotenv

load_dotenv()

# =========================
# APP SETUP
# =========================

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="Relixo API", version="3.0.0", lifespan=lifespan)
security = HTTPBearer()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_URL", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24
TRIAL_DAYS = 7

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable is not set!")

pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto"
)

PLAN_LIMITS = {
    "starter": 1000,
    "growth": 5000,
    "pro": 15000
}

DEMO_BUSINESS_DATA = """
You are a helpful customer support assistant for an online store.
You help customers with questions about shipping, order tracking, returns, and refunds.

Shipping: Standard shipping takes 5-7 business days. Express shipping takes 1-2 business days.
Order Tracking: Customers can track their order using the tracking link sent to their email.
Returns: We accept returns within 30 days of purchase. Items must be unused and in original packaging.
Refunds: Refunds are processed within 5-7 business days after we receive the returned item.

Always be friendly, helpful, and concise.
"""

# =========================
# MODELS
# =========================

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    business_name: str

class LoginRequest(BaseModel):
    email: EmailStr
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
# MONTHLY RESET HELPER
# =========================

def check_and_reset_monthly_count(cursor, business_id, last_reset_at):
    now = datetime.utcnow()

    # If never reset before or it's been more than a month
    if last_reset_at is None or (now.year > last_reset_at.year or now.month > last_reset_at.month):
        cursor.execute("""
            UPDATE businesses
            SET message_count = 0,
                last_reset_at = %s
            WHERE id = %s
        """, (now, business_id))
        return 0

    return None

# =========================
# AUTH ROUTES
# =========================

@app.post("/auth/register")
def register(data: RegisterRequest):
    if len(data.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    conn = get_connection()
    cursor = conn.cursor()

    business_id = str(uuid.uuid4())
    trial_end = datetime.utcnow() + timedelta(days=TRIAL_DAYS)

    try:
        cursor.execute("""
            INSERT INTO businesses 
            (id, name, created_at, plan, message_count, subscription_status, trial_ends_at, last_reset_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            business_id,
            data.business_name,
            datetime.utcnow(),
            "starter",
            0,
            "trialing",
            trial_end,
            datetime.utcnow()
        ))

        cursor.execute("""
            INSERT INTO users (email, password_hash, business_id, created_at)
            VALUES (%s, %s, %s, %s)
        """, (
            data.email,
            hash_password(data.password),
            business_id,
            datetime.utcnow()
        ))

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

    conn.close()
    token = create_access_token({"sub": data.email})
    return {"access_token": token}

@app.post("/auth/login")
def login(data: LoginRequest):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE email = %s", (data.email,))
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

    cursor.execute(
        "SELECT business_id FROM users WHERE email = %s",
        (email,)
    )
    result = cursor.fetchone()

    if not result:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    business_id = result["business_id"]

    cursor.execute("""
        UPDATE businesses
        SET data = %s
        WHERE id = %s
    """, (request.data, business_id))

    conn.commit()
    conn.close()

    return {"message": "Business data saved successfully"}

# =========================
# USAGE ENDPOINT
# =========================

@app.get("/business/usage")
def get_usage(credentials: HTTPAuthorizationCredentials = Security(security)):
    token = credentials.credentials
    email = get_current_user(token)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT b.plan, b.message_count, b.subscription_status, b.trial_ends_at, b.last_reset_at
        FROM users u
        JOIN businesses b ON u.business_id = b.id
        WHERE u.email = %s
    """, (email,))

    result = cursor.fetchone()

    if not result:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "plan": result["plan"],
        "message_count": result["message_count"],
        "limit": PLAN_LIMITS.get(result["plan"], 1000),
        "subscription_status": result["subscription_status"],
        "trial_ends_at": result["trial_ends_at"],
        "last_reset_at": result["last_reset_at"]
    }

# =========================
# GET BUSINESS ID FOR WIDGET
# =========================

@app.get("/business/id")
def get_business_id(credentials: HTTPAuthorizationCredentials = Security(security)):
    token = credentials.credentials
    email = get_current_user(token)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT business_id FROM users WHERE email = %s", (email,))
    result = cursor.fetchone()
    conn.close()

    if not result:
        raise HTTPException(status_code=404, detail="User not found")

    return {"business_id": result["business_id"]}

# =========================
# CHAT WITH TRIAL + LIMIT ENFORCEMENT
# =========================

@app.post("/chat")
def chat(data: ChatRequest, credentials: HTTPAuthorizationCredentials = Security(security)):
    token = credentials.credentials
    email = get_current_user(token)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT b.id, b.data, b.plan, b.message_count,
               b.subscription_status, b.trial_ends_at, b.last_reset_at
        FROM users u
        JOIN businesses b ON u.business_id = b.id
        WHERE u.email = %s
    """, (email,))

    result = cursor.fetchone()

    if not result:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    business_id = result["id"]
    business_data = result["data"]
    plan = result["plan"]
    message_count = result["message_count"]
    subscription_status = result["subscription_status"]
    trial_ends_at = result["trial_ends_at"]
    last_reset_at = result["last_reset_at"]

    if isinstance(trial_ends_at, str):
        trial_ends_at = datetime.fromisoformat(trial_ends_at)

    if isinstance(last_reset_at, str):
        last_reset_at = datetime.fromisoformat(last_reset_at)

    # Check and reset monthly count if needed
    reset_count = check_and_reset_monthly_count(cursor, business_id, last_reset_at)
    if reset_count is not None:
        message_count = reset_count
        conn.commit()

    # Trial enforcement
    if subscription_status == "trialing":
        if datetime.utcnow() > trial_ends_at:
            cursor.execute("""
                UPDATE businesses
                SET subscription_status = 'expired'
                WHERE id = %s
            """, (business_id,))
            conn.commit()
            conn.close()
            return {"reply": "Your free trial has expired. Please upgrade to continue using Relixo."}

    elif subscription_status not in ["active", "trialing"]:
        conn.close()
        return {"reply": "Subscription inactive. Please upgrade to continue."}

    limit = PLAN_LIMITS.get(plan, 1000)

    if message_count >= limit:
        conn.close()
        return {"reply": f"You have reached your {plan} plan limit of {limit} messages this month."}

    if not business_data:
        conn.close()
        return {"reply": "No business data configured yet."}

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": business_data},
            {"role": "user", "content": data.message}
        ]
    )

    cursor.execute("""
        UPDATE businesses
        SET message_count = message_count + 1
        WHERE id = %s
    """, (business_id,))

    conn.commit()
    conn.close()

    return {"reply": response.choices[0].message.content}

# =========================
# PUBLIC WIDGET ENDPOINT
# =========================

@app.post("/widget/chat/{business_id}")
def widget_chat(business_id: str, data: ChatRequest):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT data, plan, message_count, subscription_status, trial_ends_at, last_reset_at
        FROM businesses
        WHERE id = %s
    """, (business_id,))

    result = cursor.fetchone()

    if not result:
        conn.close()
        raise HTTPException(status_code=404, detail="Business not found")

    business_data = result["data"]
    plan = result["plan"]
    message_count = result["message_count"]
    subscription_status = result["subscription_status"]
    trial_ends_at = result["trial_ends_at"]
    last_reset_at = result["last_reset_at"]

    if isinstance(trial_ends_at, str):
        trial_ends_at = datetime.fromisoformat(trial_ends_at)

    if isinstance(last_reset_at, str):
        last_reset_at = datetime.fromisoformat(last_reset_at)

    # Check and reset monthly count if needed
    reset_count = check_and_reset_monthly_count(cursor, business_id, last_reset_at)
    if reset_count is not None:
        message_count = reset_count
        conn.commit()

    if subscription_status == "trialing":
        if datetime.utcnow() > trial_ends_at:
            cursor.execute("""
                UPDATE businesses
                SET subscription_status = 'expired'
                WHERE id = %s
            """, (business_id,))
            conn.commit()
            conn.close()
            return {"reply": "This assistant is currently unavailable. Please contact the store directly."}

    elif subscription_status not in ["active", "trialing"]:
        conn.close()
        return {"reply": "This assistant is currently unavailable. Please contact the store directly."}

    limit = PLAN_LIMITS.get(plan, 1000)
    if message_count >= limit:
        conn.close()
        return {"reply": "This assistant is currently unavailable. Please contact the store directly."}

    if not business_data:
        conn.close()
        return {"reply": "This assistant is not configured yet."}

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": business_data},
            {"role": "user", "content": data.message}
        ]
    )

    cursor.execute("""
        UPDATE businesses
        SET message_count = message_count + 1
        WHERE id = %s
    """, (business_id,))

    conn.commit()
    conn.close()

    return {"reply": response.choices[0].message.content}

# =========================
# PUBLIC DEMO ENDPOINT
# =========================

@app.post("/demo/chat")
def demo_chat(data: ChatRequest):
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": DEMO_BUSINESS_DATA},
            {"role": "user", "content": data.message}
        ]
    )
    return {"reply": response.choices[0].message.content}