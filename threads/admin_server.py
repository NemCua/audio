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


class AccountStatusUpdate(BaseModel):
    status: str
    reason: str = Field(min_length=2, max_length=300)


@app.get("/")
def dashboard_page():
    return FileResponse(HERE / "admin.html")


@app.get("/api/dashboard")
def dashboard():
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT COUNT(*) FILTER (WHERE account_status='active')::int AS users,
                                  COUNT(*) FILTER (WHERE account_status='suspended')::int AS suspended_users,
                                  COUNT(*) FILTER (WHERE account_status='deleted')::int AS deleted_users,
                                  COALESCE(SUM(balance) FILTER (WHERE account_status<>'deleted'),0)::bigint AS total_credit
                           FROM users""")
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
            cur.execute("""SELECT a.id,a.user_id,a.action,a.reason,a.metadata,a.created_at,u.email
                           FROM admin_audit_log a LEFT JOIN users u ON u.id=a.user_id
                           ORDER BY a.id DESC LIMIT 30""")
            admin_actions = rows(cur)
    return {
        "summary": summary,
        "pricing": pricing,
        "default_pricing": DEFAULT_CREDIT_PRICING,
        "reservations": reservations,
        "admin_actions": admin_actions,
    }


@app.get("/api/users")
def list_users(q: str = "", status: str = "all", limit: int = 100):
    limit = max(1, min(limit, 300))
    if status not in {"all", "active", "suspended", "deleted"}:
        raise HTTPException(400, "Bộ lọc trạng thái không hợp lệ")
    search = f"%{q.strip()}%"
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT u.id,u.email,u.display_name,u.balance,u.created_at,u.account_status,
                                  u.status_reason,u.status_changed_at,u.deleted_at,
                                  COUNT(s.id) FILTER (WHERE s.revoked_at IS NULL AND s.expires_at>NOW())::int AS active_sessions
                           FROM users u LEFT JOIN auth_sessions s ON s.user_id=u.id
                           WHERE (%s='' OR u.email ILIKE %s OR COALESCE(u.display_name,'') ILIKE %s OR u.id::text=%s)
                             AND (%s='all' OR u.account_status=%s)
                           GROUP BY u.id ORDER BY u.created_at DESC LIMIT %s""",
                        (q.strip(), search, search, q.strip(), status, status, limit))
            return rows(cur)


@app.get("/api/users/{user_id}")
def user_detail(user_id: int):
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT id,email,display_name,balance,created_at,settings,account_status,
                                  status_reason,status_changed_at,deleted_at
                           FROM users WHERE id=%s""", (user_id,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(404, "Không tìm thấy tài khoản")
            cur.execute("SELECT id,amount,balance_after,kind,reference,metadata,created_at FROM credit_ledger WHERE user_id=%s ORDER BY id DESC LIMIT 100", (user_id,))
            ledger = rows(cur)
            cur.execute("SELECT id,device_name,revoked_at,expires_at,created_at,last_used_at FROM auth_sessions WHERE user_id=%s ORDER BY created_at DESC LIMIT 50", (user_id,))
            sessions = rows(cur)
            cur.execute("SELECT id,action,reason,metadata,created_at FROM admin_audit_log WHERE user_id=%s ORDER BY id DESC LIMIT 50", (user_id,))
            audit = rows(cur)
    return {"user": dict(user), "ledger": ledger, "sessions": sessions, "audit": audit}


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
            cur.execute("INSERT INTO admin_audit_log(user_id,action,reason,metadata) VALUES(%s,'credit_adjusted',%s,%s)",
                        (user_id, req.reason.strip(), psycopg2.extras.Json({"amount": req.amount, "balance_after": new_balance})))
    return {"ok": True, "balance": new_balance, "ledger": entry}


@app.post("/api/users/{user_id}/revoke-sessions")
def revoke_sessions(user_id: int):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE auth_sessions SET revoked_at=NOW() WHERE user_id=%s AND revoked_at IS NULL", (user_id,))
            count = cur.rowcount
            cur.execute("INSERT INTO admin_audit_log(user_id,action,reason,metadata) VALUES(%s,'sessions_revoked','Đăng xuất mọi thiết bị',%s)",
                        (user_id, psycopg2.extras.Json({"revoked": count})))
    return {"ok": True, "revoked": count}


@app.put("/api/users/{user_id}/status")
def update_account_status(user_id: int, req: AccountStatusUpdate):
    target = req.status.strip().lower()
    if target not in {"active", "suspended", "deleted"}:
        raise HTTPException(400, "Trạng thái tài khoản không hợp lệ")
    reason = req.reason.strip()
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT account_status,balance FROM users WHERE id=%s FOR UPDATE", (user_id,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(404, "Không tìm thấy tài khoản")
            previous = user["account_status"] or "active"
            if previous == target:
                return {"ok": True, "status": target, "unchanged": True, "revoked": 0, "refunded": 0}

            revoked = refunded = refunded_credit = 0
            if target in {"suspended", "deleted"}:
                cur.execute("UPDATE auth_sessions SET revoked_at=NOW() WHERE user_id=%s AND revoked_at IS NULL", (user_id,))
                revoked = cur.rowcount
                cur.execute("SELECT id,cost FROM export_reservations WHERE user_id=%s AND status='reserved' FOR UPDATE", (user_id,))
                reservations = cur.fetchall()
                for reservation in reservations:
                    cur.execute("UPDATE users SET balance=balance+%s WHERE id=%s RETURNING balance", (reservation["cost"], user_id))
                    balance_after = cur.fetchone()["balance"]
                    cur.execute("UPDATE export_reservations SET status='admin_refunded',finished_at=NOW() WHERE id=%s", (reservation["id"],))
                    cur.execute("""INSERT INTO credit_ledger(user_id,amount,balance_after,kind,reference,idempotency_key,metadata)
                                   VALUES(%s,%s,%s,'admin_refund',%s,%s,%s)""",
                                (user_id, reservation["cost"], balance_after, str(reservation["id"]),
                                 f"admin-refund:{reservation['id']}", psycopg2.extras.Json({"reason": target})))
                    refunded += 1
                    refunded_credit += int(reservation["cost"])

            cur.execute("""UPDATE users SET account_status=%s,status_reason=%s,status_changed_at=NOW(),
                                      deleted_at=CASE WHEN %s='deleted' THEN NOW() ELSE NULL END
                           WHERE id=%s""", (target, reason, target, user_id))
            cur.execute("INSERT INTO admin_audit_log(user_id,action,reason,metadata) VALUES(%s,%s,%s,%s)",
                        (user_id, f"account_{target}", reason, psycopg2.extras.Json({
                            "previous_status": previous, "revoked_sessions": revoked,
                            "refunded_reservations": refunded, "refunded_credit": refunded_credit,
                        })))
    return {"ok": True, "status": target, "revoked": revoked, "refunded": refunded, "refunded_credit": refunded_credit}


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
            cur.execute("INSERT INTO admin_audit_log(action,reason,metadata) VALUES('pricing_updated','Cập nhật bảng giá',%s)",
                        (psycopg2.extras.Json({"prices": req.prices}),))
    return {"ok": True, "pricing": req.prices}


if __name__ == "__main__":
    import uvicorn

    url = f"http://127.0.0.1:{PORT}"
    print(f"Threads Admin: {url}")
    webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=PORT)
