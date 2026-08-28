"""会话路由：技能组、轮询 / 饱和度、熟客跟进、上下班。"""
from __future__ import annotations

import db

HANDOFF_NOTICE = {
    "assigned": "已为你接入人工客服，请稍候。",
    "queued": "正在为你转接人工客服，请稍候。当前坐席全忙或已下班，排队中。",
}


def wants_handoff(text: str) -> bool:
    raw = (db.default_brand().get("handoff_keywords") or "转人工,人工客服,找人工")
    t = (text or "").strip()
    if not t:
        return False
    return any(k.strip() and k.strip() in t for k in raw.split(","))


def handoff_notice(status: str) -> str:
    return HANDOFF_NOTICE.get(status, HANDOFF_NOTICE["queued"])


def human_status(status: str | None) -> bool:
    return (status or "") in ("queued", "assigned")


def pick_agent(visitor_id: str | None, skill_group_id: int | None = None) -> dict | None:
    if db.get_setting("sticky_enabled", "1") == "1" and visitor_id:
        aid = db.last_assignee_for_visitor(visitor_id)
        if aid:
            by_id = {int(a["id"]): a for a in db.available_agents(skill_group_id)}
            if aid in by_id:
                return by_id[aid]
    agents = db.available_agents(skill_group_id)
    if not agents:
        agents = db.available_agents(None)
    if not agents:
        return None
    mode = db.get_setting("routing_mode", "least_busy")
    if mode == "round_robin":
        agents = sorted(agents, key=lambda a: int(a["id"]))
        idx = 0
        try:
            idx = int(db.get_setting("rr_index", "0") or 0)
        except (TypeError, ValueError):
            idx = 0
        pick = agents[idx % len(agents)]
        db.set_setting("rr_index", str(idx + 1))
        return pick
    agents.sort(key=lambda a: (int(a.get("open_chats") or 0), int(a["id"])))
    return agents[0]


def enqueue_or_assign(session_id: str, visitor_id: str | None, skill_group_id: int | None = None) -> str:
    sess = db.get_session(session_id) or {}
    cur = sess.get("status") or "bot"
    if cur in ("queued", "assigned"):
        return cur
    gid = skill_group_id or sess.get("skill_group_id") or 1
    if not db.within_business_hours():
        db.set_session_fields(session_id, status="queued", skill_group_id=gid, assignee_id=None)
        return "queued"
    agent = pick_agent(visitor_id, gid)
    if agent:
        db.set_session_fields(
            session_id,
            status="assigned",
            assignee_id=int(agent["id"]),
            skill_group_id=int(agent.get("skill_group_id") or gid),
        )
        return "assigned"
    db.set_session_fields(session_id, status="queued", skill_group_id=gid, assignee_id=None)
    return "queued"


def claim(session_id: str, staff_id: int) -> str:
    sess = db.get_session(session_id)
    if not sess:
        raise ValueError("会话不存在")
    if sess.get("status") == "assigned" and int(sess.get("assignee_id") or 0) == staff_id:
        return "assigned"
    if sess.get("status") not in ("queued", "bot", "active"):
        raise ValueError("该会话不可认领")
    db.set_session_fields(session_id, status="assigned", assignee_id=staff_id)
    return "assigned"


def transfer(session_id: str, to_staff_id: int) -> str:
    staff = db.get_staff(to_staff_id)
    if not staff or not staff.get("enabled"):
        raise ValueError("目标坐席不可用")
    sess = db.get_session(session_id)
    if not sess:
        raise ValueError("会话不存在")
    db.set_session_fields(
        session_id,
        status="assigned",
        assignee_id=to_staff_id,
        skill_group_id=staff.get("skill_group_id") or sess.get("skill_group_id"),
    )
    return "assigned"


def resolve(session_id: str) -> str:
    if not db.get_session(session_id):
        raise ValueError("会话不存在")
    db.set_session_fields(session_id, status="resolved")
    return "resolved"
