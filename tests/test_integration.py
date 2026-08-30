"""tests/test_integration.py — 客服平台集成测试（临时 sqlite，不写仓库 data.db）"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["XIAOMEI_DB"] = _tmp.name
_tmp.close()
os.environ["XIAOMEI_UPLOADS"] = tempfile.mkdtemp()
os.environ["ADMIN_PASSWORD"] = "testpass"
os.environ["ADMIN_SECRET"] = "testsecret"
os.environ["ASTRBOT_API_KEY"] = "abk_test"
os.environ["ASTRBOT_URL"] = "http://127.0.0.1:6185"

import admin_api  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(main.app)


class _Stream:
    status_code = 200

    async def aiter_lines(self):
        yield 'data: {"type": "session_id", "session_id": "sess-test"}'
        yield 'data: {"type": "plain", "data": "你好呀"}'
        yield 'data: {"type": "end", "data": ""}'

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _UploadResp:
    status_code = 200

    def json(self):
        return {"data": {"attachment_id": "att-1", "type": "image"}}


class _FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, *a, **k):
        return _Stream()

    async def post(self, *a, **k):
        return _UploadResp()


def check(name, cond, msg=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name} {msg}")
    assert cond, name


r = c.get("/admin")
check("admin 页面 200", r.status_code == 200 and "客服小美" in r.text, f"(status={r.status_code})")

r = c.get("/admin/api/visitors")
check("未登录 visitors 401", r.status_code == 401, f"(status={r.status_code})")
r = c.get("/admin/api/stats")
check("未登录 stats 401", r.status_code == 401, f"(status={r.status_code})")

r = c.post("/admin/login", json={"password": "wrong"})
check("错误密码 401", r.status_code == 401, f"(status={r.status_code})")

r = c.post("/admin/login", json={"password": "testpass"})
check("登录 200", r.status_code == 200, f"(status={r.status_code})")
cookie = r.cookies.get("admin_session") or c.cookies.get("admin_session")
check("拿到 cookie", bool(cookie))
auth_hdr = {"Cookie": f"admin_session={cookie}"}

r = c.get("/admin/api/visitors", headers=auth_hdr)
check("鉴权 visitors 200", r.status_code == 200 and isinstance(r.json()["data"], list), f"(status={r.status_code})")
r = c.get("/admin/api/stats", headers=auth_hdr)
check("鉴权 stats 200", r.status_code == 200, f"(status={r.status_code})")
st = r.json()
check("stats 含日趋", isinstance(st.get("daily"), list) and len(st["daily"]) == 14, f"(daily={st.get('daily')})")
check("stats 含客户消息数", "customer_msgs" in st and "today_customer" in st)
check("stats 含未读", "unread_customer" in st)
check("daily 含 sessions", "sessions" in (st["daily"][0] if st["daily"] else {}))

r = c.get("/api/widget")
check("公开 widget 200", r.status_code == 200 and r.json().get("name") == "客服小美", f"(status={r.status_code} body={r.text[:80]})")
r = TestClient(main.app).get("/admin/api/settings")
check("未登录 settings 401", r.status_code == 401, f"(status={r.status_code})")

r = c.post("/admin/api/settings", json={"name": "测试客服", "online": False, "online_lock": False}, headers=auth_hdr)
check("保存外观 200", r.status_code == 200 and r.json().get("online") is False, f"(status={r.status_code})")
r = c.get("/api/widget")
check("外观改名不影响前台", r.json().get("name") == "客服小美" and r.json().get("online") is False)
check("后台名已写入", db.get_setting("admin_name") == "测试客服")
r = c.get("/admin/api/ops", headers=auth_hdr)
check("ops 名称", r.status_code == 200 and r.json().get("name") == "测试客服")

r = c.post("/admin/api/ops", json={"name": "运营测试"}, headers=auth_hdr)
check("ops 改名", r.status_code == 200 and r.json().get("name") == "运营测试")
check("ops 改名不影响前台", c.get("/api/widget").json().get("name") == "客服小美")

brands = c.get("/admin/api/brands", headers=auth_hdr).json().get("data") or []
check("有默认品牌", bool(brands))
bid = brands[0]["id"]
r = c.put(f"/admin/api/brands/{bid}", json={"name": "前台客服"}, headers=auth_hdr)
check("前台改名", r.status_code == 200 and r.json().get("name") == "前台客服", f"(body={r.text[:120]})")
check("widget 前台名", c.get("/api/widget").json().get("name") == "前台客服")
c.put(f"/admin/api/brands/{bid}", json={"name": "客服小美"}, headers=auth_hdr)

png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
r = c.post("/admin/api/visitor-avatars", files=[("files", ("a.png", png, "image/png")), ("files", ("b.png", png, "image/png"))], headers=auth_hdr)
check("上传客户头像", r.status_code == 200 and len(r.json().get("data") or []) >= 2, f"(status={r.status_code} body={r.text[:160]})")
w = c.get("/api/widget", headers={"X-Visitor-Id": "avatar-user-1"}).json()
check("访客分到头像", bool(w.get("visitor_avatar_url")), f"(widget={w})")
r = c.get(w["visitor_avatar_url"].split("?")[0])
check("客户头像可访问", r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n", f"(status={r.status_code})")
w2 = c.get("/api/widget", headers={"X-Visitor-Id": "avatar-user-1"}).json()
check("同一访客头像不变", w2.get("visitor_avatar_url") == w.get("visitor_avatar_url"))

r = c.post("/admin/api/settings", json={"name": "客服小美", "online": False, "online_lock": True}, headers=auth_hdr)
check("锁定后仍在线", r.status_code == 200 and r.json().get("online") is True and r.json().get("online_lock") is True, f"(body={r.text[:120]})")
check("锁定不改写 stored online", db.get_setting("online") == "0")
r = c.get("/api/widget")
check("widget 锁定在线", r.json().get("online") is True)

r = c.post("/admin/api/settings", json={"name": "不该保存", "chat_bg": "red"}, headers=auth_hdr)
check("非法颜色 400", r.status_code == 400, f"(status={r.status_code})")
check("非法颜色不部分提交", db.get_setting("name") == "客服小美")

r = c.post("/admin/api/settings", json={"name": "客服小美", "online": True, "online_lock": True, "chat_bg": "#f2f2f7"}, headers=auth_hdr)
check("外观还原", r.status_code == 200)

r = c.get("/api/widget", headers={"X-Visitor-Id": "opener-1"})
check("打开聊天页登记访客", any(v["visitor_id"] == "opener-1" for v in db.list_visitors()))

before = db.stats()["messages"]
with patch("main.httpx.AsyncClient", _FakeClient):
    r = c.post("/api/chat", json={"message": "你好小美"}, headers={"X-Visitor-Id": "tester-1"})
check("chat 200", r.status_code == 200, f"(status={r.status_code})")
cust = [m for m in db.query_messages(keyword="你好小美") if m["direction"] == "customer"]
check("客户消息已落库", len(cust) >= 1, f"(customer count={len(cust)})")
check("session 已回填", bool(cust) and cust[0]["session_id"] == "sess-test", f"(sid={cust[0]['session_id'] if cust else None})")
ask = [m for m in db.query_messages() if m["direction"] == "assistant" and "你好呀" in m["content"]]
check("assistant 已落库", len(ask) >= 1, f"(assistant count={len(ask)})")
check("今日消息 LIKE 前缀", db.stats()["today_msgs"] >= 1, f"(today_msgs={db.stats()['today_msgs']})")
check("一次 chat 双向落库", db.stats()["messages"] == before + 2, f"({before} -> {db.stats()['messages']})")

before_up = db.stats()["messages"]
png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
with patch("main.httpx.AsyncClient", _FakeClient):
    r = c.post(
        "/api/upload",
        files={"file": ("a.png", png, "image/png")},
        headers={"X-Visitor-Id": "tester-1"},
    )
check("upload 200", r.status_code == 200 and r.json().get("attachment_id") == "att-1", f"(body={r.text[:120]})")
check("upload 不落库", db.stats()["messages"] == before_up, f"({before_up} -> {db.stats()['messages']})")
with patch("main.httpx.AsyncClient", _FakeClient):
    r = c.post(
        "/api/chat",
        json={"message": "", "parts": [{"type": "image", "attachment_id": "att-1"}]},
        headers={"X-Visitor-Id": "tester-1"},
    )
check("附件只经 chat 落库一次", db.stats()["messages"] == before_up + 2, f"({before_up} -> {db.stats()['messages']})")

r = c.post("/api/upload", files={"file": ("a.exe", b"MZ" + b"\x00" * 40, "application/octet-stream")}, headers={"X-Visitor-Id": "tester-1"})
check("拒绝 exe", r.status_code == 400, f"(status={r.status_code})")

r = c.post("/admin/api/settings", json={"online": False, "online_lock": False}, headers=auth_hdr)
check("关闭锁定可离线", r.status_code == 200 and r.json().get("online") is False)
with patch("main.httpx.AsyncClient", _FakeClient):
    r = c.post("/api/chat", json={"message": "留言测试"}, headers={"X-Visitor-Id": "leave-1"})
check("离线走留言", "留言模式" in r.text and "你好呀" not in r.text, f"(body={r.text[:160]})")
r = c.post("/admin/api/settings", json={"online": True, "online_lock": True}, headers=auth_hdr)
check("恢复锁定在线", r.status_code == 200 and r.json().get("online") is True)

r = c.post("/admin/api/inbox/read", headers=auth_hdr)
check("标记已读", r.status_code == 200)
check("已读后未读为 0", db.unread_customer() == 0)

r = c.post("/admin/logout", headers=auth_hdr)
check("logout 200", r.status_code == 200)
r = c.get("/admin/api/stats", headers=auth_hdr)
check("logout 后 cookie 失效", r.status_code == 401, f"(status={r.status_code})")

old_pw = admin_api.ADMIN_PASSWORD
admin_api.ADMIN_PASSWORD = ""
r = c.post("/admin/login", json={"password": "testpass"})
check("未配置密码拒绝", r.status_code == 503, f"(status={r.status_code})")
admin_api.ADMIN_PASSWORD = old_pw
old_sec = admin_api.SECRET
admin_api.SECRET = "changeme-secret"
r = c.post("/admin/login", json={"password": "testpass"})
check("默认 SECRET 拒绝", r.status_code == 503, f"(status={r.status_code})")
admin_api.SECRET = old_sec

r = c.post("/admin/login", json={"password": "testpass"})
check("重新登录 200", r.status_code == 200)
auth_hdr = {"Cookie": f"admin_session={r.cookies.get('admin_session') or c.cookies.get('admin_session')}"}

r = c.get("/desk")
check("desk 页面 200", r.status_code == 200 and "坐席工作台" in r.text)
desk_html = (Path(__file__).resolve().parents[1] / "desk.html").read_text(encoding="utf-8")
check("工作台绑定在线按钮", "$('#presenceBtn').onclick" in desk_html)
check("工作台有手机布局", "max-width:768px" in desk_html and "chat-open" in desk_html)
check("工作台队列顺序", desk_html.find('data-q="queued"') < desk_html.find('data-q="all"') < desk_html.find('data-q="mine"'))

r = c.get("/admin/api/me", headers=auth_hdr)
check("超管 me", r.status_code == 200 and r.json().get("role") == "superadmin")

w = c.get("/api/widget").json()
check("widget 含欢迎语", "guide_text" in w and isinstance(w.get("guide_qs"), list))
check("widget 含转人工词", "转人工" in (w.get("handoff_keywords") or ""))

r = c.post("/admin/api/staff", json={"login_name": "agent1", "name": "坐席一", "password": "agentpass", "role": "agent", "max_chats": 8}, headers=auth_hdr)
check("创建坐席", r.status_code == 200 and r.json().get("login_name") == "agent1", f"(body={r.text[:160]})")
agent_id = r.json()["id"]

r = c.post("/admin/api/skill-groups", json={"name": "售前"}, headers=auth_hdr)
check("创建技能组", r.status_code == 200 and r.json().get("name") == "售前")

r = c.post("/admin/api/canned", json={"title": "问好", "content": "您好，我是人工客服。"}, headers=auth_hdr)
check("快捷回复", r.status_code == 200 and r.json().get("title") == "问好")

r = c.post("/admin/api/canned", json={"title": "怎么收费？", "content": "我们按年收费。"}, headers=auth_hdr)
check("问答已保存", r.status_code == 200)
w = c.get("/api/widget").json()
check("widget 带出问答", any(x.get("q") == "怎么收费？" and x.get("a") == "我们按年收费。" for x in (w.get("guide_qs") or [])))
class _FaqClient(_FakeClient):
    n = 0
    def stream(self, *a, **k):
        type(self).n += 1
        return _Stream()
_FaqClient.n = 0
with patch("main.httpx.AsyncClient", _FaqClient):
    r = c.post("/api/chat", json={"message": "怎么收费？"}, headers={"X-Visitor-Id": "faq-1"})
check("点问题走答案", r.status_code == 200 and "按年收费" in r.text, f"(body={r.text[:200]})")
check("答案不调 AstrBot", _FaqClient.n == 0, f"(n={_FaqClient.n})")

r = c.post("/admin/api/routing", json={"routing_mode": "least_busy", "sticky_enabled": True, "business_hours": {"enabled": False, "start": "09:00", "end": "18:00", "days": [1,2,3,4,5]}}, headers=auth_hdr)
check("保存路由", r.status_code == 200 and r.json().get("routing_mode") == "least_busy")

class _CountClient(_FakeClient):
    n = 0
    def stream(self, *a, **k):
        type(self).n += 1
        return _Stream()

_CountClient.n = 0
with patch("main.httpx.AsyncClient", _CountClient):
    r = c.post("/api/chat", json={"message": "转人工"}, headers={"X-Visitor-Id": "handoff-1"})
check("转人工 200", r.status_code == 200, f"(status={r.status_code})")
check("转人工不调 AstrBot", _CountClient.n == 0, f"(n={_CountClient.n})")
check("转人工有提示", "人工" in r.text, f"(body={r.text[:200]})")
sid = ""
for line in r.text.splitlines():
    if "session_id" in line and line.startswith("data:"):
        try:
            sid = json.loads(line[5:].strip()).get("session_id") or sid
        except Exception:
            pass
check("转人工有 session", bool(sid), f"(sid={sid})")
sess = db.get_session(sid)
check("会话进入排队或已分配", sess and sess["status"] in ("queued", "assigned"), f"(status={sess})")

agent_c = TestClient(main.app)
r = agent_c.post("/admin/login", json={"username": "agent1", "password": "agentpass"})
check("坐席登录", r.status_code == 200 and r.json().get("role") == "agent", f"(body={r.text[:160]})")
agent_hdr = {"Cookie": f"admin_session={r.cookies.get('admin_session') or agent_c.cookies.get('admin_session')}"}

r = agent_c.get("/admin/api/staff", headers=agent_hdr)
check("坐席不能管账号", r.status_code == 403)

if sess and sess["status"] == "queued":
    r = agent_c.post(f"/admin/api/inbox/{sid}/claim", headers=agent_hdr)
    check("认领 200", r.status_code == 200, f"(body={r.text[:160]})")
else:
    check("已自动分配给在线坐席", sess.get("status") == "assigned")

r = agent_c.post(f"/admin/api/inbox/{sid}/reply", json={"text": "我是人工，已收到"}, headers=agent_hdr)
check("人工回复", r.status_code == 200, f"(body={r.text[:160]})")

r = c.get(f"/api/thread?session_id={sid}&after=0", headers={"X-Visitor-Id": "handoff-1"})
check("访客能看到坐席消息", r.status_code == 200 and any("已收到" in (m.get("content") or "") for m in r.json().get("data", [])), f"(body={r.text[:240]})")

_CountClient.n = 0
with patch("main.httpx.AsyncClient", _CountClient):
    r = c.post("/api/chat", json={"message": "还在吗", "session_id": sid}, headers={"X-Visitor-Id": "handoff-1"})
check("人工接入后 AI 停口", _CountClient.n == 0 and "你好呀" not in r.text, f"(n={_CountClient.n} body={r.text[:160]})")

r = agent_c.post(f"/admin/api/inbox/{sid}/resolve", headers=agent_hdr)
check("结束会话", r.status_code == 200 and r.json().get("status") == "resolved")

r = c.post("/api/csat", json={"session_id": sid, "score": 5}, headers={"X-Visitor-Id": "handoff-1"})
check("满意度", r.status_code == 200)

r = c.get("/admin/api/csat", headers=auth_hdr)
check("满意度统计", r.status_code == 200 and r.json().get("count") >= 1)

r = c.get("/admin/api/reports/agents", headers=auth_hdr)
check("坐席报表", r.status_code == 200 and isinstance(r.json().get("data"), list))

r = c.get("/admin/api/inbox?queue=all", headers=auth_hdr)
check("收件箱全部", r.status_code == 200 and isinstance(r.json().get("data"), list))

png2 = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
with patch("main.httpx.AsyncClient", _FakeClient):
    r = c.post(
        "/api/upload",
        files={"file": ("keep.png", png2, "image/png")},
        headers={"X-Visitor-Id": "img-keep-1"},
    )
up = r.json()
check("upload 返回可访问 url", str(up.get("url") or "").startswith("/api/chat-file/"), f"(body={r.text[:160]})")
r = c.get(up["url"])
check("本机存了原图", r.status_code == 200 and r.content.startswith(b"\x89PNG"), f"(status={r.status_code})")
with patch("main.httpx.AsyncClient", _FakeClient):
    r = c.post(
        "/api/chat",
        json={"message": "", "parts": [{"type": "image", "attachment_id": up.get("attachment_id"), "url": up.get("url")}]},
        headers={"X-Visitor-Id": "img-keep-1"},
    )
stored = [m for m in db.query_messages() if m.get("msg_type") == "image" and (up.get("url") or "") in (m.get("content") or "")]
check("图片地址已落库", len(stored) >= 1, f"(n={len(stored)})")

r = c.post("/admin/api/staff", json={"login_name": "admin", "name": "后台管理员", "password": "consolepass", "role": "admin", "max_chats": 8}, headers=auth_hdr)
check("总控台可建 admin 登录名", r.status_code == 200, f"(body={r.text[:160]})")
ops_c = TestClient(main.app)
r = ops_c.post("/admin/login", json={"username": "admin", "password": "consolepass"})
check("总控台账号能登后台", r.status_code == 200 and r.json().get("role") == "admin", f"(body={r.text[:160]})")
ops_hdr = {"Cookie": f"admin_session={r.cookies.get('admin_session') or ops_c.cookies.get('admin_session')}"}
r = ops_c.get("/admin/api/me", headers=ops_hdr)
check("总控台账号 me 是管理员", r.status_code == 200 and r.json().get("role") == "admin", f"(body={r.text[:160]})")
r = ops_c.post("/admin/login", json={"username": "后台管理员", "password": "consolepass"})
check("显示名也能登", r.status_code == 200 and r.json().get("role") == "admin", f"(body={r.text[:160]})")
r = c.post("/admin/login", json={"password": "testpass"})
check("超管仍可空用户名登录", r.status_code == 200 and r.json().get("role") == "superadmin")

print("\n=== 生产向集成测试：全部通过 ===")
print(f"临时库: {os.environ['XIAOMEI_DB']}")
