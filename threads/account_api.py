"""Account API v2 dùng lại Neon database cũ.

Khác auth_server.py cũ: pool kết nối lazy, retry khi Neon cold-start, access +
refresh session có thể revoke, credit ledger bất biến và export reservation.
"""
import hashlib
import os
import re
import secrets
import time
import uuid
import threading
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import bcrypt
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from psycopg2.pool import ThreadedConnectionPool
from pydantic import BaseModel, Field

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
AUTH_SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "").strip()
AUTH_ADMIN_KEY = os.getenv("AUTH_ADMIN_KEY", "").strip()
ACCESS_MINUTES = int(os.getenv("AUTH_ACCESS_MINUTES", "30"))
REFRESH_DAYS = int(os.getenv("AUTH_REFRESH_DAYS", "30"))
SIGNUP_CREDITS = int(os.getenv("AUTH_SIGNUP_CREDITS", "0"))
EXPORT_CREDIT_COST = int(os.getenv("EXPORT_CREDIT_COST", "1"))
RESERVATION_MINUTES = int(os.getenv("EXPORT_RESERVATION_MINUTES", "30"))
DEFAULT_CREDIT_PRICING = {
    "ai_script_short": 100,
    "ai_script_medium": 500,
    "ai_script_long": 1000,
    "ai_script_prompt_word": 1,
    "post": 100,
    "comment": 100,
    "reply": 100,
    "text": 100,
    "note": 100,
    "image": 150,
    "sticker": 150,
    "mess": 20,
    "audio": 10,
}

if not DATABASE_URL:
    raise RuntimeError("Thiếu DATABASE_URL")
if not AUTH_SECRET_KEY:
    raise RuntimeError("Thiếu AUTH_SECRET_KEY; không được dùng secret ngẫu nhiên trong production")

router = APIRouter(prefix="/auth", tags=["account-v2"])
bearer = HTTPBearer(auto_error=False)
_pool: ThreadedConnectionPool | None = None
_tts_hits: dict[int, deque[float]] = defaultdict(deque)
_tts_hits_lock = threading.Lock()


def _new_pool() -> ThreadedConnectionPool:
    separator = "&" if "?" in DATABASE_URL else "?"
    dsn = DATABASE_URL if "connect_timeout=" in DATABASE_URL else f"{DATABASE_URL}{separator}connect_timeout=8"
    return ThreadedConnectionPool(1, int(os.getenv("DB_POOL_MAX", "8")), dsn=dsn)


def pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = _new_pool()
    return _pool


@contextmanager
def db():
    """Mượn connection từ pool, retry một lần nếu Neon đã đóng socket idle."""
    global _pool
    conn = None
    for attempt in range(2):
        try:
            conn = pool().getconn()
            if conn.closed:
                raise psycopg2.InterfaceError("connection closed")
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            break
        except (psycopg2.InterfaceError, psycopg2.OperationalError) as exc:
            if conn is not None:
                try: pool().putconn(conn, close=True)
                except Exception: pass
                conn = None
            if attempt == 0:
                try: pool().closeall()
                except Exception: pass
                _pool = None
                time.sleep(0.25)
                continue
            raise HTTPException(503, f"Database tạm thời không kết nối được: {exc}")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool().putconn(conn)


