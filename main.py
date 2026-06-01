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
from typing import List, Optional
import uuid
import os
import hmac
import hashlib
import json
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
    allow_origins=[
        os.getenv("FRONTEND_URL", "http://localhost:3000"),
        "https://getrelixo.com",
        "https://www.getrelixo.com",
        "https://store-support-frontend.onrender.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 720
TRIAL_DAYS = 7

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable is not set!")

PADDLE_WEBHOOK_SECRET = os.getenv("PADDLE_WEBHOOK_SECRET")
PADDLE_SECRET_KEY = os.getenv("PADDLE_SECRET_KEY")

PADDLE_PRICE_TO_PLAN = {
    os.getenv("PADDLE_STARTER_PRICE_ID"): "starter",
    os.getenv("PADDLE_GROWTH_PRICE_ID"): "growth",
    os.getenv("PADDLE_PRO_PRICE_ID"): "pro"
}

pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto"
)

PLAN_LIMITS = {
    "starter": 1000,
    "growth": 5000,
    "pro": 15000
}

PLAN_MODELS = {
    "starter": {"model": "gpt-4o-mini", "max_tokens": 500},
    "growth": {"model": "gpt-4o", "max_tokens": 1000},
    "pro": {"model": "gpt-4o", "max_tokens": 2000}
}

DEMO_BUSINESS_DATA = """
You are Relixo, a friendly and helpful assistant for the Relixo platform — an AI customer support platform for small businesses.

Your job is to help website visitors understand what Relixo is, how it works and how to get started.

About Relixo:
Relixo is an AI-powered customer support assistant that small businesses can set up in under 2 minutes and embed on their website. It answers customer questions 24/7 automatically.

Plans & Pricing:
- Starter: $19/month — 1,000 messages per month
- Growth: $49/month — 5,000 messages per month
- Pro: $99/month — 15,000 messages per month
- All plans include a 7-day free trial with no credit card required

Key Features:
- Custom bot name and personality
- Full product and business knowledge configuration
- Supports 50+ languages automatically
- Easy embed on any website including Shopify, WordPress, Wix and Squarespace
- Usage dashboard with monthly message tracking
- Works 24/7 with no human needed

How it works:
1. Sign up for free at getrelixo.com
2. Configure your assistant with your business details using our 5 step wizard
3. Copy one line of code and paste it on your website
4. Your customers can now get instant answers 24/7

Free Trial:
All plans include a 7-day free trial. No credit card required to start.

Support:
For any questions email us at hello@getrelixo.com or visit getrelixo.com

Always be friendly, conversational and encouraging. Help visitors understand the value of Relixo and guide them toward signing up for a free trial. Always respond in the same language the visitor is writing in. Never switch languages mid conversation.
"""

