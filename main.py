"""客服小美 — 公开客户聊天页 + AstrBot OpenAPI 代理。

客户浏览器访问 chat.okva.cc，页面调本服务 POST /api/chat，
本服务用服务端 ApiKey 代理到 AstrBot OpenAPI (SSE 流式透传)。
支持文本 / 图片 / 文件 / 语音附件。
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import db
import httpx
from admin_api import router as admin_router
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(admin_router)
db.init_db()

ASTRBOT_URL = os.environ.get("ASTRBOT_URL", "http://localhost:6185")
ASTRBOT_API_KEY = os.environ.get("ASTRBOT_API_KEY", "")
CHAT_USERNAME = os.environ.get("CHAT_USERNAME", "webchat")


def _visitor(headers) -> str:
    vid = (headers.get("x-visitor-id") or "").strip()
    if not vid:
        return f"anon-{hashlib.md5((headers.get('user-agent') or '').encode()).hexdigest()[:12]}"
    return vid[:64]

INDEX_HTML = Path(__file__).parent / "index.html"
ADMIN_HTML = Path(__file__).parent / "admin.html"

# 限制单条消息长度，防止滥用
MAX_MESSAGE_LEN = 4000


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"ApiKey {ASTRBOT_API_KEY}"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    host = request.headers.get("host", "")
    if "admin01" in host:
        return HTMLResponse(ADMIN_HTML.read_text(encoding="utf-8"))
    return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))


@app.get("/admin", response_class=HTMLResponse)
async def admin_page() -> HTMLResponse:
    return HTMLResponse(ADMIN_HTML.read_text(encoding="utf-8"))


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


async def _proxy_stream(payload: dict, visitor: str = ""):
    """转发到 AstrBot OpenAPI，SSE 逐块透传；捕获小美回复落库。"""
    headers = {**_auth_headers(), "Content-Type": "application/json"}
    sid = payload.get("session_id") or ""
    buf: list[str] = []
    saved = False
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        async with client.stream(
            "POST",
            f"{ASTRBOT_URL}/api/v1/chat",
            headers=headers,
            json=payload,
        ) as resp:
            if resp.status_code != 200:
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
                    sid = evt["session_id"]
                    db.ensure_session(sid, visitor)
                elif t == "plain" and isinstance(evt.get("data"), str):
                    buf.append(evt["data"])
                elif t == "end":
                    db.insert_msg(sid, visitor, "assistant", "text", "".join(buf))
                    saved = True
    # 流异常/无 end 帧兜底
    if buf and not saved:
        db.insert_msg(sid, visitor, "assistant", "text", "".join(buf))


@app.post("/api/upload")
async def api_upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    """客户上传图片/文件/语音 → AstrBot OpenAPI 附件，返回 attachment_id。"""
    data = await file.read()
    if not data:
        return JSONResponse({"error": "空文件"}, status_code=400)
    if len(data) > 20 * 1024 * 1024:
        return JSONResponse({"error": "文件太大（限 20MB）"}, status_code=400)

    headers = {**_auth_headers()}
    files = {"file": (file.filename or "upload.bin", data, file.content_type or "application/octet-stream")}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        resp = await client.post(f"{ASTRBOT_URL}/api/v1/file", headers=headers, files=files)
        try:
            body = resp.json()
        except Exception:
            body = {}
        if resp.status_code != 200 or not body.get("data"):
            return JSONResponse({"error": f"上传失败（{resp.status_code}）"}, status_code=502)
        info = body["data"]
        visitor_id = _visitor(request.headers)
        db.ensure_visitor(visitor_id)
        db.insert_msg("", visitor_id, "customer", info.get("type", "file"), file.filename or info.get("type", "file"))
        return JSONResponse({
            "attachment_id": info.get("attachment_id"),
            "type": info.get("type", "file"),
            "filename": file.filename,
        })


@app.post("/api/chat")
async def api_chat(request: Request) -> StreamingResponse:
    """客户消息 → AstrBot 对话。body: {message, session_id?, parts?}

    parts: [{type: "plain"|"image"|"file"|"record", text?|attachment_id?}]
    """
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
        return StreamingResponse(
            iter(["data: {\"type\": \"plain\", \"data\": \"请输入内容后再发送哦～\"}\n\n",
                  "data: {\"type\": \"end\", \"data\": \"\"}\n\n"]),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    total_len = sum(len(str(p.get("text", ""))) for p in parts)
    if total_len > MAX_MESSAGE_LEN:
        return StreamingResponse(
            iter([f"data: {json.dumps({'type': 'plain', 'data': '消息太长啦，请精简到 4000 字以内'}, ensure_ascii=False)}\n\n",
                  "data: {\"type\": \"end\", \"data\": \"\"}\n\n"]),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    session_id = str(body.get("session_id", "") or "").strip() or None
    visitor_id = _visitor(request.headers)
    db.ensure_visitor(visitor_id)
    if session_id:
        db.ensure_session(session_id, visitor_id)
    # 落客户消息
    for p in parts:
        ptype = p.get("type")
        if ptype == "plain":
            db.insert_msg(session_id, visitor_id, "customer", "text", p.get("text", ""))
        elif ptype == "image":
            db.insert_msg(session_id, visitor_id, "customer", "image", "图片")
        elif ptype in ("file", "record"):
            db.insert_msg(session_id, visitor_id, "customer", ptype, p.get("filename") or p.get("type") or ptype)
    payload = {
        "message": parts,
        "username": CHAT_USERNAME,
        "enable_streaming": True,
    }
    if session_id:
        payload["session_id"] = session_id

    return StreamingResponse(
        _proxy_stream(payload, visitor_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
