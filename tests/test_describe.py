from niannian.describe import clock, describe, human_size, summarize

IMAGE_XML = (
    '<?xml version="1.0"?>\n<msg>\n\t<img aeskey="d427de8ff397a962a0d17fa70e5defb6" '
    'encryver="1" cdnthumblength="9882" cdnthumbheight="210" cdnthumbwidth="118" '
    'length="76183" md5="6793b97ea4cb8f58ba2f65086e3b761b"><live><md5 /></live>'
    "</img>\n</msg>"
)

FILE_XML = (
    '<msg><appmsg appid="" sdkver="0"><title>大赛报名表.docx</title><type>6</type>'
    "<appattach><totallen>14129</totallen><fileext>docx</fileext></appattach>"
    "<md5>796187102be7d810581c572eb17d1bc1</md5></appmsg>"
    "<fromusername>wxid_a</fromusername></msg>"
)

LINK_XML = (
    '<msg><appmsg><title>古诗词音乐作品大赛的通知</title><type>5</type>'
    "<url>https://mp.weixin.qq.com/s?x=1</url><sourcedisplayname>某某之声</sourcedisplayname>"
    "</appmsg><fromusername>wxid_a</fromusername></msg>"
)

# 外链卡片也常挂一个字段全空的 <weappinfo>，不能被当成小程序。
LINK_EMPTY_WEAPPINFO_XML = (
    '<msg><appmsg><title>B站视频分享</title><des>UP主：某人</des><type>4</type>'
    "<url>https://b23.tv/abc</url><weappinfo><username /><appid /></weappinfo>"
    "</appmsg></msg>"
)

MINI_XML = (
    '<msg><appmsg><title>近代史</title><type>33</type><sourcedisplayname>接龙管家</sourcedisplayname>'
    "<weappinfo><username>gh_123@app</username><appid>wx36ddf</appid>"
    "<pagepath>pages/detail.html?id=1</pagepath></weappinfo></appmsg></msg>"
)

PAT_XML = (
    '<msg><appmsg><type>62</type><title>"甲" 拍了拍 "乙" 说：“家里管饭吗”</title>'
    "<patinfo><fromusername>wxid_a</fromusername><chatusername>123@chatroom</chatusername>"
    "<pattedusername>wxid_b</pattedusername><patsuffix>说：“家里管饭吗”</patsuffix>"
    "</patinfo></appmsg><fromusername></fromusername></msg>"
)

MERGED_XML = (
    "<msg><appmsg><title>群聊的聊天记录</title><des>甲: 一\n乙: 二</des><type>19</type>"
    "<recorditem><![CDATA[<recordinfo><datalist count=\"2\">"
    '<dataitem datatype="1"><datadesc>这个是文件说明</datadesc><sourcename>甲</sourcename>'
    "<sourcetime>2026-10-13 16:24:10</sourcetime></dataitem>"
    '<dataitem datatype="8"><datatitle>通知.pdf</datatitle><sourcename>乙</sourcename>'
    "<sourcetime>2026-10-13 16:24:00</sourcetime></dataitem>"
    "</datalist></recordinfo>]]></recorditem></appmsg></msg>"
)

TRANSFER_XML = (
    "<msg><appmsg><title><![CDATA[微信转账]]></title>"
    "<des><![CDATA[收到转账8.00元]]></des><type>2000</type><wcpayinfo>"
    "<paysubtype>3</paysubtype><feedesc><![CDATA[￥8.00]]></feedesc>"
    "<transferid>100005000120261009033</transferid>"
    "<transcationid>53010003396142</transcationid>"
    "<receiver_username>wxid_me</receiver_username><payer_username>wxid_a</payer_username>"
    "</wcpayinfo></appmsg></msg>"
)

EMOJI_XML = (
    '<msg><emoji fromusername = "wxid_a" tousername = "123@chatroom" type="2" '
    'md5="62a2ee79596831ee958833eab3fb7c03" len = "26287" width= "518" height= "474" '
    "desc= \"\" ></emoji><gameext type=\"0\" content=\"0\" ></gameext></msg>"
)

VIDEO_XML = (
    '<msg>\n\t<videomsg aeskey="b6e5142cf70d3e0f" length="460072" playlength="6" '
    'fromusername="wxid_a" md5="8dedf07ce5266eed" newmd5="abc89981ee881193" />\n'
    "<extcommoninfo><media_expire_at>1792898502</media_expire_at></extcommoninfo>\n</msg>"
)

