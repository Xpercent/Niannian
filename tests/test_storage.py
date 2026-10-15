import sqlite3

import pytest

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


def count(db, sql):
    """断言用的裸计数：库层不再为界面提供全局计数汇总（界面一个都没读）。"""
    with db.connect() as conn:
        return conn.execute(sql).fetchone()[0]


def test_quoted_targets_maps_sort_seq_to_row_and_skips_missing(tmp_path):
    """引用跳转：只有本库里真有那行才给目标，没导入的不能出现（否则前端会跳空）。"""
    db = make_db(tmp_path)
    sid = seed(db)
    db.ingest([payload(sid, "wechat:1", sort=100), payload(sid, "wechat:2", sort=200)])
    page = db.messages_page(sid, limit=10)["items"]
    row_id = next(item["id"] for item in page if item["sort_seq"] == 100)
    assert db.quoted_targets(sid, {100, 999, 0}) == {100: row_id}
    assert db.quoted_targets(sid, {999}) == {}
    # 别的会话里同 sort_seq 的行不能算命中
    other = seed(db, sid="wechat:sc:u2", account="wxid_me")
    db.ingest([payload(other, "wechat:9", sort=100)])
    assert db.quoted_targets(other, {100})[100] != db.quoted_targets(sid, {100})[100]


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
    assert count(db, "SELECT COUNT(*) FROM messages") == 2


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
    assert count(db, "SELECT COUNT(*) FROM messages") == 1
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
    assert count(db, "SELECT COUNT(*) FROM sessions WHERE monitored=1") == 1


def image_row(sid, source, seq, digest, linked=False, tier=None):
    meta = {"attachment": {"kind": "image"}, "media_md5": digest, "local_id": seq}
    if linked:
        meta["local_file"] = f"scope/{source}.jpg"
        if tier:
            meta["tier"] = tier
    row = payload(sid, source, sort=seq)
    row["metadata"] = meta
    return row


def test_projection_columns_follow_metadata_on_both_write_paths(tmp_path):
    """投影列只由 metadata 算，且 ingest / update_metadata 两条写入口都要刷新它。

    采集侧每轮"该补哪几行"的判定全看列（这样才走得上部分索引），所以列一旦和
    metadata 漂移，就会漏补链接或反复重做——这条不变式必须由测试钉住。
    """
    db = make_db(tmp_path)
    sid = seed(db)
    db.set_monitored(sid, True)
    db.ingest([image_row(sid, "wechat:1", 300, "a" * 32)])
    with db.connect() as conn:
        row = conn.execute("SELECT id, kind, media_md5, local_file, tier FROM messages "
                           "WHERE source_id='wechat:1'").fetchone()
    assert tuple(row[1:]) == ("image", "a" * 32, "", "")
    message_id = row[0]

    db.update_metadata(message_id, {"attachment": {"kind": "image"},
                                    "media_md5": "a" * 32,
                                    "local_file": "scope/x.jpg", "tier": "thumb"})
    with db.connect() as conn:
        after = conn.execute("SELECT local_file, tier FROM messages WHERE id=?",
                             (message_id,)).fetchone()
    assert tuple(after) == ("scope/x.jpg", "thumb")
    # 链接补上之后就不该再在"缺链接"的队列里，而该出现在"待升级"的队列里
    assert db.pending_images(10) == []
    assert [r["sort_seq"] for r in db.stale_tier_images(10)] == [300]


def test_worklist_reads_columns_not_metadata_text(tmp_path):
    """该不该补只看列，不看 metadata 文本里有没有那一串字。

    原来用 ``instr(metadata, '"kind": "image"')`` 粗筛：既绑死 ``json.dumps`` 的
    分隔符形状，又让一行随便写坏的文本影响队列。现在文本怎么写都不算数。
    """
    db = make_db(tmp_path)
    sid = seed(db)
    db.set_monitored(sid, True)
    db.ingest([image_row(sid, "wechat:1", 300, "a" * 32), payload(sid, "wechat:2", sort=400)])
    with db.connect() as conn:
        conn.execute("""UPDATE messages SET metadata='{"attachment": {"kind": "image"}, '
                        || '"media_md5": "' || printf('%032x', 2) || '"}' WHERE source_id=?""",
                     ("wechat:2",))
    # wechat:2 的文本现在"看起来"就是一张缺链接的图片，但它没有投影列，不算图片行
    assert [row["sort_seq"] for row in db.pending_images(10)] == [300]


