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
    older = db.messages_page(sid, limit=2, before_id=page["before_id"])
    assert [m["content"] for m in older["items"]] == ["m3", "m2"]
    tail = db.messages_page(sid, limit=2, before_id=older["before_id"])
    assert [m["content"] for m in tail["items"]] == ["m1"]
    assert tail["has_more"] is False


def test_monitored_filter_and_counts(tmp_path):
    db = make_db(tmp_path)
    seed(db)
    seed(db, sid="wechat:sc:u2")
    db.set_monitored("wechat:sc:u2", True)
    rows = db.list_sessions(monitored_only=True)
    assert [r["id"] for r in rows] == ["wechat:sc:u2"]
    assert db.counts()["monitored_count"] == 1