REVOKE_XML = (
    '<?xml version="1.0"?><sysmsg type="revokemsg"><revokemsg>'
    "<content>\"甲\" 撤回了一条消息</content><revoketime>1791945886</revoketime>"
    "</revokemsg></sysmsg>"
)

GROUP_RENAME_XML = (
    '<sysmsg type="sysmsgtemplate"><sysmsgtemplate><content_template type="tmpl_type_profile">'
    "<plain><![CDATA[]]></plain>"
    '<template><![CDATA["$username$"修改群名为“$remark$”]]></template><link_list>'
    '<link name="username" type="link_profile"><memberlist><member>'
    "<username><![CDATA[wxid_a]]></username><nickname><![CDATA[甲]]></nickname>"
    "</member></memberlist></link>"
    '<link name="remark" type="link_plain"><plain><![CDATA[新群名]]></plain></link>'
    "</link_list></content_template></sysmsgtemplate></sysmsg>"
)

QUOTE_XML = (
    '<msg><appmsg><title>忘记说了——想要报名的私信我</title><type>57</type>'
    "<refermsg><type>1</type><svrid>1802656594381821731</svrid>"
    "<fromusr>123@chatroom</fromusr><chatusr>wxid_a</chatusr>"
    "<displayname>甲</displayname><content>一整段很长的通知正文</content>"
    "<createtime>1791470865</createtime></refermsg>"
    "</appmsg><fromusername>wxid_a</fromusername></msg>"
)

# 引用卡片/图片时，refermsg/content 是整段转义后的 XML，必须递归换成摘要
QUOTE_CARD_XML = (
    '<msg><appmsg><title>第一组填这个</title><type>57</type><refermsg>'
    "<type>49</type><svrid>3096977283805072190</svrid>"
    "<fromusr>wxid_a</fromusr><chatusr></chatusr><displayname>甲</displayname>"
    "<content>&lt;msg&gt;&lt;appmsg&gt;&lt;title&gt;第一组&lt;/title&gt;"
    "&lt;type&gt;33&lt;/type&gt;"
    "&lt;sourcedisplayname&gt;金山文档&lt;/sourcedisplayname&gt;"
    "&lt;/appmsg&gt;&lt;/msg&gt;</content>"
    "<createtime>1791468240</createtime></refermsg></appmsg></msg>"
)


def test_image_only_marked_as_image():
    """图片只认结构：XML 的 md5 不是磁盘文件名，故意不解析出来免得被误用。"""
    assert describe(IMAGE_XML) == {"kind": "image"}
    assert summarize(describe(IMAGE_XML)) == "[图片]"


def test_file_summary_has_name_and_size_not_raw_xml():
    desc = describe(FILE_XML)
    assert desc["kind"] == "file" and desc["ext"] == "docx"
    assert desc["bytes"] == 14129 and desc["name"] == "大赛报名表.docx"
    assert summarize(desc) == "[文件] 大赛报名表.docx · 13.8 KB"


def test_link_and_card_and_miniapp():
    link = describe(LINK_XML)
    assert link["kind"] == "link" and link["source"] == "某某之声"
    assert summarize(link) == "[链接] 古诗词音乐作品大赛的通知 · 某某之声"
    assert describe(LINK_EMPTY_WEAPPINFO_XML)["kind"] == "link"
    mini = describe(MINI_XML)
    assert mini["kind"] == "miniapp" and mini["appid"] == "wx36ddf"
    assert mini["page_path"] == "pages/detail.html?id=1"
    assert summarize(mini) == "[小程序] 近代史 · 接龙管家"


def test_pat_keeps_wechat_sentence_and_exposes_senders():
    desc = describe(PAT_XML)
    assert desc["kind"] == "pat"
    assert desc["from"] == "wxid_a" and desc["patted"] == "wxid_b"
    assert summarize(desc) == '"甲" 拍了拍 "乙" 说：“家里管饭吗”'


def test_merged_forward_second_layer_xml():
    desc = describe(MERGED_XML)
    assert desc["kind"] == "merged" and desc["count"] == 2
    assert [item["name"] for item in desc["items"]] == ["甲", "乙"]
    assert desc["items"][0]["text"] == "这个是文件说明"
    assert desc["items"][1]["text"] == "通知.pdf"
    assert summarize(desc) == "[聊天记录] 群聊的聊天记录 · 2 条"


