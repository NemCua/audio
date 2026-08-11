"""Dashboard quản trị local cho Threads Video Maker.

Chỉ lắng nghe 127.0.0.1 và thao tác trực tiếp Neon qua DATABASE_URL trong .env.
Chạy: python3 threads/admin_server.py
"""
from __future__ import annotations

import os
import uuid
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg2.extras
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

try:
    from .account_api import DEFAULT_CREDIT_PRICING, db, migrate
except ImportError:
    from account_api import DEFAULT_CREDIT_PRICING, db, migrate

HERE = Path(__file__).resolve().parent
PORT = int(os.getenv("ADMIN_PORT", "8012"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    migrate()
    yield


app = FastAPI(title="Threads Local Admin", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.middleware("http")
async def local_only(request: Request, call_next):
    if request.client and request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Dashboard chỉ được dùng trên máy local")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin", "")
        allowed = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
        if origin and origin not in allowed:
            raise HTTPException(403, "Origin không hợp lệ")
    return await call_next(request)


def rows(cur) -> list[dict]:
    return [dict(item) for item in cur.fetchall()]


class CreditAdjustment(BaseModel):
    amount: int
    reason: str = Field(min_length=2, max_length=200)


class PricingUpdate(BaseModel):
    prices: dict[str, int]


@app.get("/")
def dashboard_page():
    return FileResponse(HERE / "admin.html")


@app.get("/api/dashboard")
def dashboard():
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*)::int AS users,COALESCE(SUM(balance),0)::bigint AS total_credit FROM users")
            summary = dict(cur.fetchone())
            cur.execute("SELECT COUNT(*)::int AS active_sessions FROM auth_sessions WHERE revoked_at IS NULL AND expires_at>NOW()")
            summary.update(cur.fetchone())
            cur.execute("SELECT COUNT(*)::int AS exports_today,COALESCE(SUM(cost),0)::bigint AS credits_today FROM export_reservations WHERE status='consumed' AND finished_at>=CURRENT_DATE")
            summary.update(cur.fetchone())
            cur.execute("SELECT kind,unit_cost,updated_at FROM credit_pricing ORDER BY kind")
            pricing = rows(cur)
            cur.execute("""SELECT r.id,r.user_id,u.email,r.cost,r.status,r.created_at,r.finished_at
                           FROM export_reservations r JOIN users u ON u.id=r.user_id
                           ORDER BY r.created_at DESC LIMIT 20""")
            reservations = rows(cur)
    return {"summary": summary, "pricing": pricing, "reservations": reservations}


@app.get("/api/users")
def list_users(q: str = "", limit: int = 100):
    limit = max(1, min(limit, 300))
    search = f"%{q.strip()}%"
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT u.id,u.email,u.display_name,u.balance,u.created_at,
                                  COUNT(s.id) FILTER (WHERE s.revoked_at IS NULL AND s.expires_at>NOW())::int AS active_sessions
                           FROM users u LEFT JOIN auth_sessions s ON s.user_id=u.id
                           WHERE (%s='' OR u.email ILIKE %s OR COALESCE(u.display_name,'') ILIKE %s OR u.id::text=%s)
                           GROUP BY u.id ORDER BY u.created_at DESC LIMIT %s""",
                        (q.strip(), search, search, q.strip(), limit))
            return rows(cur)


@app.get("/api/users/{user_id}")
def user_detail(user_id: int):
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id,email,display_name,balance,created_at,settings FROM users WHERE id=%s", (user_id,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(404, "Không tìm thấy tài khoản")
            cur.execute("SELECT id,amount,balance_after,kind,reference,metadata,created_at FROM credit_ledger WHERE user_id=%s ORDER BY id DESC LIMIT 100", (user_id,))
            ledger = rows(cur)
            cur.execute("SELECT id,device_name,revoked_at,expires_at,created_at,last_used_at FROM auth_sessions WHERE user_id=%s ORDER BY created_at DESC LIMIT 50", (user_id,))
            sessions = rows(cur)
    return {"user": dict(user), "ledger": ledger, "sessions": sessions}


@app.post("/api/users/{user_id}/credits")
def adjust_credit(user_id: int, req: CreditAdjustment):
    if req.amount == 0 or abs(req.amount) > 100_000_000:
        raise HTTPException(400, "Số credit điều chỉnh không hợp lệ")
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT balance FROM users WHERE id=%s FOR UPDATE", (user_id,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(404, "Không tìm thấy tài khoản")
            new_balance = int(user["balance"] or 0) + req.amount
            if new_balance < 0:
                raise HTTPException(400, "Số dư sau điều chỉnh không thể âm")
            key = f"local-admin:{uuid.uuid4()}"
            cur.execute("UPDATE users SET balance=%s WHERE id=%s", (new_balance, user_id))
            cur.execute("""INSERT INTO credit_ledger(user_id,amount,balance_after,kind,reference,idempotency_key,metadata)
                           VALUES(%s,%s,%s,'local_admin',%s,%s,%s) RETURNING id,created_at""",
                        (user_id, req.amount, new_balance, req.reason.strip(), key, psycopg2.extras.Json({"source": "local-admin"})))
            entry = dict(cur.fetchone())
    return {"ok": True, "balance": new_balance, "ledger": entry}


@app.post("/api/users/{user_id}/revoke-sessions")
def revoke_sessions(user_id: int):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE auth_sessions SET revoked_at=NOW() WHERE user_id=%s AND revoked_at IS NULL", (user_id,))
            count = cur.rowcount
    return {"ok": True, "revoked": count}


@app.put("/api/pricing")
def update_pricing(req: PricingUpdate):
    if set(req.prices) != set(DEFAULT_CREDIT_PRICING):
        raise HTTPException(400, "Phải gửi đủ đúng 7 loại card")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 1_000_000 for value in req.prices.values()):
        raise HTTPException(400, "Giá credit không hợp lệ")
    with db() as conn:
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO credit_pricing(kind,unit_cost,updated_at) VALUES(%s,%s,NOW())
                               ON CONFLICT(kind) DO UPDATE SET unit_cost=EXCLUDED.unit_cost,updated_at=NOW()""",
                            list(req.prices.items()))
    return {"ok": True, "pricing": req.prices}


if __name__ == "__main__":
    import uvicorn

    url = f"http://127.0.0.1:{PORT}"
    print(f"Threads Admin: {url}")
    webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=PORT)