def migrate() -> None:
    statements = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS settings JSONB DEFAULT '{}'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS balance INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS account_status TEXT NOT NULL DEFAULT 'active'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS status_reason TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS status_changed_at TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
        """CREATE TABLE IF NOT EXISTS auth_sessions (
            id UUID PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            refresh_hash TEXT NOT NULL, device_name TEXT DEFAULT '', revoked_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW(), last_used_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS auth_sessions_user_idx ON auth_sessions(user_id)",
        """CREATE TABLE IF NOT EXISTS credit_ledger (
            id BIGSERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            amount INTEGER NOT NULL, balance_after INTEGER NOT NULL, kind TEXT NOT NULL,
            reference TEXT, idempotency_key TEXT, metadata JSONB DEFAULT '{}', created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(user_id, idempotency_key)
        )""",
        """CREATE TABLE IF NOT EXISTS export_reservations (
            id UUID PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), cost INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'reserved', idempotency_key TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW(), finished_at TIMESTAMPTZ,
            UNIQUE(user_id, idempotency_key)
        )""",
        """CREATE TABLE IF NOT EXISTS ai_script_reservations (
            id UUID PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), cost INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'reserved', idempotency_key TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(), finished_at TIMESTAMPTZ,
            UNIQUE(user_id, idempotency_key)
        )""",
        """CREATE TABLE IF NOT EXISTS credit_pricing (
            kind TEXT PRIMARY KEY, unit_cost INTEGER NOT NULL CHECK (unit_cost >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS api_rate_limits (
            bucket TEXT NOT NULL, rate_key TEXT NOT NULL, window_start BIGINT NOT NULL, hits BIGINT NOT NULL,
            PRIMARY KEY(bucket, rate_key)
        )""",
        """CREATE TABLE IF NOT EXISTS tts_usage (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, usage_date DATE NOT NULL,
            chars BIGINT NOT NULL DEFAULT 0, requests INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY(user_id, usage_date)
        )""",
        """CREATE TABLE IF NOT EXISTS admin_audit_log (
            id BIGSERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), action TEXT NOT NULL,
            reason TEXT DEFAULT '', metadata JSONB DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS admin_audit_user_idx ON admin_audit_log(user_id, created_at DESC)",
    ]
    with db() as conn:
        with conn.cursor() as cur:
            # Không để một DDL đang chờ lock khiến toàn bộ API treo vô hạn ở
            # startup. Docker sẽ restart và thử lại sau nếu Neon tạm bận.
            cur.execute("SET LOCAL lock_timeout = '5s'")
            cur.execute("SET LOCAL statement_timeout = '60s'")
            for statement in statements:
                cur.execute(statement)
            cur.executemany(
                "INSERT INTO credit_pricing(kind,unit_cost) VALUES(%s,%s) ON CONFLICT(kind) DO NOTHING",
                list(DEFAULT_CREDIT_PRICING.items()),
            )


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def _verify_password(password: str, hashed: str) -> bool:
    try: return bcrypt.checkpw(password.encode(), hashed.encode())
    except (ValueError, TypeError): return False


def _refresh_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _client_ip(request: Request) -> str:
    # Render thêm địa chỉ client ở hop cuối; không đưa IP thô vào database.
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def _rate_key(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def _enforce_rate_limit(bucket: str, key: str, limit: int, window_seconds: int, units: int = 1) -> None:
    now_window = int(time.time()) // window_seconds
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO api_rate_limits(bucket,rate_key,window_start,hits) VALUES(%s,%s,%s,%s)
                   ON CONFLICT(bucket,rate_key) DO UPDATE SET
                     window_start=CASE WHEN api_rate_limits.window_start=EXCLUDED.window_start THEN api_rate_limits.window_start ELSE EXCLUDED.window_start END,
                     hits=CASE WHEN api_rate_limits.window_start=EXCLUDED.window_start THEN api_rate_limits.hits+EXCLUDED.hits ELSE EXCLUDED.hits END
                   RETURNING hits""",
                (bucket, _rate_key(key), now_window, units),
            )
            hits = int(cur.fetchone()[0])
    if hits > limit:
        raise HTTPException(429, "Bạn thao tác quá nhanh, hãy thử lại sau", headers={"Retry-After": str(window_seconds)})


def consume_tts_quota(identity: dict, chars: int) -> None:
    """Giữ quota trước khi gọi dịch vụ TTS để request đồng thời không vượt trần."""
    if identity.get("id", 0) <= 0:
        return
    chars = int(chars)
    if chars <= 0:
        raise HTTPException(400, "Nội dung TTS rỗng")
    per_minute = max(1, int(os.getenv("TTS_CHARS_PER_MINUTE", "30000")))
    daily = max(per_minute, int(os.getenv("TTS_DAILY_CHAR_QUOTA", "100000")))
    _enforce_rate_limit("tts_chars", str(identity["id"]), per_minute, 60, chars)
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tts_usage(user_id,usage_date,chars,requests) VALUES(%s,CURRENT_DATE,%s,1)
                   ON CONFLICT(user_id,usage_date) DO UPDATE SET
                     chars=tts_usage.chars+EXCLUDED.chars, requests=tts_usage.requests+1, updated_at=NOW()
                   WHERE tts_usage.chars+EXCLUDED.chars<=%s RETURNING chars""",
                (identity["id"], chars, daily),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(429, f"Đã vượt quota TTS {daily:,} ký tự/ngày")


def _access_token(user_id: int, email: str, session_id: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user_id), "email": email, "sid": session_id, "type": "access", "iat": now, "exp": now + timedelta(minutes=ACCESS_MINUTES)}, AUTH_SECRET_KEY, algorithm="HS256")


