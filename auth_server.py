"""
Auth service — port 8006
Endpoints: POST /auth/register, POST /auth/login, GET /auth/me
JWT access token, Neon Postgres, bcrypt passwords
"""
import os, secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import bcrypt
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel

load_dotenv()

# ── Config ───────────────────────────────────────────────────────
SECRET_KEY   = os.getenv("AUTH_SECRET_KEY", secrets.token_hex(32))
DATABASE_URL = os.getenv("DATABASE_URL", "")
ALGORITHM    = "HS256"
TOKEN_HOURS  = 72

def hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_pw(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())
bearer  = HTTPBearer()

# ── DB helpers ───────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id           SERIAL PRIMARY KEY,
                    email        TEXT UNIQUE NOT NULL,
                    hashed_pw    TEXT NOT NULL,
                    display_name TEXT DEFAULT '',
                    balance      INTEGER DEFAULT 0,
                    created_at   TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS topup_txns (
                    id         SERIAL PRIMARY KEY,
                    user_id    INTEGER NOT NULL REFERENCES users(id),
                    amount     INTEGER NOT NULL,
                    note       TEXT,
                    status     TEXT DEFAULT 'pending',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

init_db()

# ── App ──────────────────────────────────────────────────────────
app = FastAPI(title="Auth Service", docs_url="/auth/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Schemas ──────────────────────────────────────────────────────
class RegisterReq(BaseModel):
    email: str
    password: str
    display_name: str = ""

class LoginReq(BaseModel):
    email: str
    password: str

class ChangePasswordReq(BaseModel):
    old_password: str
    new_password: str

# ── Helpers ──────────────────────────────────────────────────────
def make_token(user_id: int, email: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
    return jwt.encode({"sub": str(user_id), "email": email, "exp": exp},
                      SECRET_KEY, algorithm=ALGORITHM)

def verify_token(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return int(payload["sub"])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Token khong hop le hoac da het han")

def get_user_by_id(user_id: int):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User khong ton tai")
    return row

def user_payload(row) -> dict:
    return {
        "id":           row["id"],
        "email":        row["email"],
        "display_name": row["display_name"],
        "balance":      row["balance"],
    }

# ── Endpoints ────────────────────────────────────────────────────
@app.post("/auth/register")
def register(req: RegisterReq):
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Mat khau toi thieu 6 ky tu")
    hashed = hash_pw(req.password)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users(email,hashed_pw,display_name) VALUES(%s,%s,%s) RETURNING id",
                    (req.email.lower().strip(), hashed, req.display_name.strip())
                )
                user_id = cur.fetchone()["id"]
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="Email da duoc dang ky")
    token = make_token(user_id, req.email)
    return {"token": token, "user": {"id": user_id, "email": req.email,
                                      "display_name": req.display_name, "balance": 0}}

@app.post("/auth/login")
def login(req: LoginReq):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE email=%s", (req.email.lower().strip(),))
            row = cur.fetchone()
    if not row or not verify_pw(req.password, row["hashed_pw"]):
        raise HTTPException(status_code=401, detail="Email hoac mat khau khong dung")
    token = make_token(row["id"], row["email"])
    return {"token": token, "user": user_payload(row)}

@app.get("/auth/me")
def me(user_id: int = Depends(verify_token)):
    row = get_user_by_id(user_id)
    return {**user_payload(row), "created_at": str(row["created_at"])}

@app.post("/auth/change-password")
def change_password(req: ChangePasswordReq, user_id: int = Depends(verify_token)):
    row = get_user_by_id(user_id)
    if not verify_pw(req.old_password, row["hashed_pw"]):
        raise HTTPException(status_code=400, detail="Mat khau cu khong dung")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Mat khau moi toi thieu 6 ky tu")
    hashed = hash_pw(req.new_password)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET hashed_pw=%s WHERE id=%s", (hashed, user_id))
    return {"ok": True}

@app.get("/auth/balance")
def get_balance(user_id: int = Depends(verify_token)):
    row = get_user_by_id(user_id)
    return {"balance": row["balance"]}

@app.get("/auth/transactions")
def get_transactions(user_id: int = Depends(verify_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id,amount,note,status,created_at FROM topup_txns "
                "WHERE user_id=%s ORDER BY created_at DESC LIMIT 50",
                (user_id,)
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("auth_server:app", host="0.0.0.0", port=8006, reload=True)
