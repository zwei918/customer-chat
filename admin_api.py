"""admin_api.py — 运营中台 + 坐席工作台 API（可吊销多人 session）"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import defaultdict, deque

import db
import live
import routing
from fastapi import APIRouter, Cookie, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SECRET = os.environ.get("ADMIN_SECRET", "changeme-secret")
COOKIE_NAME = "admin_session"
DEFAULT_SECRET = "changeme-secret"

LOGIN_LIMIT = 10
LOGIN_WINDOW = 900
_login_hist: dict[str, deque] = defaultdict(deque)


def admin_configured() -> bool:
    return bool(ADMIN_PASSWORD) and bool(SECRET) and SECRET != DEFAULT_SECRET


def _hash_token(raw: str) -> str:
    return hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()


def client_ip(request: Request) -> str:
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if xff:
        return xff[:64]
    return ((request.client.host if request.client else "?") or "?")[:64]


def _cookie_secure(request: Request) -> bool:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").split(",")[0].strip().lower()
    return proto == "https"


class Actor:
    __slots__ = ("role", "staff_id", "name", "login_name")

    def __init__(self, role: str, staff_id: int | None = None, name: str = "", login_name: str = ""):
        self.role = role
        self.staff_id = staff_id
        self.name = name
        self.login_name = login_name

    def as_dict(self) -> dict:
        return {
            "role": self.role,
            "staff_id": self.staff_id,
            "name": self.name,
            "login_name": self.login_name,
            "is_ops": self.role in ("superadmin", "admin"),
        }


def current_actor(admin_session: str = Cookie(default="")) -> Actor:
    if not admin_configured():
        raise HTTPException(status_code=503, detail="后台未配置 ADMIN_PASSWORD / ADMIN_SECRET")
    raw = admin_session or ""
    if not raw:
        raise HTTPException(status_code=401, detail="未登录")
    token_hash = _hash_token(raw)
    row = db.get_admin_session(token_hash)
    if row:
        sid = row.get("staff_id")
        if sid:
            staff = db.get_staff(int(sid))
            if not staff or int(staff.get("enabled") or 0) != 1:
                raise HTTPException(status_code=401, detail="未登录")
            role = staff.get("role") or "agent"
            if role == "owner":
                role = "admin"
            if role not in ("admin", "agent"):
                role = "agent"
            return Actor(role, int(sid), staff.get("name") or "", staff.get("login_name") or "")
        return Actor("superadmin", None, "超管", "admin")
    stored = db.get_setting("admin_session_hash", "")
    if stored and hmac.compare_digest(stored, token_hash):
        return Actor("superadmin", None, "超管", "admin")
    raise HTTPException(status_code=401, detail="未登录")


def require_login(actor: Actor = Depends(current_actor)) -> Actor:
    return actor


def require_ops(actor: Actor = Depends(current_actor)) -> Actor:
    if actor.role not in ("superadmin", "admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return actor


class LoginReq(BaseModel):
    password: str
    username: str = ""


def _issue_cookie(response: Response, request: Request, staff_id: int | None) -> None:
    raw = secrets.token_urlsafe(32)
    db.create_admin_session(_hash_token(raw), staff_id)
    response.set_cookie(
        COOKIE_NAME,
        raw,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
        max_age=86400,
        path="/",
    )


@router.post("/login")
async def login(req: LoginReq, response: Response, request: Request):
    if not admin_configured():
        raise HTTPException(status_code=503, detail="后台未配置 ADMIN_PASSWORD / ADMIN_SECRET")
    ip = client_ip(request)
    now = time.time()
    q = _login_hist[ip]
    while q and now - q[0] > LOGIN_WINDOW:
        q.popleft()
    if len(q) >= LOGIN_LIMIT:
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
    username = (req.username or "").strip()
    staff = db.find_staff_login(username) if username else None
    if staff:
        if int(staff.get("enabled") or 0) != 1 or not db.verify_password(req.password, staff.get("password_hash") or ""):
            q.append(now)
            raise HTTPException(status_code=401, detail="账号或密码错误")
        _issue_cookie(response, request, int(staff["id"]))
        db.update_staff(int(staff["id"]), presence="online")
        return {"ok": True, "role": "admin" if staff.get("role") in ("admin", "owner") else "agent", "staff_id": int(staff["id"])}
    if username and username not in ("admin", "超管"):
        q.append(now)
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if req.password != ADMIN_PASSWORD:
        q.append(now)
        raise HTTPException(status_code=401, detail="密码错误")
    _issue_cookie(response, request, None)
    return {"ok": True, "role": "superadmin", "staff_id": None}


@router.get("/api/me", dependencies=[Depends(require_login)])
async def api_me(actor: Actor = Depends(current_actor)):
    return actor.as_dict()


class PresenceReq(BaseModel):
    presence: str


@router.post("/api/presence")
async def api_presence(req: PresenceReq, actor: Actor = Depends(current_actor)):
    if req.presence not in ("online", "offline", "away"):
        raise HTTPException(status_code=400, detail="presence 无效")
    if not actor.staff_id:
        return {"ok": True, "presence": req.presence}
    db.update_staff(actor.staff_id, presence=req.presence)
    return {"ok": True, "presence": req.presence}


def _page(page: int, limit: int) -> tuple[int, int, int]:
    page = max(int(page or 1), 1)
    limit = min(max(int(limit or 50), 1), 100)
    return page, limit, (page - 1) * limit


@router.get("/api/visitors", dependencies=[Depends(require_login)])
async def api_visitors(page: int = 1, limit: int = 50):
    page, limit, offset = _page(page, limit)
    return {"data": db.list_visitors(limit, offset), "total": db.count_visitors(), "page": page, "limit": limit}


@router.get("/api/sessions", dependencies=[Depends(require_login)])
async def api_sessions(visitor_id: str = "", page: int = 1, limit: int = 50):
    page, limit, offset = _page(page, limit)
    return {"data": db.list_sessions(visitor_id or None, limit, offset), "page": page, "limit": limit}


@router.get("/api/messages", dependencies=[Depends(require_login)])
async def api_messages(session_id: str = "", visitor_id: str = "", keyword: str = "",
                       page: int = 1, limit: int = 50):
    page, limit, offset = _page(page, limit)
    sid, vid, kw = session_id or None, visitor_id or None, keyword or None
    return {
        "data": db.query_messages(sid, vid, kw, limit, offset),
        "total": db.count_messages(sid, vid, kw),
        "page": page,
        "limit": limit,
    }


@router.get("/api/stats", dependencies=[Depends(require_login)])
async def api_stats():
    return db.stats()


@router.post("/api/inbox/read", dependencies=[Depends(require_login)])
async def api_inbox_read():
    return {"ok": True, "last_read_msg_id": db.mark_inbox_read()}


class SettingsReq(BaseModel):
    name: str | None = None
    online: bool | None = None
    online_lock: bool | None = None
    chat_bg: str | None = None


_LOGO_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _logo_ext(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise HTTPException(status_code=400, detail="请上传 JPG / PNG / WebP / GIF")


def _save_named_logo(folder, data: bytes) -> None:
    if len(data) > 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片不能超过 1MB")
    if len(data) < 32:
        raise HTTPException(status_code=400, detail="文件无效")
    ext = _logo_ext(data)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("logo.*"):
        old.unlink(missing_ok=True)
    (folder / f"logo{ext}").write_bytes(data)


class OpsReq(BaseModel):
    name: str | None = None


@router.get("/api/ops", dependencies=[Depends(require_ops)])
async def api_get_ops():
    return db.ops_config()


@router.post("/api/ops", dependencies=[Depends(require_ops)])
async def api_save_ops(req: OpsReq):
    if req.name is not None:
        name = (req.name or "").strip()[:32]
        if not name:
            raise HTTPException(status_code=400, detail="名称不能为空")
        db.set_setting("admin_name", name)
    return db.ops_config()


@router.get("/api/ops-logo", dependencies=[Depends(require_login)])
async def api_get_ops_logo():
    path = db.ops_logo_file()
    if not path:
        raise HTTPException(status_code=404, detail="无头像")
    media = _LOGO_MEDIA.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, headers={"Cache-Control": "private, max-age=3600"})


@router.post("/api/ops-logo", dependencies=[Depends(require_ops)])
async def api_upload_ops_logo(file: UploadFile = File(...)):
    _save_named_logo(db.OPS_DIR, await file.read())
    return db.ops_config()


@router.delete("/api/ops-logo", dependencies=[Depends(require_ops)])
async def api_delete_ops_logo():
    for old in db.OPS_DIR.glob("logo.*"):
        old.unlink(missing_ok=True)
    return db.ops_config()


@router.get("/api/settings", dependencies=[Depends(require_ops)])
async def api_get_settings():
    return db.widget_config()


@router.post("/api/settings", dependencies=[Depends(require_ops)])
async def api_save_settings(req: SettingsReq):
    pending: dict = {}
    admin_name = None
    if req.name is not None:
        admin_name = (req.name or "").strip()[:32]
        if not admin_name:
            raise HTTPException(status_code=400, detail="名称不能为空")
    if req.chat_bg is not None:
        color = db.normalize_hex(req.chat_bg, default="")
        if not color:
            raise HTTPException(status_code=400, detail="请填写 #RRGGBB 颜色")
        pending["chat_bg"] = color
    if req.online_lock is not None:
        pending["online_lock"] = req.online_lock
    if req.online is not None:
        will_lock = pending["online_lock"] if "online_lock" in pending else db.default_brand().get("online_lock")
        if not will_lock:
            pending["online"] = req.online
    if admin_name is not None:
        db.set_setting("admin_name", admin_name)
    if pending:
        db.patch_default_brand(pending)
    return db.widget_config()


@router.post("/api/logo", dependencies=[Depends(require_ops)])
async def api_upload_logo(file: UploadFile = File(...)):
    _save_named_logo(db.BRAND_DIR, await file.read())
    return db.widget_config()


@router.delete("/api/logo", dependencies=[Depends(require_ops)])
async def api_delete_logo():
    for old in db.BRAND_DIR.glob("logo.*"):
        old.unlink(missing_ok=True)
    return db.widget_config()


@router.get("/api/visitor-avatars", dependencies=[Depends(require_ops)])
async def api_list_visitor_avatars():
    return {"data": db.list_visitor_avatars(), "max": db.MAX_VISITOR_AVATARS}


@router.post("/api/visitor-avatars", dependencies=[Depends(require_ops)])
async def api_upload_visitor_avatars(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="请选择图片")
    for f in files:
        data = await f.read()
        if len(data) > 1024 * 1024:
            raise HTTPException(status_code=400, detail="每张图片不能超过 1MB")
        if len(data) < 32:
            raise HTTPException(status_code=400, detail="文件无效")
        try:
            db.add_visitor_avatar(data, _logo_ext(data))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    return {"data": db.list_visitor_avatars(), "max": db.MAX_VISITOR_AVATARS}


@router.delete("/api/visitor-avatars/{name}", dependencies=[Depends(require_ops)])
async def api_delete_visitor_avatar(name: str):
    db.delete_visitor_avatar(name)
    return {"data": db.list_visitor_avatars(), "max": db.MAX_VISITOR_AVATARS}


@router.post("/logout")
async def logout(response: Response, admin_session: str = Cookie(default="")):
    raw = admin_session or ""
    if raw:
        db.delete_admin_session(_hash_token(raw))
    db.set_setting("admin_session_hash", "")
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


class StaffReq(BaseModel):
    login_name: str
    name: str
    password: str
    role: str = "agent"
    skill_group_id: int | None = None
    max_chats: int = 8


class StaffPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    skill_group_id: int | None = None
    max_chats: int | None = None
    enabled: bool | None = None
    presence: str | None = None
    password: str | None = None


@router.get("/api/staff", dependencies=[Depends(require_ops)])
async def api_list_staff():
    return {"data": [db.public_staff(s) for s in db.list_staff()]}


@router.post("/api/staff", dependencies=[Depends(require_ops)])
async def api_create_staff(req: StaffReq):
    login_name = (req.login_name or "").strip()[:32]
    name = (req.name or "").strip()[:32]
    if not login_name or not name or len(req.password or "") < 4:
        raise HTTPException(status_code=400, detail="登录名、显示名必填，密码至少 4 位")
    if req.role not in ("admin", "agent"):
        raise HTTPException(status_code=400, detail="角色只能是 admin 或 agent")
    if db.get_staff_by_login(login_name):
        raise HTTPException(status_code=400, detail="登录名已存在")
    row = db.create_staff(login_name, name, req.password, req.role, req.skill_group_id, max(1, min(int(req.max_chats), 50)))
    return db.public_staff(row)


@router.patch("/api/staff/{staff_id}", dependencies=[Depends(require_ops)])
async def api_patch_staff(staff_id: int, req: StaffPatch):
    if not db.get_staff(staff_id):
        raise HTTPException(status_code=404, detail="坐席不存在")
    fields: dict = {}
    if req.name is not None:
        fields["name"] = req.name.strip()[:32]
    if req.role is not None:
        if req.role not in ("admin", "agent"):
            raise HTTPException(status_code=400, detail="角色无效")
        fields["role"] = req.role
    if req.skill_group_id is not None:
        fields["skill_group_id"] = req.skill_group_id
    if req.max_chats is not None:
        fields["max_chats"] = max(1, min(int(req.max_chats), 50))
    if req.enabled is not None:
        fields["enabled"] = 1 if req.enabled else 0
        if not req.enabled:
            fields["presence"] = "offline"
    if req.presence is not None:
        if req.presence not in ("online", "offline", "away"):
            raise HTTPException(status_code=400, detail="状态无效")
        fields["presence"] = req.presence
    if req.password:
        fields["password_hash"] = db.hash_password(req.password)
    db.update_staff(staff_id, **fields)
    return db.public_staff(db.get_staff(staff_id))


@router.delete("/api/staff/{staff_id}", dependencies=[Depends(require_ops)])
async def api_delete_staff(staff_id: int):
    db.delete_staff(staff_id)
    return {"ok": True}


class GroupReq(BaseModel):
    name: str


@router.get("/api/skill-groups", dependencies=[Depends(require_ops)])
async def api_groups():
    return {"data": db.list_skill_groups()}


@router.post("/api/skill-groups", dependencies=[Depends(require_ops)])
async def api_create_group(req: GroupReq):
    name = (req.name or "").strip()[:32]
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    return db.create_skill_group(name)


@router.delete("/api/skill-groups/{gid}", dependencies=[Depends(require_ops)])
async def api_delete_group(gid: int):
    db.delete_skill_group(gid)
    return {"ok": True}


class BrandReq(BaseModel):
    name: str | None = None
    welcome: str | None = None
    guide_qs: list | None = None
    allow_image: bool | None = None
    allow_file: bool | None = None
    allow_voice: bool | None = None
    chat_bg: str | None = None
    online: bool | None = None
    online_lock: bool | None = None
    handoff_keywords: str | None = None
    is_default: bool | None = None


@router.get("/api/brands", dependencies=[Depends(require_ops)])
async def api_brands():
    return {"data": db.list_brands()}


@router.put("/api/brands/{brand_id}", dependencies=[Depends(require_ops)])
async def api_save_brand(brand_id: int, req: BrandReq):
    data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    data = {k: v for k, v in data.items() if v is not None}
    return db.save_brand(brand_id, data)


class RoutingReq(BaseModel):
    routing_mode: str | None = None
    sticky_enabled: bool | None = None
    business_hours: dict | None = None


@router.get("/api/routing", dependencies=[Depends(require_ops)])
async def api_get_routing():
    try:
        hours = json.loads(db.get_setting("business_hours") or "{}")
    except Exception:
        hours = {}
    return {
        "routing_mode": db.get_setting("routing_mode", "least_busy"),
        "sticky_enabled": db.get_setting("sticky_enabled", "1") == "1",
        "business_hours": hours,
    }


@router.post("/api/routing", dependencies=[Depends(require_ops)])
async def api_save_routing(req: RoutingReq):
    if req.routing_mode:
        if req.routing_mode not in ("least_busy", "round_robin"):
            raise HTTPException(status_code=400, detail="分配方式无效")
        db.set_setting("routing_mode", req.routing_mode)
    if req.sticky_enabled is not None:
        db.set_setting("sticky_enabled", "1" if req.sticky_enabled else "0")
    if req.business_hours is not None:
        db.set_setting("business_hours", json.dumps(req.business_hours, ensure_ascii=False))
    return await api_get_routing()


class CannedReq(BaseModel):
    title: str
    content: str
    group_name: str = ""


@router.get("/api/canned", dependencies=[Depends(require_login)])
async def api_list_canned():
    return {"data": db.list_canned()}


@router.post("/api/canned", dependencies=[Depends(require_ops)])
async def api_add_canned(req: CannedReq):
    title = (req.title or "").strip()[:40]
    content = (req.content or "").strip()[:2000]
    if not title or not content:
        raise HTTPException(status_code=400, detail="问题和答案都要填")
    return db.add_canned(title, content, (req.group_name or "")[:20])


@router.delete("/api/canned/{cid}", dependencies=[Depends(require_ops)])
async def api_del_canned(cid: int):
    db.delete_canned(cid)
    return {"ok": True}


@router.get("/api/csat", dependencies=[Depends(require_ops)])
async def api_csat():
    return db.csat_stats()


@router.get("/api/reports/agents", dependencies=[Depends(require_ops)])
async def api_agent_report():
    return {"data": db.agent_report()}


@router.get("/api/agents")
async def api_agents(actor: Actor = Depends(current_actor)):
    rows = [db.public_staff(s) for s in db.list_staff() if int((s or {}).get("enabled") or 0) == 1]
    return {"data": rows}


@router.get("/api/inbox")
async def api_inbox(queue: str = "queued", actor: Actor = Depends(current_actor)):
    q = queue if queue in ("queued", "mine", "bot", "all", "resolved") else "queued"
    sid = actor.staff_id if q == "mine" else None
    if q == "mine" and not sid:
        return {"data": []}
    return {"data": db.desk_conversations(q, sid)}


@router.get("/api/inbox/{session_id}/messages")
async def api_inbox_messages(session_id: str, actor: Actor = Depends(current_actor)):
    sess = db.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session": sess, "data": list(reversed(db.query_messages(session_id, None, None, 200, 0)))}


class TransferReq(BaseModel):
    staff_id: int


class ReplyReq(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _can_handle(sess: dict, actor: Actor) -> bool:
    if actor.role in ("superadmin", "admin"):
        return True
    return int(sess.get("assignee_id") or 0) == (actor.staff_id or -1)


@router.post("/api/inbox/{session_id}/claim")
async def api_claim(session_id: str, actor: Actor = Depends(current_actor)):
    if not actor.staff_id:
        raise HTTPException(status_code=400, detail="超管请先创建坐席账号再认领")
    try:
        status = routing.claim(session_id, actor.staff_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    live.publish(session_id, {"type": "claimed", "staff_id": actor.staff_id})
    live.publish("desk", {"type": "inbox"})
    return {"ok": True, "status": status}


@router.post("/api/inbox/{session_id}/transfer")
async def api_transfer(session_id: str, req: TransferReq, actor: Actor = Depends(current_actor)):
    sess = db.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not _can_handle(sess, actor):
        raise HTTPException(status_code=403, detail="只能转接自己的会话")
    try:
        status = routing.transfer(session_id, req.staff_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    live.publish(session_id, {"type": "transferred", "staff_id": req.staff_id})
    live.publish("desk", {"type": "inbox"})
    return {"ok": True, "status": status}


@router.post("/api/inbox/{session_id}/resolve")
async def api_resolve(session_id: str, actor: Actor = Depends(current_actor)):
    sess = db.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not _can_handle(sess, actor):
        raise HTTPException(status_code=403, detail="只能结束自己的会话")
    try:
        status = routing.resolve(session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    note = "会话已结束，欢迎为本次服务打分。"
    db.insert_msg(session_id, sess.get("visitor_id"), "assistant", "text", note, sender_type="system")
    live.publish(session_id, {"type": "resolved", "text": note})
    live.publish("desk", {"type": "inbox"})
    return {"ok": True, "status": status}


@router.post("/api/inbox/{session_id}/reply")
async def api_reply(session_id: str, req: ReplyReq, actor: Actor = Depends(current_actor)):
    sess = db.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    if sess.get("status") != "assigned":
        raise HTTPException(status_code=400, detail="请先认领会话")
    if not _can_handle(sess, actor):
        raise HTTPException(status_code=403, detail="只能回复自己的会话")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="内容不能为空")
    mid = db.insert_msg(
        session_id, sess.get("visitor_id"), "assistant", "text", text,
        sender_type="agent", staff_id=actor.staff_id,
    )
    live.publish(session_id, {"type": "agent", "text": text, "id": mid})
    live.publish("desk", {"type": "inbox"})
    return {"ok": True, "id": mid}


@router.get("/api/live")
async def api_live(actor: Actor = Depends(current_actor)):
    q = live.subscribe("desk")

    async def gen():
        try:
            yield "data: {\"type\":\"hello\"}\n\n"
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield "data: {\"type\":\"ping\"}\n\n"
                    continue
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        finally:
            live.unsubscribe("desk", q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
