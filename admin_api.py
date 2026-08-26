"""admin_api.py — 后台登录 + 查询 API（会话 cookie + 速率限制）"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections import Counter, defaultdict, deque

import db
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel

router = APIRouter(prefix="/admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SECRET = os.environ.get("ADMIN_SECRET", "changeme-secret")
COOKIE_NAME = "admin_session"

# 登录速率限制：每 IP 15 分钟最多 10 次
LOGIN_LIMIT = 10
LOGIN_WINDOW = 900
_login_hist: dict[str, deque] = defaultdict(deque)


def _cookie_val() -> str:
    return hmac.new(SECRET.encode(), ADMIN_PASSWORD.encode(), hashlib.sha256).hexdigest()


def require_login(admin_session: str = Cookie(default="")) -> bool:
    if hmac.compare_digest(admin_session or "", _cookie_val()):
        return True
    raise HTTPException(status_code=401, detail="未登录")


class LoginReq(BaseModel):
    password: str


@router.post("/login")
async def login(req: LoginReq, response: Response, request: Request):
    ip = request.client.host if request.client else "?"
    now = time.time()
    q = _login_hist[ip]
    while q and now - q[0] > LOGIN_WINDOW:
        q.popleft()
    if len(q) >= LOGIN_LIMIT:
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
    if not ADMIN_PASSWORD or req.password != ADMIN_PASSWORD:
        q.append(now)
        raise HTTPException(status_code=401, detail="密码错误")
    q.append(now)
    response.set_cookie(COOKIE_NAME, _cookie_val(), httponly=True, secure=True, samesite="lax", max_age=86400)
    return {"ok": True}


@router.get("/api/visitors", dependencies=[Depends(require_login)])
async def api_visitors(page: int = 1, limit: int = 50):
    offset = (page - 1) * limit
    return {"data": db.list_visitors(limit, offset)}


@router.get("/api/sessions", dependencies=[Depends(require_login)])
async def api_sessions(visitor_id: str = "", page: int = 1, limit: int = 50):
    offset = (page - 1) * limit
    return {"data": db.list_sessions(visitor_id or None, limit, offset)}


@router.get("/api/messages", dependencies=[Depends(require_login)])
async def api_messages(session_id: str = "", visitor_id: str = "", keyword: str = "",
                       page: int = 1, limit: int = 50):
    offset = (page - 1) * limit
    return {"data": db.query_messages(session_id or None, visitor_id or None, keyword or None, limit, offset)}


@router.get("/api/stats", dependencies=[Depends(require_login)])
async def api_stats():
    s = db.stats()
    # top keywords: 简单分词聚合（仅客户文本消息）
    cnt: Counter = Counter()
    rows = db.query_messages(limit=5000)
    for r in rows:
        if r["direction"] == "customer" and r["msg_type"] == "text":
            for w in str(r["content"]).split():
                if len(w) >= 2:
                    cnt[w] += 1
    s["top_keywords"] = cnt.most_common(20)
    return s


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}
