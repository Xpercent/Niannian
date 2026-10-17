"""待办页要的那几条读接口与改状态接口：全部打在替身采集器 + 真库上，不出网。"""

import json
from datetime import timedelta

import pytest
from fastapi import HTTPException

from niannian.api import create_app
from niannian.ids import session_key
from niannian.storage import SHANGHAI, Database, shanghai_bounds

ACCOUNT = "wxid_me"


def shifted(iso: str, **delta) -> str:
    """从 shanghai_bounds 给的形状挪一点，保持**同一种形状**（才能比字典序）。"""
    from datetime import datetime
    return (datetime.fromisoformat(iso) + timedelta(**delta)).isoformat(timespec="minutes")


def message(sid, seq, content="明天把表交一下"):
    return {"session_id": sid, "source_id": f"wechat:{seq}", "sender_key": "wxid_other",
            "sender_name": "老王", "is_self": False, "content": f"{content}{seq}",
            "message_type": "文本", "created_at": "2026-10-11T12:00:00+00:00",
            "sort_seq": seq, "metadata": {}}


@pytest.fixture
def seeded(tmp_path):
    """一个关注会话 + 三条已提炼消息 + 三条任务（逾期 / 今天 / 明天）。

    库要落在 `data/niannian.db`：`create_app` 打开的就是这个路径，接口用例与存储用例
    才是在看同一份数据。
    """
    db = Database(tmp_path / "data" / "niannian.db")
    db.init()
    sid = session_key(ACCOUNT, "45948752706@chatroom")
    db.sync_catalog(ACCOUNT, [{"id": sid, "username": sid, "name": "26软工3班通知群",
                               "unread": 0, "last_message_at": None}])
    db.set_monitored(sid, True)
    db.ingest([message(sid, 100), message(sid, 101), message(sid, 102)])
    day_start, day_end, _now = shanghai_bounds()
    db.commit_extraction([
        {"session_id": sid, "title": "提交模考截图", "description": "私信给课代表",
         "deadline": shifted(day_start, days=-1, hours=20), "priority": "high",
         "sources": [100, 102]},
        {"session_id": sid, "title": "填投票", "description": "",
         "deadline": shifted(day_start, hours=20), "priority": "medium",
         "sources": [101]},
        {"session_id": sid, "title": "交心得", "description": "周四之前",
         "deadline": shifted(day_end, hours=3), "priority": "low", "sources": []},
    ], [row["id"] for row in db.messages_page(sid, limit=10)["items"]])
    db.update_task(3, {"status": "completed"})
    return db, sid


# ------------------------------------------------------------------ 读侧


def test_due_filters_read_the_stored_shape(seeded):
    db, _ = seeded
    day_start, _day_end, now = shanghai_bounds()
    vote = shifted(day_start, hours=20)

    assert [t["title"] for t in db.list_tasks(status="all", due="today")] == ["填投票"]
    assert [t["title"] for t in db.list_tasks(status="all", due="upcoming")] == ["交心得"]
    # "今天截止"看日历、"已逾期"表现在几点，两档的重叠取决于跑测试的时刻——
    # 这里按同一份字典序自己算一遍，不靠"现在是白天"这种假设。
    expected = ["提交模考截图"] + ([ "填投票"] if vote < now else [])
    assert sorted(t["title"] for t in db.list_tasks(status="all", due="overdue")) == sorted(expected)
    # 状态筛选照样叠加：逾期那条一旦做完就不再催
    db.update_task(1, {"status": "completed"})
    assert "提交模考截图" not in [t["title"] for t in db.list_tasks(due="overdue")]


def test_search_also_matches_the_session_name(seeded):
    db, _ = seeded
    assert [t["title"] for t in db.list_tasks(status="all", query="软工")] == [
        "提交模考截图", "填投票", "交心得"]
    assert [t["title"] for t in db.list_tasks(status="all", query="课代表")] == ["提交模考截图"]


def test_task_detail_returns_source_messages_oldest_first(seeded):
    db, _ = seeded

    task = db.task(1)

    assert task["session_name"] == "26软工3班通知群"
    assert task["sources"] == [100, 102]
    # 依据原文按消息先后回；顺序由 sort_seq 定，不是由模型给的编号定
    assert [m["sort_seq"] for m in task["messages"]] == [100, 102]
    assert task["messages"][0]["content"].endswith("100")
    assert db.task(999) is None


def test_task_detail_ignores_a_source_that_was_never_imported(seeded):
    db, _ = seeded
    db.update_task(1, {})       # 只为确认空改动不写库
    with db.connect() as conn:
        conn.execute("UPDATE tasks SET sources='[100, 777777]' WHERE id=1")

    assert [m["sort_seq"] for m in db.task(1)["messages"]] == [100]
    assert db.task(1)["sources"] == [100, 777777]