# =========================
# MODELS
# =========================

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    business_name: str
    business_type: Optional[str] = "Other"

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class HistoryMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str
    history: Optional[List[HistoryMessage]] = []

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
            (id, name, business_type, created_at, plan, message_count, subscription_status, trial_ends_at, last_reset_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            business_id,
            data.business_name,
            data.business_type,
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
    conn.close()

    if not result:
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
               b.subscription_status, b.trial_ends_at, b.last_reset_at,
               u.is_admin
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
    is_admin = result["is_admin"]

    if isinstance(trial_ends_at, str):
        trial_ends_at = datetime.fromisoformat(trial_ends_at)

    if isinstance(last_reset_at, str):
        last_reset_at = datetime.fromisoformat(last_reset_at)

    # Admin bypass — skip all limits and trial enforcement
    if not is_admin:
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

    # Build messages array with history
    messages = [{"role": "system", "content": business_data}]

    if data.history:
        history = data.history[-20:]
        for h in history:
            if h.role in ["user", "assistant"]:
                messages.append({"role": h.role, "content": h.content})

    messages.append({"role": "user", "content": data.message})

    # Use model based on plan
    model_config = PLAN_MODELS.get(plan, PLAN_MODELS["starter"])
    response = client.chat.completions.create(
        model=model_config["model"],
        max_tokens=model_config["max_tokens"],
        messages=messages
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

    # Build messages array with history
    messages = [{"role": "system", "content": business_data}]

    if data.history:
        history = data.history[-20:]
        for h in history:
            if h.role in ["user", "assistant"]:
                messages.append({"role": h.role, "content": h.content})

    messages.append({"role": "user", "content": data.message})

    # Use model based on plan
    model_config = PLAN_MODELS.get(plan, PLAN_MODELS["starter"])
    response = client.chat.completions.create(
        model=model_config["model"],
        max_tokens=model_config["max_tokens"],
        messages=messages
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
# PADDLE WEBHOOK
# =========================

@app.post("/paddle/webhook")
async def paddle_webhook(request: Request):
    payload = await request.body()
    signature = request.headers.get("paddle-signature", "")

    ts = None
    h1 = None
    for part in signature.split(";"):
        if part.startswith("ts="):
            ts = part[3:]
        elif part.startswith("h1="):
            h1 = part[3:]

    if not ts or not h1:
        raise HTTPException(status_code=400, detail="Invalid signature format")

    signed_payload = f"{ts}:{payload.decode('utf-8')}"
    expected = hmac.new(
        PADDLE_WEBHOOK_SECRET.encode(),
        signed_payload.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, h1):
        raise HTTPException(status_code=400, detail="Signature mismatch")

    data = json.loads(payload)
    event_type = data.get("event_type", "")
    event_data = data.get("data", {})

    # Handle transaction.completed
    if event_type == "transaction.completed":
        subscription_id = event_data.get("subscription_id")
        customer_email = event_data.get("customer", {}).get("email")
        items = event_data.get("items", [])
        price_id = items[0].get("price", {}).get("id") if items else None
        plan = PADDLE_PRICE_TO_PLAN.get(price_id, "starter")

        if subscription_id and customer_email:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE businesses
                SET subscription_status = 'active',
                    plan = %s,
                    paddle_subscription_id = %s
                WHERE id = (
                    SELECT business_id FROM users WHERE email = %s
                )
            """, (plan, subscription_id, customer_email))
            conn.commit()
            conn.close()

    # Handle subscription events
    elif event_type in ["subscription.created", "subscription.updated"]:
        subscription_id = event_data.get("id")
        status = event_data.get("status")
        items = event_data.get("items", [])
        price_id = items[0].get("price", {}).get("id") if items else None
        plan = PADDLE_PRICE_TO_PLAN.get(price_id, "starter")

        status_map = {
            "active": "active",
            "trialing": "trialing",
            "canceled": "inactive",
            "past_due": "inactive",
            "paused": "inactive"
        }
        app_status = status_map.get(status, "inactive")

        if subscription_id:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE businesses
                SET subscription_status = %s,
                    plan = %s,
                    paddle_subscription_id = %s
                WHERE paddle_subscription_id = %s
            """, (app_status, plan, subscription_id, subscription_id))
            conn.commit()
            conn.close()

    elif event_type == "subscription.canceled":
        subscription_id = event_data.get("id")
        if subscription_id:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE businesses
                SET subscription_status = 'inactive'
                WHERE paddle_subscription_id = %s
            """, (subscription_id,))
            conn.commit()
            conn.close()

    return {"status": "ok"}

# =========================
# PUBLIC DEMO ENDPOINT
# =========================

@app.post("/demo/chat")
def demo_chat(data: ChatRequest):
    messages = [{"role": "system", "content": DEMO_BUSINESS_DATA}]

    if data.history:
        history = data.history[-20:]
        for h in history:
            if h.role in ["user", "assistant"]:
                messages.append({"role": h.role, "content": h.content})

    messages.append({"role": "user", "content": data.message})

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=500,
        messages=messages
    )
    return {"reply": response.choices[0].message.content}