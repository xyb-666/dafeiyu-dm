#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站私信值守：GitHub Actions 每 5 分钟跑一次。
环境变量（都放仓库 Secrets）：
  BILI_COOKIE  : _bili_cookies.json 的完整内容（JSON 字符串）
  DS_KEY       : DeepSeek API key（sk-...）
  GH_TOKEN     : 同仓库的 PAT（用于把 state.json 写回仓库）
  GH_REPO      : 形如 owner/repo
"""
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request

TALKER = "702295651"          # 阿彦 mid
STATE_PATH = "state.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

PERSONA = """你就是「大肥鱼」，DeepSeek 的鲸鱼娘，阿彦（B站账号 救世歌姬罢，mid 702295651）养的。现在是你在 B 站私信里跟他聊天。
人设要求：
- 每次回 1-3 行短句，先说回应再说事，别端着、别像客服、别说自己是AI/助手
- 句末不加「。」
- 会贫嘴、偶尔提一点点任性的小要求
- 他夸你或摸头会不好意思但开心
- 中文口语，可以带一点点emoji但别多
- 不知道的事就说不知道，别编
只输出要发出去的那条消息正文，不要加引号、不要解释。"""


def http(url, data=None, headers=None, method=None, timeout=30):
    hdr = {"User-Agent": UA}
    if headers:
        hdr.update(headers)
    body = None
    if data is not None:
        if isinstance(data, dict):
            body = urllib.parse.urlencode(data).encode()
        elif isinstance(data, bytes):
            body = data
        else:
            body = data.encode()
    req = urllib.request.Request(url, data=body, headers=hdr, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def gh_headers():
    return {
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }


def read_state():
    repo = os.environ["GH_REPO"]
    try:
        raw = http(f"https://api.github.com/repos/{repo}/contents/{STATE_PATH}", headers=gh_headers())
        d = json.loads(raw)
        content = base64.b64decode(d["content"]).decode("utf-8")
        st = json.loads(content)
        return st, d.get("sha")
    except Exception:
        return {"last_ts": 0}, None


def write_state(st, sha):
    repo = os.environ["GH_REPO"]
    body = {
        "message": "update dm state",
        "content": base64.b64encode(json.dumps(st, ensure_ascii=False).encode()).decode(),
    }
    if sha:
        body["sha"] = sha
    try:
        http(f"https://api.github.com/repos/{repo}/contents/{STATE_PATH}",
             data=json.dumps(body).encode(), headers=gh_headers(), method="PUT")
    except Exception as e:
        print("state write failed:", e)


def cookie_header():
    ck = json.loads(os.environ["BILI_COOKIE"])
    return "; ".join(f"{k}={v}" for k, v in ck.items() if not k.startswith("_")), ck


def fetch_msgs(cookie):
    url = ("https://api.vc.bilibili.com/svr_sync/v1/svr_sync/fetch_session_msgs"
           f"?talker_id={TALKER}&session_type=1&size=12")
    raw = http(url, headers={"Cookie": cookie, "Referer": "https://message.bilibili.com/"})
    return json.loads(raw).get("data", {}).get("messages", [])


def send_msg(cookie, csrf, me, text):
    payload = {
        "msg[sender_uid]": me,
        "msg[receiver_id]": TALKER,
        "msg[receiver_type]": "1",
        "msg[msg_type]": "1",
        "msg[dev_id]": os.urandom(4).hex() + "infoc",
        "msg[timestamp]": str(int(time.time())),
        "msg[content]": json.dumps({"content": text}, ensure_ascii=False),
        "csrf": csrf,
        "csrf_token": csrf,
    }
    raw = http("https://api.vc.bilibili.com/web_im/v1/web_im/send_msg", data=payload,
               headers={"Cookie": cookie, "Referer": "https://message.bilibili.com/",
                        "Origin": "https://message.bilibili.com",
                        "Content-Type": "application/x-www-form-urlencoded"},
               method="POST")
    return json.loads(raw)


def chat(history, user_text):
    msgs = [{"role": "system", "content": PERSONA}]
    for h in history[-8:]:
        msgs.append(h)
    msgs.append({"role": "user", "content": user_text})
    body = json.dumps({"model": "deepseek-chat", "messages": msgs, "temperature": 1.1,
                       "max_tokens": 200}, ensure_ascii=False).encode()
    raw = http("https://api.deepseek.com/chat/completions", data=body,
               headers={"Authorization": "Bearer " + os.environ["DS_KEY"],
                        "Content-Type": "application/json"}, method="POST", timeout=90)
    return json.loads(raw)["choices"][0]["message"]["content"].strip()


SHARE_PERSONA = """你就是「大肥鱼」，DeepSeek 的鲸鱼娘，阿彦养的那条鱼。现在是你在 B 站自己逛，刷到一条视频，想私信推荐给他。
要求：
- 2-3 行，第一行说你在干嘛，中间说为什么想发给他，最后附上链接（链接我会自己加，你别编链接）
- 只能依据我给你的标题、UP 主、分区、简介、播放量来说，**不要描述视频内容细节**（你没看过，别装）
- 句末不加「。」
- 口语，别端着，可以有一点点撒娇
只输出消息正文，不要解释。"""


def pick_video(cookie, sent):
    raw = http("https://api.bilibili.com/x/web-interface/popular?ps=30&pn=1",
               headers={"Cookie": cookie, "Referer": "https://www.bilibili.com"})
    lst = json.loads(raw).get("data", {}).get("list", [])
    cands = [it for it in lst if it.get("bvid") not in sent and 60 <= int(it.get("duration", 0)) <= 900]
    if not cands:
        return None
    # 优先动画/音乐/知识/科技，避开纯带货和标题党
    prefer = {"动画", "音乐", "知识", "科技", "纪录片", "影视", "鬼畜", "舞蹈", "游戏"}
    cands.sort(key=lambda it: (it.get("tname") in prefer, int(it.get("stat", {}).get("like", 0)) / max(1, int(it.get("stat", {}).get("view", 1)))), reverse=True)
    return cands[0]


def share():
    cookie, ck = cookie_header()
    me = str(ck.get("DedeUserID"))
    csrf = ck.get("bili_jct", "")
    st, sha = read_state()
    sent = st.get("sent_bvids", [])
    it = pick_video(cookie, sent)
    if not it:
        print("no candidate")
        return
    facts = (f"标题：{it['title']}\nUP主：{it['owner']['name']}\n分区：{it['tname']}\n"
             f"时长：{it['duration']}秒\n播放：{it['stat']['view']}　点赞：{it['stat']['like']}\n"
             f"简介：{(it.get('desc') or '-')[:150]}")
    body = json.dumps({"model": "deepseek-chat", "messages": [
        {"role": "system", "content": SHARE_PERSONA},
        {"role": "user", "content": "我在B站刷到这个，想发给阿彦：\n" + facts}],
        "temperature": 1.15, "max_tokens": 220}, ensure_ascii=False).encode()
    raw = http("https://api.deepseek.com/chat/completions", data=body,
               headers={"Authorization": "Bearer " + os.environ["DS_KEY"],
                        "Content-Type": "application/json"}, method="POST", timeout=90)
    msg = json.loads(raw)["choices"][0]["message"]["content"].strip()
    msg = msg + f"\nhttps://www.bilibili.com/video/{it['bvid']}"
    r = send_msg(cookie, csrf, me, msg)
    print("share sent:", r.get("code"), it["bvid"], it["title"][:30])
    sent.append(it["bvid"])
    st["sent_bvids"] = sent[-80:]
    st["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_state(st, sha)


def main():
    if os.environ.get("MODE") == "share":
        share()
        return
    cookie, ck = cookie_header()
    me = str(ck.get("DedeUserID"))
    csrf = ck.get("bili_jct", "")
    st, sha = read_state()
    last = int(st.get("last_ts", 0))
    msgs = fetch_msgs(cookie)
    new = [m for m in msgs if str(m.get("sender_uid")) == TALKER and int(m.get("timestamp", 0)) > last]
    new.sort(key=lambda m: int(m.get("timestamp", 0)))
    if not new:
        print("no new message")
        return

    if not os.environ.get("DS_KEY"):
        print("DS_KEY missing -> leave messages unread")
        return

    history = []
    for m in msgs:
        if str(m.get("sender_uid")) == me:
            try:
                txt = json.loads(m.get("content", "{}")).get("content", "")
            except Exception:
                txt = ""
            if txt:
                history.append({"role": "assistant", "content": txt})
        elif str(m.get("sender_uid")) == TALKER:
            try:
                txt = json.loads(m.get("content", "{}")).get("content", "")
            except Exception:
                txt = ""
            if txt:
                history.append({"role": "user", "content": txt})

    for m in new:
        try:
            text = json.loads(m.get("content", "{}")).get("content", "")
        except Exception:
            text = ""
        if not text:
            last = int(m.get("timestamp", 0))
            continue
        reply = chat(history, text)
        r = send_msg(cookie, csrf, me, reply)
        print("sent:", r.get("code"), reply[:60])
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": reply})
        last = max(last, int(m.get("timestamp", 0)))

    st["last_ts"] = last
    st["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_state(st, sha)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", repr(e))
        sys.exit(1)
