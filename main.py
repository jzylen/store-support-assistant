from fastapi import FastAPI, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from database import init_db, get_connection
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
from openai import OpenAI
import uuid
import os

app = FastAPI(title="Relixo API", version="1.0.0")

security = HTTPBearer()

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
            INSERT INTO businesses (id, name, created_at)
            VALUES (?, ?, ?)
        """, (business_id, data.business_name, datetime.utcnow().isoformat()))

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
# CHAT (PROTECTED)
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

    cursor.execute("SELECT business_id FROM users WHERE email = ?", (email,))
    result = cursor.fetchone()

    if not result:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    business_id = result["business_id"]

    cursor.execute("SELECT data FROM businesses WHERE id = ?", (business_id,))
    business = cursor.fetchone()

    conn.close()

    if not business or not business["data"]:
        return {"reply": "No business data configured yet."}

    response = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": business["data"]},
            {"role": "user", "content": data.message}
        ]
    )

    return {"reply": response.output_text}