def _user(row) -> dict:
    return {"id": row["id"], "email": row["email"], "display_name": row["display_name"] or "", "balance": row["balance"] or 0}


def _ensure_account_active(user) -> None:
    account_status = user.get("account_status", "active")
    if account_status == "suspended":
        raise HTTPException(403, "Tài khoản đang bị tạm khóa. Vui lòng liên hệ quản trị viên")
    if account_status == "deleted" or user.get("deleted_at") is not None:
        raise HTTPException(403, "Tài khoản đã bị vô hiệu hóa")


def _create_session(cur, user, device_name: str = "") -> dict:
    session_id, refresh = str(uuid.uuid4()), secrets.token_urlsafe(48)
    expires = datetime.now(timezone.utc) + timedelta(days=REFRESH_DAYS)
    cur.execute("INSERT INTO auth_sessions(id,user_id,refresh_hash,device_name,expires_at) VALUES(%s,%s,%s,%s,%s)", (session_id, user["id"], _refresh_hash(refresh), device_name[:120], expires))
    access = _access_token(user["id"], user["email"], session_id)
    return {"token": access, "access_token": access, "refresh_token": refresh, "expires_in": ACCESS_MINUTES * 60, "user": _user(user)}


def current_identity(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if not credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Thiếu access token")
    try:
        payload = jwt.decode(credentials.credentials, AUTH_SECRET_KEY, algorithms=["HS256"])
        if payload.get("type") != "access": raise JWTError("wrong token type")
        user_id, session_id = int(payload["sub"]), str(payload["sid"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Access token không hợp lệ hoặc đã hết hạn")
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT u.id,u.email,u.display_name,u.balance,u.account_status,u.deleted_at,s.id AS session_id
                           FROM users u JOIN auth_sessions s ON s.user_id=u.id
                           WHERE u.id=%s AND s.id=%s AND s.revoked_at IS NULL AND s.expires_at>NOW()
                             AND u.account_status='active' AND u.deleted_at IS NULL""", (user_id, session_id))
            row = cur.fetchone()
    if not row: raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Phiên đăng nhập đã bị thu hồi")
    return dict(row)


def tts_identity(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    """Bật REQUIRE_TTS_AUTH=1 ở production; local dev giữ tương thích UI cũ."""
    if os.getenv("REQUIRE_TTS_AUTH", "0").lower() not in ("1", "true", "yes"):
        return {"id": 0, "email": "local-dev"}
    identity = current_identity(credentials)
    limit = max(1, int(os.getenv("TTS_REQUESTS_PER_MINUTE", "30")))
    now = time.monotonic()
    with _tts_hits_lock:
        hits = _tts_hits[identity["id"]]
        while hits and now - hits[0] >= 60: hits.popleft()
        if len(hits) >= limit:
            raise HTTPException(429, "Bạn tạo TTS quá nhanh, hãy thử lại sau")
        hits.append(now)
    return identity


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254); password: str = Field(min_length=8, max_length=128); display_name: str = Field(default="", max_length=120); device_name: str = Field(default="", max_length=120)
class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254); password: str = Field(min_length=1, max_length=128); device_name: str = Field(default="", max_length=120)
class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=256)
class ReserveRequest(BaseModel):
    idempotency_key: str
    type_counts: dict[str, int] = Field(default_factory=dict)
class ExportQuoteRequest(BaseModel):
    type_counts: dict[str, int] = Field(default_factory=dict)
class ReservationRequest(BaseModel):
    reservation_id: str
class AdminCreditRequest(BaseModel):
    user_id: int; amount: int; reference: str = "manual"; idempotency_key: str
class AdminPricingRequest(BaseModel):
    prices: dict[str, int] = Field(default_factory=dict)


def _validated_type_counts(type_counts: dict[str, int]) -> dict[str, int]:
    allowed = set(DEFAULT_CREDIT_PRICING)
    counts: dict[str, int] = {}
    for kind, raw_count in type_counts.items():
        if kind not in allowed:
            continue
        if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0 or raw_count > 100_000:
            raise HTTPException(400, "Số lượng nội dung không hợp lệ")
        counts[kind] = raw_count
    return counts


def _credit_pricing() -> dict[str, int]:
    """Đọc bảng giá live; app đã cài không cần cập nhật khi chủ app đổi giá."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT kind,unit_cost FROM credit_pricing")
            stored = {kind: int(cost) for kind, cost in cur.fetchall()}
    return {kind: stored.get(kind, default) for kind, default in DEFAULT_CREDIT_PRICING.items()}


def _export_credit_cost(type_counts: dict[str, int], pricing: dict[str, int] | None = None) -> int:
    counts = _validated_type_counts(type_counts)
    prices = pricing or _credit_pricing()
    return sum(counts.get(kind, 0) * prices[kind] for kind in DEFAULT_CREDIT_PRICING)


def reserve_ai_script_credit(identity: dict, idempotency_key: str, length: str, prompt: str) -> dict:
    """Giữ credit trước khi gọi AI; local-dev không chạm database."""
    if identity.get("id") == 0:
        return {"id": f"local-{uuid.uuid4()}", "cost": 0, "balance": 0, "status": "reserved"}
    key = idempotency_key.strip()
    if not key or len(key) > 160: raise HTTPException(400, "Idempotency key AI không hợp lệ")
    if length not in ("short", "medium", "long"):
        raise HTTPException(400, "Độ dài AI không hợp lệ")
    pricing = _credit_pricing()
    prompt_words = len(re.findall(r"[^\W_]+", prompt, flags=re.UNICODE))
    base_cost = pricing[f"ai_script_{length}"]
    word_cost = pricing["ai_script_prompt_word"]
    cost = base_cost + prompt_words * word_cost
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ai_script_reservations WHERE user_id=%s AND idempotency_key=%s", (identity["id"], key)); existing = cur.fetchone()
            if existing:
                cur.execute("SELECT balance FROM users WHERE id=%s", (identity["id"],)); balance = cur.fetchone()["balance"]
                return {**dict(existing), "balance": balance}
            cur.execute("SELECT balance FROM users WHERE id=%s FOR UPDATE", (identity["id"],)); row = cur.fetchone()
            if not row: raise HTTPException(404, "Không tìm thấy tài khoản")
            balance = int(row["balance"] or 0)
            if balance < cost: raise HTTPException(402, f"Không đủ credit để tạo kịch bản AI (cần {cost})")
            reservation_id, new_balance = str(uuid.uuid4()), balance - cost
            cur.execute("UPDATE users SET balance=%s WHERE id=%s", (new_balance, identity["id"]))
            cur.execute("INSERT INTO ai_script_reservations(id,user_id,cost,idempotency_key) VALUES(%s,%s,%s,%s)", (reservation_id, identity["id"], cost, key))
            cur.execute("INSERT INTO credit_ledger(user_id,amount,balance_after,kind,reference,idempotency_key,metadata) VALUES(%s,%s,%s,'ai_script_reserve',%s,%s,%s)", (identity["id"], -cost, new_balance, reservation_id, f"ai-script-reserve:{reservation_id}", psycopg2.extras.Json({"request_key": key, "length": length, "prompt_words": prompt_words, "base_cost": base_cost, "word_cost": word_cost})))
            return {"id": reservation_id, "cost": cost, "balance": new_balance, "status": "reserved"}


def finish_ai_script_credit(identity: dict, reservation_id: str, success: bool) -> dict:
    if identity.get("id") == 0: return {"ok": True, "balance": 0}
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ai_script_reservations WHERE id=%s AND user_id=%s FOR UPDATE", (reservation_id, identity["id"])); reservation = cur.fetchone()
            if not reservation: raise HTTPException(404, "Không tìm thấy giao dịch AI")
            if reservation["status"] != "reserved":
                cur.execute("SELECT balance FROM users WHERE id=%s", (identity["id"],)); return {"ok": True, "balance": cur.fetchone()["balance"]}
            if success:
                cur.execute("UPDATE ai_script_reservations SET status='consumed',finished_at=NOW() WHERE id=%s", (reservation_id,))
                cur.execute("SELECT balance FROM users WHERE id=%s", (identity["id"],)); balance = cur.fetchone()["balance"]
            else:
                cur.execute("UPDATE users SET balance=balance+%s WHERE id=%s RETURNING balance", (reservation["cost"], identity["id"])); balance = cur.fetchone()["balance"]
                cur.execute("UPDATE ai_script_reservations SET status='refunded',finished_at=NOW() WHERE id=%s", (reservation_id,))
                cur.execute("INSERT INTO credit_ledger(user_id,amount,balance_after,kind,reference,idempotency_key) VALUES(%s,%s,%s,'ai_script_refund',%s,%s)", (identity["id"], reservation["cost"], balance, reservation_id, f"ai-script-refund:{reservation_id}"))
            return {"ok": True, "balance": balance}


@router.post("/register")
def register(req: RegisterRequest, request: Request):
    _enforce_rate_limit("register_ip", _client_ip(request), int(os.getenv("AUTH_REGISTER_PER_HOUR", "10")), 3600)
    email = req.email.strip().lower()
    if "@" not in email or len(email) > 254: raise HTTPException(400, "Email không hợp lệ")
    if len(req.password) < 8: raise HTTPException(400, "Mật khẩu tối thiểu 8 ký tự")
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT 1 FROM users WHERE email=%s", (email,))
                if cur.fetchone(): raise HTTPException(409, "Email đã được đăng ký")
                cur.execute("INSERT INTO users(email,hashed_pw,display_name,balance) VALUES(%s,%s,%s,%s) RETURNING *", (email, _hash_password(req.password), req.display_name.strip(), SIGNUP_CREDITS))
                user = cur.fetchone()
                if SIGNUP_CREDITS:
                    cur.execute("INSERT INTO credit_ledger(user_id,amount,balance_after,kind,reference,idempotency_key) VALUES(%s,%s,%s,'signup','signup',%s)", (user["id"], SIGNUP_CREDITS, SIGNUP_CREDITS, f"signup:{user['id']}"))
                return _create_session(cur, user, req.device_name)
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "Email đã được đăng ký")


@router.post("/login")
def login(req: LoginRequest, request: Request):
    email = req.email.strip().lower()
    _enforce_rate_limit("login_ip", _client_ip(request), int(os.getenv("AUTH_LOGIN_IP_PER_15_MIN", "30")), 900)
    _enforce_rate_limit("login_account", email, int(os.getenv("AUTH_LOGIN_ACCOUNT_PER_15_MIN", "10")), 900)
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE email=%s", (email,)); user = cur.fetchone()
            if not user or not _verify_password(req.password, user["hashed_pw"]): raise HTTPException(401, "Email hoặc mật khẩu không đúng")
            _ensure_account_active(user)
            return _create_session(cur, user, req.device_name)


@router.post("/refresh")
def refresh(req: RefreshRequest):
    token_hash = _refresh_hash(req.refresh_token)
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT s.*,u.email,u.display_name,u.balance,u.account_status,u.deleted_at
                           FROM auth_sessions s JOIN users u ON u.id=s.user_id
                           WHERE s.refresh_hash=%s AND s.revoked_at IS NULL AND s.expires_at>NOW()
                             AND u.account_status='active' AND u.deleted_at IS NULL FOR UPDATE""", (token_hash,)); row = cur.fetchone()
            if not row: raise HTTPException(401, "Refresh token không hợp lệ hoặc đã hết hạn")
            new_refresh = secrets.token_urlsafe(48)
            cur.execute("UPDATE auth_sessions SET refresh_hash=%s,last_used_at=NOW() WHERE id=%s", (_refresh_hash(new_refresh), row["id"]))
            access = _access_token(row["user_id"], row["email"], str(row["id"]))
            return {"token": access, "access_token": access, "refresh_token": new_refresh, "expires_in": ACCESS_MINUTES * 60, "user": {"id": row["user_id"], "email": row["email"], "display_name": row["display_name"] or "", "balance": row["balance"] or 0}}


@router.post("/logout")
def logout(identity: dict = Depends(current_identity)):
    with db() as conn:
        with conn.cursor() as cur: cur.execute("UPDATE auth_sessions SET revoked_at=NOW() WHERE id=%s", (identity["session_id"],))
    return {"ok": True}


@router.get("/me")
def me(identity: dict = Depends(current_identity)):
    return _user(identity)


@router.get("/credits/ledger")
def ledger(identity: dict = Depends(current_identity)):
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id,amount,balance_after,kind,reference,metadata,created_at FROM credit_ledger WHERE user_id=%s ORDER BY id DESC LIMIT 100", (identity["id"],))
            return [dict(row) for row in cur.fetchall()]


@router.get("/pricing")
def get_pricing(identity: dict = Depends(current_identity)):
    return {"pricing": _credit_pricing(), "version": "database-v1"}


@router.post("/exports/quote")
def quote_export(req: ExportQuoteRequest, identity: dict = Depends(current_identity)):
    pricing = _credit_pricing()
    cost = _export_credit_cost(req.type_counts, pricing)
    return {
        "cost": cost,
        "balance": identity["balance"] or 0,
        "can_export": (identity["balance"] or 0) >= cost,
        "pricing": pricing,
        "pricing_version": "database-v1",
    }


@router.post("/exports/reserve")
def reserve_export(req: ReserveRequest, identity: dict = Depends(current_identity)):
    counts = _validated_type_counts(req.type_counts)
    if not any(counts.values()):
        raise HTTPException(400, "Video chưa có nội dung tính credit")
    pricing = _credit_pricing()
    cost = sum(counts.get(kind, 0) * pricing[kind] for kind in DEFAULT_CREDIT_PRICING)
    if not req.idempotency_key.strip(): raise HTTPException(400, "Thiếu idempotency_key")
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Hoàn tự động các job cũ bị app tắt/crash trước khi tạo reservation mới.
            cur.execute("SELECT id,cost FROM export_reservations WHERE user_id=%s AND status='reserved' AND expires_at<=NOW() FOR UPDATE", (identity["id"],))
            for expired in cur.fetchall():
                cur.execute("UPDATE users SET balance=balance+%s WHERE id=%s RETURNING balance", (expired["cost"], identity["id"])); restored = cur.fetchone()["balance"]
                cur.execute("UPDATE export_reservations SET status='expired_refunded',finished_at=NOW() WHERE id=%s", (expired["id"],))
                cur.execute("INSERT INTO credit_ledger(user_id,amount,balance_after,kind,reference,idempotency_key) VALUES(%s,%s,%s,'export_expired_refund',%s,%s) ON CONFLICT(user_id,idempotency_key) DO NOTHING", (identity["id"], expired["cost"], restored, str(expired["id"]), f"expired-refund:{expired['id']}"))
            cur.execute("SELECT * FROM export_reservations WHERE user_id=%s AND idempotency_key=%s", (identity["id"], req.idempotency_key)); existing = cur.fetchone()
            if existing: return dict(existing)
            cur.execute("SELECT balance FROM users WHERE id=%s FOR UPDATE", (identity["id"],)); balance = cur.fetchone()["balance"]
            if balance < cost: raise HTTPException(402, "Không đủ credit")
            new_balance, reservation_id = balance - cost, str(uuid.uuid4())
            cur.execute("UPDATE users SET balance=%s WHERE id=%s", (new_balance, identity["id"]))
            cur.execute("INSERT INTO export_reservations(id,user_id,cost,idempotency_key,expires_at) VALUES(%s,%s,%s,%s,NOW()+(%s || ' minutes')::interval) RETURNING *", (reservation_id, identity["id"], cost, req.idempotency_key, RESERVATION_MINUTES)); reservation = cur.fetchone()
            cur.execute("INSERT INTO credit_ledger(user_id,amount,balance_after,kind,reference,idempotency_key) VALUES(%s,%s,%s,'export_reserve',%s,%s)", (identity["id"], -cost, new_balance, reservation_id, f"reserve:{reservation_id}"))
            return {**dict(reservation), "balance": new_balance}


@router.post("/exports/consume")
def consume_export(req: ReservationRequest, identity: dict = Depends(current_identity)):
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT status,expires_at FROM export_reservations WHERE id=%s AND user_id=%s FOR UPDATE", (req.reservation_id, identity["id"])); reservation = cur.fetchone()
            if not reservation: raise HTTPException(404, "Không tìm thấy reservation")
            if reservation["status"] == "consumed": return {"ok": True, "already_consumed": True}
            if reservation["status"] != "reserved" or reservation["expires_at"] <= datetime.now(timezone.utc):
                raise HTTPException(409, "Reservation không còn ở trạng thái reserved")
            cur.execute("UPDATE export_reservations SET status='consumed',finished_at=NOW() WHERE id=%s", (req.reservation_id,))
    return {"ok": True}


@router.post("/exports/refund")
def refund_export(req: ReservationRequest, identity: dict = Depends(current_identity)):
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM export_reservations WHERE id=%s AND user_id=%s FOR UPDATE", (req.reservation_id, identity["id"])); reservation = cur.fetchone()
            if not reservation: raise HTTPException(404, "Không tìm thấy reservation")
            if reservation["status"] in ("refunded", "expired_refunded"):
                cur.execute("SELECT balance FROM users WHERE id=%s", (identity["id"],))
                return {"ok": True, "already_refunded": True, "balance": cur.fetchone()["balance"]}
            if reservation["status"] != "reserved": raise HTTPException(409, "Reservation không thể hoàn")
            cur.execute("UPDATE users SET balance=balance+%s WHERE id=%s RETURNING balance", (reservation["cost"], identity["id"])); balance = cur.fetchone()["balance"]
            cur.execute("UPDATE export_reservations SET status='refunded',finished_at=NOW() WHERE id=%s", (req.reservation_id,))
            cur.execute("INSERT INTO credit_ledger(user_id,amount,balance_after,kind,reference,idempotency_key) VALUES(%s,%s,%s,'export_refund',%s,%s)", (identity["id"], reservation["cost"], balance, req.reservation_id, f"refund:{req.reservation_id}"))
    return {"ok": True, "balance": balance}


@router.post("/admin/credits")
def admin_credit(req: AdminCreditRequest, x_admin_key: str = Header(default="")):
    if not AUTH_ADMIN_KEY or not secrets.compare_digest(x_admin_key, AUTH_ADMIN_KEY): raise HTTPException(403, "Admin key không hợp lệ")
    if req.amount == 0 or abs(req.amount) > 1_000_000: raise HTTPException(400, "Amount không hợp lệ")
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id,amount,balance_after FROM credit_ledger WHERE user_id=%s AND idempotency_key=%s", (req.user_id, req.idempotency_key)); existing = cur.fetchone()
            if existing: return dict(existing)
            cur.execute("UPDATE users SET balance=balance+%s WHERE id=%s RETURNING balance", (req.amount, req.user_id)); row = cur.fetchone()
            if not row: raise HTTPException(404, "User không tồn tại")
            cur.execute("INSERT INTO credit_ledger(user_id,amount,balance_after,kind,reference,idempotency_key) VALUES(%s,%s,%s,'admin',%s,%s) RETURNING id", (req.user_id, req.amount, row["balance"], req.reference, req.idempotency_key)); ledger_id = cur.fetchone()["id"]
            return {"id": ledger_id, "balance": row["balance"]}


@router.put("/admin/pricing")
def update_pricing(req: AdminPricingRequest, x_admin_key: str = Header(default="")):
    if not AUTH_ADMIN_KEY or not secrets.compare_digest(x_admin_key, AUTH_ADMIN_KEY):
        raise HTTPException(403, "Admin key không hợp lệ")
    if not req.prices:
        raise HTTPException(400, "Bảng giá không được để trống")
    unknown = set(req.prices) - set(DEFAULT_CREDIT_PRICING)
    if unknown:
        raise HTTPException(400, "Loại card không hợp lệ: " + ", ".join(sorted(unknown)))
    for kind, value in req.prices.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 1_000_000:
            raise HTTPException(400, f"Giá {kind} không hợp lệ")
    with db() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO credit_pricing(kind,unit_cost,updated_at) VALUES(%s,%s,NOW())
                   ON CONFLICT(kind) DO UPDATE SET unit_cost=EXCLUDED.unit_cost,updated_at=NOW()""",
                list(req.prices.items()),
            )
    return {"pricing": _credit_pricing(), "version": "database-v1"}
