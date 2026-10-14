from niannian.storage import Database


def make_db(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init()
    return db


def payload(sid, source, content="hi", sort=1, created="2026-10-14T00:00:00+00:00"):
    return {"session_id": sid, "source_id": source, "sender_key": "wxid_a",
            "sender_name": "甲", "is_self": False, "content": content,
            "message_type": "文本", "created_at": created, "sort_seq": sort,
            "metadata": {}}


def seed(db, sid="wechat:sc:u1", account="wxid_me"):
    db.sync_catalog(account, [{"id": sid, "username": "u1", "name": "会话一",
                               "unread": 0, "last_message_at": None}])
    return sid


def test_catalog_upsert_preserves_monitored(tmp_path):
    db = make_db(tmp_path)
    sid = seed(db)
    assert db.set_monitored(sid, True) is True
    db.sync_catalog("wxid_me", [{"id": sid, "username": "u1", "name": "改名了",
                                 "unread": 5, "last_message_at": None}])
    row = db.session(sid)
    assert row["monitored"] is True
    assert row["name"] == "改名了"
    assert row["unread"] == 5


def test_ingest_dedupes_and_checkpoint_never_regresses(tmp_path):
    db = make_db(tmp_path)
    sid = seed(db)
    inserted = db.ingest([payload(sid, "wechat:a"), payload(sid, "wechat:a")],
                         {"account": "wxid_me", "session_id": sid, "sort_seq": 7})
    assert inserted == 1
    # 更旧的水位不能把检查点拉回去
    db.ingest([payload(sid, "wechat:b", sort=8)],
              {"account": "wxid_me", "session_id": sid, "sort_seq": 3})
    assert db.watermark("wxid_me", sid) == 7
    db.ingest([], {"account": "wxid_me", "session_id": sid, "sort_seq": 9})
    assert db.watermark("wxid_me", sid) == 9
    assert db.counts()["message_count"] == 2


def test_messages_page_cursor(tmp_path):
    db = make_db(tmp_path)
    sid = seed(db)
    db.ingest([payload(sid, f"wechat:{i}", content=f"m{i}", sort=i)
               for i in range(1, 6)])
    page = db.messages_page(sid, limit=2)
    assert [m["content"] for m in page["items"]] == ["m5", "m4"]
    assert page["has_more"] is True
    older = db.messages_page(sid, limit=2, before_seq=page["before_seq"],
                             before_id=page["before_id"])
    assert [m["content"] for m in older["items"]] == ["m3", "m2"]
    tail = db.messages_page(sid, limit=2, before_seq=older["before_seq"],
                            before_id=older["before_id"])
    assert [m["content"] for m in tail["items"]] == ["m1"]
    assert tail["has_more"] is False


def test_messages_page_orders_by_time_not_by_insertion(tmp_path):
    """重新导入历史会把更旧的消息以**更大**的自增 id 插进来；翻页仍必须按时间倒序。

    这条就是"按 id 排"会翻车的情形。
    """
    db = make_db(tmp_path)
    sid = seed(db)
    db.ingest([payload(sid, f"new:{i}", content=f"新{i}", sort=100 + i) for i in (1, 2)])
    db.ingest([payload(sid, f"old:{i}", content=f"旧{i}", sort=10 + i) for i in (1, 2)])
    first = db.messages_page(sid, limit=3)
    assert [m["content"] for m in first["items"]] == ["新2", "新1", "旧2"]
    rest = db.messages_page(sid, limit=3, before_seq=first["before_seq"],
                            before_id=first["before_id"])
    assert [m["content"] for m in rest["items"]] == ["旧1"]
    assert rest["has_more"] is False


def test_messages_page_same_sort_seq_tiebreak_is_stable(tmp_path):
    """同一 sort_seq 并列时按 id 倒序破并列：可复现，且不重复、不漏条。"""
    db = make_db(tmp_path)
    sid = seed(db)
    db.ingest([payload(sid, "a", content="甲", sort=50),
               payload(sid, "b", content="乙", sort=50)])
    page = db.messages_page(sid, limit=5)
    assert [m["content"] for m in page["items"]] == ["乙", "甲"]
    again = db.messages_page(sid, limit=5, before_seq=page["before_seq"],
                             before_id=page["before_id"])
    assert again["items"] == []


def test_history_limit_is_per_session(tmp_path):
    """会话自己的条数与全局配置解耦：改一个不该影响其它会话。"""
    db = make_db(tmp_path)
    sid = seed(db)
    other = seed(db, sid="wechat:sc:u2")
    assert db.session(sid)["history_limit"] == 0        # 0 = 未设过，跟随全局
    assert db.set_history_limit(sid, 500) is True
    assert db.session(sid)["history_limit"] == 500
    assert db.session(other)["history_limit"] == 0
    assert db.set_history_limit("wechat:sc:missing", 500) is False
    # 目录快照刷新不能把会话自己设的条数冲掉
    db.sync_catalog("wxid_me", [{"id": sid, "username": "u1", "name": "改名",
                                 "unread": 1, "last_message_at": None}])
    assert db.session(sid)["history_limit"] == 500


def test_clear_watermark_keeps_messages(tmp_path):
    """重新导入只清水位：已入库消息一条都不能少，水位清掉后才会重新导。"""
    db = make_db(tmp_path)
    sid = seed(db)
    db.ingest([payload(sid, "wechat:1", sort=9)],
              checkpoint={"account": "wxid_me", "session_id": sid, "sort_seq": 9})
    assert db.watermark("wxid_me", sid) == 9
    db.clear_watermark("wxid_me", sid)
    assert db.watermark("wxid_me", sid) is None
    assert db.counts()["message_count"] == 1
    # 水位重新建立时不会被旧的更小的值拉回去
    db.ingest([payload(sid, "wechat:1", sort=9)],
              checkpoint={"account": "wxid_me", "session_id": sid, "sort_seq": 12})
    assert db.watermark("wxid_me", sid) == 12


def test_monitored_filter_and_counts(tmp_path):
    db = make_db(tmp_path)
    seed(db)
    seed(db, sid="wechat:sc:u2")
    db.set_monitored("wechat:sc:u2", True)
    rows = db.list_sessions(monitored_only=True)
    assert [r["id"] for r in rows] == ["wechat:sc:u2"]
    assert db.counts()["monitored_count"] == 1
