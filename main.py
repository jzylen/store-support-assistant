from fastapi import FastAPI, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from database import init_db, get_connection
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
from openai import OpenAI
import uuid

# -----------------------
# APP SETUP
# -----------------------

app = FastAPI(title="Relixo API", version="1.0.0")

security = HTTPBearer()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://store-support-frontend.onrender.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

client = OpenAI()

SECRET_KEY = "relixo_super_secret_key_123456789"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto"
)

# -----------------------
# PLAN LIMITS
# -----------------------

PLAN_LIMITS = {
    "starter": 1000,
    "growth": 5000,
    "pro": 15000
}

# -----------------------
# MODELS
# -----------------------

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


# -----------------------
# AUTH HELPERS
# -----------------------

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


# -----------------------
# REGISTER
# -----------------------

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


# -----------------------
# LOGIN
# -----------------------

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


# -----------------------
# BUSINESS SETUP
# -----------------------

@app.post("/business/setup")
def setup_business(
    request: BusinessSetupRequest,
    credentials: HTTPAuthorizationCredentials = Security(security)
):
    token = credentials.credentials
    email = get_current_user(token)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT business_id FROM users WHERE email = ?", (email,))
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


# -----------------------
# CHAT (WITH LIMIT ENFORCEMENT)
# -----------------------

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

    # increment usage
    cursor.execute("""
        UPDATE businesses
        SET message_count = message_count + 1
        WHERE id = ?
    """, (business_id,))
    conn.commit()
    conn.close()

    return {"reply": response.output_text}