def test_transfer_amount_and_ids():
    desc = describe(TRANSFER_XML)
    assert desc["amount"] == "￥8.00" and desc["sub_type"] == "3"
    assert summarize(desc) == "[转账] ￥8.00"
    # 单号与双方账号留在描述里，不进摘要
    assert (desc["transfer_id"], desc["transaction_id"]) == ("100005000120261009033",
                                                             "53010003396142")
    assert desc["payer_username"] == "wxid_a" and desc["receiver_username"] == "wxid_me"


def test_transfer_summary_names_the_counterpart():
    """措辞由用户定：发送者付钱写"向X"、收钱写"收到X"，方向和对象都在标签前面。"""
    desc = describe(TRANSFER_XML)
    desc["direction"], desc["target"] = "out", "影"
    assert summarize(desc) == "向影 [转账] ￥8.00"
    desc["direction"], desc["target"] = "in", "(=°Д°=)"
    assert summarize(desc) == "收到(=°Д°=) [转账] ￥8.00"


def test_emoji_and_video_fields():
    emoji = describe(EMOJI_XML)
    assert emoji["kind"] == "emoji" and emoji["bytes"] == 26287
    assert (emoji["width"], emoji["height"]) == (518, 474)
    assert summarize(emoji) == "[动画表情]"
    video = describe(VIDEO_XML)
    assert video["seconds"] == 6 and video["new_md5"] == "abc89981ee881193"
    assert summarize(video) == "[视频] 0:06"


def test_system_messages_render_as_sentences():
    revoke = describe(REVOKE_XML)
    assert revoke["kind"] == "recall" and revoke["at"] == 1791945886
    assert summarize(revoke) == '"甲" 撤回了一条消息'
    rename = describe(GROUP_RENAME_XML)
    assert rename["kind"] == "notice"
    # 引号是微信模板自己带的（"$username$"修改群名为…），不是我们加的
    assert summarize(rename) == '"甲"修改群名为“新群名”'


def test_quote_keeps_reply_text_and_locator_only():
    desc = describe(QUOTE_XML)
    assert desc["kind"] == "quote" and desc["text"] == "忘记说了——想要报名的私信我"
    quoted = desc["quoted"]
    assert quoted["svrid"] == "1802656594381821731"
    assert quoted["author"] == "wxid_a" and quoted["name"] == "甲"
    assert quoted["at"] == 1791470865 and quoted["type"] == 1
    assert quoted["label"] == "一整段很长的通知正文"
    # 正文只有回复那句话，被引全文不进正文
    assert summarize(desc) == "忘记说了——想要报名的私信我"


def test_quote_with_nested_xml_label_is_summarized_not_raw():
    quoted = describe(QUOTE_CARD_XML)["quoted"]
    assert "<" not in quoted["label"]
    assert quoted["label"] == "[小程序] 第一组 · 金山文档"
    # chatusr 为空时作者回落到 fromusr：微信自己这两个字段语义就不统一
    assert quoted["author"] == "wxid_a"


def test_quote_without_reply_text_falls_back_to_summary_line():
    empty = QUOTE_XML.replace("<title>忘记说了——想要报名的私信我</title>", "<title/>")
    assert summarize(describe(empty)) == "[引用] 甲：一整段很长的通知正文"


def test_quote_label_is_truncated():
    long = QUOTE_XML.replace("一整段很长的通知正文", "通知" * 60)
    label = describe(long)["quoted"]["label"]
    assert len(label) == 41 and label.endswith("…") and label.count("…") == 1


def test_text_broken_and_unknown_bodies_are_none():
    assert describe("晚上开会") is None
    assert describe("<msg><img broken") is None
    assert describe('<msg><unknownnode a="1"/></msg>') is None
    assert describe('<sysmsg type="somethingelse"><x/></sysmsg>') is None
    assert describe('<msg><voicemsg voicelength="1234"/></msg>') is None


def test_human_size_and_clock():
    assert human_size(14129) == "13.8 KB"
    assert human_size(76183) == "74.4 KB"
    assert human_size(5 * 1024 * 1024) == "5.0 MB"
    assert human_size(0) == "" and human_size(None) == ""
    assert clock(6) == "0:06" and clock(75) == "1:15" and clock(0) == ""
