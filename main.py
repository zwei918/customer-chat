"""客服小美 — 公开客户聊天页 + AstrBot OpenAPI 代理。

客户浏览器访问 chat.okva.cc，页面调本服务 POST /api/chat，
本服务用服务端 ApiKey 代理到 AstrBot OpenAPI (SSE 流式透传)。
支持文本 / 图片 / 文件 / 语音附件。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

import db
import live
import routing
from admin_api import client_ip, router as admin_router
import httpx
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(admin_router)
db.init_db()

ASTRBOT_URL = os.environ.get("ASTRBOT_URL", "http://localhost:6185")
ASTRBOT_API_KEY = os.environ.get("ASTRBOT_API_KEY", "")
CHAT_USERNAME = os.environ.get("CHAT_USERNAME", "webchat")

MAX_MESSAGE_LEN = 4000
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
CHAT_LIMIT, CHAT_WINDOW = 30, 60
UPLOAD_LIMIT, UPLOAD_WINDOW = 20, 60
LEAVE_REPLY = "现在是留言模式，小美上线后会尽快回复。"

ALLOWED_UPLOAD = {
    ".jpg": {"image/jpeg", "image/jpg"},
    ".jpeg": {"image/jpeg", "image/jpg"},
    ".png": {"image/png"},
    ".gif": {"image/gif"},
    ".webp": {"image/webp"},
    ".pdf": {"application/pdf"},
    ".txt": {"text/plain"},
    ".doc": {"application/msword"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".xls": {"application/vnd.ms-excel"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".mp3": {"audio/mpeg", "audio/mp3"},
    ".wav": {"audio/wav", "audio/x-wav", "audio/wave"},
    ".m4a": {"audio/mp4", "audio/m4a", "audio/x-m4a"},
    ".webm": {"audio/webm", "video/webm"},
    ".ogg": {"audio/ogg", "application/ogg"},
    ".aac": {"audio/aac"},
}

_rate: dict[str, deque] = defaultdict(deque)


def _allow(key: str, limit: int, window: float) -> bool:
    now = time.time()
    q = _rate[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def _visitor(headers) -> str:
    vid = (headers.get("x-visitor-id") or "").strip()
    if not vid:
        return f"anon-{hashlib.md5((headers.get('user-agent') or '').encode()).hexdigest()[:12]}"
    return vid[:64]


INDEX_HTML = Path(__file__).parent / "index.html"
ADMIN_HTML = Path(__file__).parent / "admin.html"
DESK_HTML = Path(__file__).parent / "desk.html"


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"ApiKey {ASTRBOT_API_KEY}"}


_HTML_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse_plain(text: str):
    return (
        f"data: {json.dumps({'type': 'plain', 'data': text}, ensure_ascii=False)}\n\n",
        "data: {\"type\": \"end\", \"data\": \"\"}\n\n",
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    host = request.headers.get("host", "")
    html = ADMIN_HTML if "admin01" in host else INDEX_HTML
    return HTMLResponse(html.read_text(encoding="utf-8"), headers=_HTML_HEADERS)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page() -> HTMLResponse:
    return HTMLResponse(ADMIN_HTML.read_text(encoding="utf-8"), headers=_HTML_HEADERS)


@app.get("/desk", response_class=HTMLResponse)
async def desk_page() -> HTMLResponse:
    return HTMLResponse(DESK_HTML.read_text(encoding="utf-8"), headers=_HTML_HEADERS)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/api/widget")
async def api_widget(request: Request) -> dict:
    vid = (request.headers.get("x-visitor-id") or "").strip()
    if vid:
        db.ensure_visitor(_visitor(request.headers))
    return db.widget_config(vid or None)


@app.get("/api/widget/logo")
async def api_widget_logo():
    path = db.logo_file()
    if not path:
        return JSONResponse({"error": "无头像"}, status_code=404)
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif"}.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/visitor-avatar/{name}")
async def api_visitor_avatar(name: str):
    path = db.visitor_avatar_path(name)
    if not path:
        return JSONResponse({"error": "无头像"}, status_code=404)
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif"}.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, headers={"Cache-Control": "public, max-age=86400"})


def _guess_ext(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    suf = Path(name).suffix
    return suf if suf in ALLOWED_UPLOAD else ""


def _upload_ok(filename: str, content_type: str, data: bytes) -> tuple[bool, str]:
    ext = _guess_ext(filename, data)
    if not ext or ext not in ALLOWED_UPLOAD:
        return False, "不支持的文件类型"
    mime = (content_type or "").split(";")[0].strip().lower()
    allowed = ALLOWED_UPLOAD[ext]
    if mime and mime not in allowed and mime != "application/octet-stream":
        return False, "文件类型与扩展名不符"
    return True, ext


async def _read_upload(file: UploadFile) -> tuple[bytes | None, JSONResponse | None]:
    buf = bytearray()
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > MAX_UPLOAD_BYTES:
            return None, JSONResponse({"error": "文件太大（限 20MB）"}, status_code=400)
    if not buf:
        return None, JSONResponse({"error": "空文件"}, status_code=400)
    return bytes(buf), None


async def _proxy_stream(payload: dict, visitor: str, pending_ids: list[int]):
    """转发到 AstrBot OpenAPI，SSE 逐块透传；捕获小美回复落库。"""
    headers = {**_auth_headers(), "Content-Type": "application/json"}
    sid = payload.get("session_id") or ""
    buf: list[str] = []
    saved = False

    def _bind_session(new_sid: str) -> str:
        if not new_sid:
            return sid
        db.ensure_session(new_sid, visitor)
        db.attach_messages_session(pending_ids, new_sid, visitor)
        return new_sid

    def _fallback_session() -> str:
        nonlocal sid
        if sid:
            return sid
        sid = "local-" + uuid.uuid4().hex
        db.ensure_session(sid, visitor)
        db.attach_messages_session(pending_ids, sid, visitor)
        return sid

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            async with client.stream(
                "POST",
                f"{ASTRBOT_URL}/api/v1/chat",
                headers=headers,
                json=payload,
            ) as resp:
                if resp.status_code != 200:
                    sid = _fallback_session()
                    msg = f"服务异常（{resp.status_code}），请稍后再试"
                    yield f"data: {json.dumps({'type': 'plain', 'data': msg}, ensure_ascii=False)}\n\n"
                    yield "data: {\"type\": \"end\", \"data\": \"\"}\n\n"
                    db.insert_msg(sid, visitor, "assistant", "text", msg)
                    return
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    yield line + "\n"
                    try:
                        if not line.startswith("data: "):
                            continue
                        evt = json.loads(line[6:])
                    except Exception:
                        continue
                    t = evt.get("type")
                    if t == "session_id" and evt.get("session_id"):
                        sid = _bind_session(str(evt["session_id"]))
                    elif t == "plain" and isinstance(evt.get("data"), str):
                        buf.append(evt["data"])
                    elif t == "end":
                        sid = sid or _fallback_session()
                        db.insert_msg(sid, visitor, "assistant", "text", "".join(buf))
                        saved = True
    except Exception:
        sid = _fallback_session()
        msg = "网络不可用，请稍后重试"
        yield f"data: {json.dumps({'type': 'plain', 'data': msg}, ensure_ascii=False)}\n\n"
        yield "data: {\"type\": \"end\", \"data\": \"\"}\n\n"
        db.insert_msg(sid, visitor, "assistant", "text", msg)
        return
    if buf and not saved:
        sid = sid or _fallback_session()
        db.insert_msg(sid, visitor, "assistant", "text", "".join(buf))
    elif pending_ids and not sid:
        _fallback_session()


@app.post("/api/upload")
async def api_upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    """客户上传图片/文件/语音 → AstrBot OpenAPI 附件，只返回 attachment_id，不落库。"""
    visitor_id = _visitor(request.headers)
    ip = client_ip(request)
    if not _allow(f"up:{visitor_id}:{ip}", UPLOAD_LIMIT, UPLOAD_WINDOW):
        return JSONResponse({"error": "上传太频繁，请稍后再试"}, status_code=429)

    data, err = await _read_upload(file)
    if err:
        return err
    ok, info = _upload_ok(file.filename or "", file.content_type or "", data)
    if not ok:
        return JSONResponse({"error": info}, status_code=400)

    headers = {**_auth_headers()}
    files = {"file": (file.filename or f"upload{info}", data, file.content_type or "application/octet-stream")}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            resp = await client.post(f"{ASTRBOT_URL}/api/v1/file", headers=headers, files=files)
            try:
                body = resp.json()
            except Exception:
                body = {}
    except Exception:
        return JSONResponse({"error": "上传失败，请稍后重试"}, status_code=502)
    if resp.status_code != 200 or not body.get("data"):
        return JSONResponse({"error": f"上传失败（{resp.status_code}）"}, status_code=502)
    info_body = body["data"]
    return JSONResponse({
        "attachment_id": info_body.get("attachment_id"),
        "type": info_body.get("type", "file"),
        "filename": file.filename,
    })


def _insert_customer_parts(session_id: str | None, visitor_id: str, parts: list[dict]) -> list[int]:
    ids: list[int] = []
    for p in parts:
        ptype = p.get("type")
        if ptype == "plain":
            ids.append(db.insert_msg(session_id, visitor_id, "customer", "text", p.get("text", ""), sender_type="visitor"))
        elif ptype == "image":
            ids.append(db.insert_msg(session_id, visitor_id, "customer", "image", "图片", sender_type="visitor"))
        elif ptype in ("file", "record"):
            ids.append(db.insert_msg(session_id, visitor_id, "customer", ptype, p.get("filename") or p.get("type") or ptype, sender_type="visitor"))
    return ids


def _parts_text(parts: list[dict]) -> str:
    return "".join(str(p.get("text") or "") for p in parts if p.get("type") == "plain")


def _notice_stream(sid: str | None, text: str, emit_sid: bool = False):
    chunks: list[str] = []
    if emit_sid and sid:
        chunks.append(f"data: {json.dumps({'type': 'session_id', 'session_id': sid}, ensure_ascii=False)}\n\n")
    if text:
        chunks.append(f"data: {json.dumps({'type': 'plain', 'data': text}, ensure_ascii=False)}\n\n")
    else:
        chunks.append('data: {"type": "handoff", "data": ""}\n\n')
    chunks.append('data: {"type": "end", "data": ""}\n\n')
    return StreamingResponse(iter(chunks), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/api/thread")
async def api_thread(request: Request, session_id: str = "", after: int = 0) -> dict:
    visitor_id = _visitor(request.headers)
    sid = (session_id or "").strip()
    if not sid:
        return {"data": [], "status": ""}
    sess = db.get_session(sid)
    if not sess or (sess.get("visitor_id") and sess.get("visitor_id") != visitor_id):
        return {"data": [], "status": ""}
    return {"data": db.messages_since(sid, after), "status": sess.get("status") or "bot"}


@app.post("/api/csat")
async def api_csat(request: Request) -> JSONResponse:
    visitor_id = _visitor(request.headers)
    try:
        body = await request.json()
    except Exception:
        body = {}
    sid = str(body.get("session_id") or "").strip()
    try:
        score = int(body.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    if not sid or score < 1 or score > 5:
        return JSONResponse({"error": "请选择 1–5 分"}, status_code=400)
    sess = db.get_session(sid)
    if not sess or (sess.get("visitor_id") and sess.get("visitor_id") != visitor_id):
        return JSONResponse({"error": "会话无效"}, status_code=404)
    db.add_csat(sid, visitor_id, score, str(body.get("comment") or ""))
    return JSONResponse({"ok": True})


@app.post("/api/chat")
async def api_chat(request: Request) -> StreamingResponse:
    """客户消息 → 机器人或人工。body: {message, session_id?, parts?}"""
    visitor_id = _visitor(request.headers)
    ip = client_ip(request)
    if not _allow(f"chat:{visitor_id}:{ip}", CHAT_LIMIT, CHAT_WINDOW):
        return StreamingResponse(iter(_sse_plain("发送太频繁，请稍后再试")), media_type="text/event-stream", headers=_SSE_HEADERS)

    try:
        body = await request.json()
    except Exception:
        body = {}

    parts: list[dict] = []
    raw_parts = body.get("parts")
    if isinstance(raw_parts, list):
        for p in raw_parts:
            if isinstance(p, dict) and p.get("type") in ("plain", "image", "file", "record"):
                parts.append(p)

    message = str(body.get("message", "") or "").strip()
    if message:
        parts.append({"type": "plain", "text": message})
    if not parts:
        return StreamingResponse(iter(_sse_plain("请输入内容后再发送哦～")), media_type="text/event-stream", headers=_SSE_HEADERS)
    total_len = sum(len(str(p.get("text", ""))) for p in parts)
    if total_len > MAX_MESSAGE_LEN:
        return StreamingResponse(
            iter(_sse_plain("消息太长啦，请精简到 4000 字以内")),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    raw_sid = str(body.get("session_id", "") or "").strip() or None
    astr_sid = None if (raw_sid and raw_sid.startswith("local-")) else raw_sid
    sess = db.get_session(raw_sid) if raw_sid else None
    status = (sess or {}).get("status") or "bot"
    human = routing.human_status(status)
    going_offline = (not human) and (not db.is_locked_online()) and (not db.is_online())
    insert_sid = raw_sid if (going_offline or human) else astr_sid
    db.ensure_visitor(visitor_id)
    if insert_sid:
        db.ensure_session(insert_sid, visitor_id)

    pending_ids = _insert_customer_parts(insert_sid, visitor_id, parts)
    text = _parts_text(parts)

    if going_offline:
        leave_sid = insert_sid or ("local-" + uuid.uuid4().hex)
        db.ensure_session(leave_sid, visitor_id)
        db.attach_messages_session(pending_ids, leave_sid, visitor_id)
        db.insert_msg(leave_sid, visitor_id, "assistant", "text", LEAVE_REPLY, sender_type="system")
        return _notice_stream(leave_sid, LEAVE_REPLY, emit_sid=not insert_sid)

    if routing.wants_handoff(text) and not human:
        hid = raw_sid or ("local-" + uuid.uuid4().hex)
        db.ensure_session(hid, visitor_id)
        db.attach_messages_session(pending_ids, hid, visitor_id)
        result = routing.enqueue_or_assign(hid, visitor_id)
        msg = routing.handoff_notice(result)
        db.insert_msg(hid, visitor_id, "assistant", "text", msg, sender_type="system")
        live.publish(hid, {"type": "handoff", "status": result})
        live.publish("desk", {"type": "inbox"})
        return _notice_stream(hid, msg, emit_sid=not raw_sid)

    if human:
        live.publish("desk", {"type": "inbox", "session_id": raw_sid})
        if raw_sid:
            live.publish(raw_sid, {"type": "visitor"})
        notice = "正在排队等待坐席，请稍候。" if status == "queued" else ""
        return _notice_stream(raw_sid, notice)

    faq = db.match_guide_answer(text)
    if faq:
        faq_sid = insert_sid or astr_sid or raw_sid or ("local-" + uuid.uuid4().hex)
        db.ensure_session(faq_sid, visitor_id)
        db.attach_messages_session(pending_ids, faq_sid, visitor_id)
        db.insert_msg(faq_sid, visitor_id, "assistant", "text", faq, sender_type="bot")
        return _notice_stream(faq_sid, faq, emit_sid=not raw_sid)

    payload = {
        "message": parts,
        "username": CHAT_USERNAME,
        "enable_streaming": True,
    }
    if astr_sid:
        payload["session_id"] = astr_sid

    return StreamingResponse(
        _proxy_stream(payload, visitor_id, pending_ids),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "assets"), name="assets")
