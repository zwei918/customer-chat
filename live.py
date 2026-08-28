"""进程内事件总线：访客线程与坐席收件箱刷新。"""
from __future__ import annotations

import asyncio
from collections import defaultdict

_queues: dict[str, list[asyncio.Queue]] = defaultdict(list)


def publish(topic: str, payload: dict) -> None:
    body = dict(payload)
    body.setdefault("topic", topic)
    for key in (topic, "desk"):
        for q in list(_queues.get(key, [])):
            try:
                q.put_nowait(body)
            except asyncio.QueueFull:
                pass


def subscribe(topic: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _queues[topic].append(q)
    return q


def unsubscribe(topic: str, q: asyncio.Queue) -> None:
    lst = _queues.get(topic) or []
    if q in lst:
        lst.remove(q)