def test_old_database_reports_rebuild_instead_of_crashing(tmp_path):
    """老库缺投影列：一句话说清"删掉重采"，别让每轮采集撞在 no such column 上。"""
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          session_id TEXT NOT NULL, source_id TEXT NOT NULL UNIQUE,
                          sender_key TEXT NOT NULL DEFAULT '',
                          sender_name TEXT NOT NULL DEFAULT '',
                          is_self INTEGER NOT NULL DEFAULT 0,
                          content TEXT NOT NULL DEFAULT '',
                          message_type TEXT NOT NULL DEFAULT '文本',
                          created_at TEXT NOT NULL, sort_seq INTEGER NOT NULL DEFAULT 0,
                          metadata TEXT NOT NULL DEFAULT '{}')""")
    with pytest.raises(RuntimeError) as caught:
        Database(path).init()
    assert "重新采集" in str(caught.value) and "kind" in str(caught.value)


def test_stale_tier_images_lists_rows_below_original(tmp_path):
    """升级候选清单：关注会话里"有链接但还没拿到原图"的行，新→旧。

    没链接的走 `pending_images` 那条路，已经到原图的没有更高一档可升，都不该出现。
    """
    db = make_db(tmp_path)
    sid = seed(db)
    other = seed(db, sid="wechat:sc:u2")
    db.set_monitored(sid, True)
    db.ingest([image_row(sid, "wechat:1", 300, "a" * 32, linked=True, tier="thumb"),
               image_row(sid, "wechat:2", 400, "b" * 32, linked=True, tier="mid"),
               image_row(sid, "wechat:3", 500, "c" * 32, linked=True, tier="original"),
               image_row(sid, "wechat:4", 600, "d" * 32),
               image_row(other, "wechat:5", 999, "e" * 32, linked=True, tier="thumb")])
    got = db.stale_tier_images(limit=10)
    assert [row["sort_seq"] for row in got] == [400, 300]
    assert got[0]["metadata"]["tier"] == "mid"


def test_pending_images_only_unlinked_images_of_followed_sessions(tmp_path):
    """补图队列：只收关注会话里"有哈希、还没链接"的图片行，新→旧。

    这是采集线程每轮取的工作清单，范围必须与采集范围一致：不替用户没关注的会话
    补图，也不能把已经补好的行反复重做。
    """
    db = make_db(tmp_path)
    sid = seed(db)
    other = seed(db, sid="wechat:sc:u2")
    db.set_monitored(sid, True)
    db.ingest([image_row(sid, "wechat:1", 300, "a" * 32),
               image_row(sid, "wechat:2", 400, "b" * 32),
               image_row(sid, "wechat:3", 500, "c" * 32, linked=True),
               payload(other, "wechat:4", sort=900),
               image_row(other, "wechat:5", 999, "d" * 32)])
    got = db.pending_images(limit=10)
    assert {(row["session_id"], row["sort_seq"]) for row in got} == {(sid, 400), (sid, 300)}
    assert got[0]["sort_seq"] == 400                    # 新→旧：正在看的这一屏先补
    assert got[0]["metadata"]["media_md5"] == "b" * 32
    assert [row["sort_seq"] for row in db.pending_images(limit=1)] == [400]


def test_pending_images_survives_broken_metadata(tmp_path):
    """metadata 是文本列：一行坏 JSON 不能把每轮都要跑的补图查询整条炸掉。

    所以粗筛用 ``instr`` 而不是 ``json_extract``；坏行交回来是 ``metadata=None``，
    采集侧跳过它，别的好行照常处理。
    """
    db = make_db(tmp_path)
    sid = seed(db)
    db.set_monitored(sid, True)
    db.ingest([image_row(sid, "wechat:1", 300, "a" * 32),
               image_row(sid, "wechat:2", 400, "b" * 32)])
    with db.connect() as conn:
        conn.execute("""UPDATE messages SET metadata='{"attachment": {"kind": "image"}, '
                        || '"media_md5": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"'
                        WHERE source_id=?""", ("wechat:1",))
    rows = db.pending_images(limit=10)
    assert [row["sort_seq"] for row in rows] == [400, 300]
    broken = next(row for row in rows if row["sort_seq"] == 300)
    assert broken["metadata"] is None


def test_search_messages_matches_substring_newest_first(tmp_path):
    """搜正文：子串命中，新→旧，返回跳转要用的 sort_seq。"""
    db = make_db(tmp_path)
    sid = seed(db)
    db.ingest([payload(sid, "wechat:s1", content="赛希带什么装备", sort=300),
               payload(sid, "wechat:s2", content="说实话赛希我也不知道", sort=500),
               payload(sid, "wechat:s3", content="别的都不相关", sort=400)])
    hits = db.search_messages(sid, "赛希")
    assert [h["sort_seq"] for h in hits] == [500, 300]
    assert hits[0]["content"] == "说实话赛希我也不知道"
    assert hits[0]["id"] and hits[0]["created_at"]
    assert db.search_messages(sid, "赛希", limit=1)[0]["sort_seq"] == 500


def test_search_messages_treats_wildcards_as_literal_text(tmp_path):
    """关键词里的 % 和 _ 必须当字面量：否则搜「100%」会捞出一堆不相干的。"""
    db = make_db(tmp_path)
    sid = seed(db)
    db.ingest([payload(sid, "wechat:w1", content="7天内充多少赠送多少hh", sort=100),
               payload(sid, "wechat:w2", content="进度 100% 了", sort=200),
               payload(sid, "wechat:w3", content="进度 100x 了", sort=300),
               payload(sid, "wechat:w4", content="a_b 下划线", sort=400),
               payload(sid, "wechat:w5", content="axb 下划线", sort=500)])
    assert [h["sort_seq"] for h in db.search_messages(sid, "100%")] == [200]
    assert [h["sort_seq"] for h in db.search_messages(sid, "a_b")] == [400]
    # 「1_0」不该把 _ 当单字通配符去匹配 "100"：不转义时这条会命中 200/300 两行
    assert db.search_messages(sid, "1_0") == []


def test_search_messages_is_scoped_to_the_session(tmp_path):
    """只搜这一个会话；空白关键词不查库。"""
    db = make_db(tmp_path)
    sid = seed(db)
    other = seed(db, sid="wechat:sc:u2")
    db.ingest([payload(sid, "wechat:c1", content="中转站ds享福了", sort=100),
               payload(other, "wechat:c2", content="中转站ds享福了", sort=900)])
    assert [h["session_id"] for h in db.search_messages(sid, "中转站")] == [sid]
    assert db.search_messages(sid, "") == []
    assert db.search_messages(sid, "   ") == []