def test_update_task_writes_only_the_whitelisted_columns(seeded):
    db, sid = seeded

    changed = db.update_task(2, {"title": "填写运动会投票", "status": "completed",
                                 "sources": [999], "session_id": "别处的会话",
                                 "created_at": "编一个时间"})

    assert changed is True
    task = db.task(2)
    assert (task["title"], task["status"]) == ("填写运动会投票", "completed")
    # 依据、归属会话与创建时间不由界面改：改了就没人能跳回原文
    assert task["sources"] == [101] and task["session_id"] == sid
    assert db.update_task(999, {"title": "没人"} ) is False


def test_stats_answer_the_cards_and_the_nav_badge(seeded):
    db, _ = seeded
    day_start, _day_end, now = shanghai_bounds()
    vote = shifted(day_start, hours=20)

    stats = db.task_stats()

    # 三条任务里已完成一条：进行中 2、累计完成 1
    assert (stats["pending"], stats["completed"], stats["cancelled"]) == (2, 1, 0)
    assert stats["due_today"] == 1
    assert stats["overdue"] == 1 + (1 if vote < now else 0)
    assert stats["messages"] == 3 and stats["monitored"] == 1
    # 这一轮把三条都标成已提炼了，积压是 0
    assert stats["queued"] == 0
    db.update_task(2, {"status": "completed"})
    after = db.task_stats()
    assert (after["pending"], after["due_today"]) == (1, 0)


# ------------------------------------------------------------------ 接口


class QuietCollector:
    def __init__(self, db, settings, root):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def status(self):
        return {"state": "connected", "account": ACCOUNT}

    def catalog(self):
        return []


def endpoint(app, path, method="GET"):
    """同一条路径可以挂两个方法（GET 详情与 PATCH 改），所以要连方法一起认。"""
    return next(route.endpoint for route in app.routes
                if getattr(route, "path", "") == path
                and method in (getattr(route, "methods", None) or set()))


@pytest.fixture
def app(tmp_path, monkeypatch, seeded):
    monkeypatch.setattr("niannian.api.Collector", QuietCollector)
    return create_app(tmp_path)


def test_stats_route_is_not_swallowed_by_the_id_route(app):
    """``/api/tasks/stats`` 必须排在 ``/api/tasks/{task_id}`` 前面，否则 "stats" 会被拿去转 int。"""
    paths = [route.path for route in app.routes
             if getattr(route, "path", "").startswith("/api/tasks")]

    assert paths.index("/api/tasks/stats") < paths.index("/api/tasks/{task_id}")
    assert endpoint(app, "/api/tasks/stats")()["pending"] == 2


def test_tasks_endpoint_passes_every_filter_through(app, seeded):
    _db, sid = seeded

    got = endpoint(app, "/api/tasks")(session_id=sid, status="all", priority="high",
                                      q="", due="overdue", limit=100)

    assert [t["title"] for t in got["items"]] == ["提交模考截图"]


def test_detail_endpoint_labels_types_and_says_404(app):
    got = endpoint(app, "/api/tasks/{task_id}")(1)

    assert got["session_name"] == "26软工3班通知群"
    assert [m["type_label"] for m in got["messages"]] == ["文本", "文本"]
    with pytest.raises(HTTPException) as error:
        endpoint(app, "/api/tasks/{task_id}")(999)
    assert error.value.status_code == 404


@pytest.mark.parametrize("payload,phrase", [
    ({"title": "   "}, "标题不能空着"),
    ({"priority": "urgent"}, "优先级只能是高、普通、低"),
    ({"status": "done"}, "状态只能是进行中"),
    ({"deadline": "下周三"}, "截止时间认不出来"),
    ({}, "没有要修改的内容"),
])
def test_patch_refuses_bad_input_in_plain_chinese(app, payload, phrase):
    with pytest.raises(HTTPException) as error:
        endpoint(app, "/api/tasks/{task_id}", "PATCH")(2, payload)

    assert error.value.status_code == 400
    assert phrase in error.value.detail
    assert app.state.store.task(2)["title"] == "填投票"      # 一条都没写进去


def test_patch_takes_the_form_time_through_the_same_parser(app):
    """界面上 `datetime-local` 交来的是没时区的 "2026-10-18T12:00"，落成库里那一种形状。"""
    patch = endpoint(app, "/api/tasks/{task_id}", "PATCH")

    assert patch(2, {"deadline": "2026-10-18T12:00"})["deadline"] == "2026-10-18T12:00+08:00"
    assert patch(2, {"deadline": ""})["deadline"] is None
    with pytest.raises(HTTPException) as error:
        patch(999, {"status": "pending"})
    assert error.value.status_code == 404
