"""冒烟测试：验证各接口在 Mock 模式下可用。"""
import urllib.request, urllib.error, json, time, os, io

BASE = os.getenv("BASE", "http://127.0.0.1:8000")


def req(method, path, data=None, headers=None, raw=None):
    url = BASE + path
    if raw is not None:
        body = raw
    elif data is not None:
        body = json.dumps(data).encode()
        headers = {**(headers or {}), "Content-Type": "application/json"}
    else:
        body = None
    r = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def wait_up():
    for _ in range(40):
        try:
            s, _ = req("GET", "/")
            if s == 200:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def main():
    assert wait_up(), "服务未启动"
    print("[1] GET / -> ", req("GET", "/")[0])

    # 上传简历(txt)
    boundary = "----xb"
    resume = "熟悉 Python/FastAPI，实习于某电商后端，用 Redis 做缓存。项目：订单系统。"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"r.txt\"\r\n"
        f"Content-Type: text/plain\r\n\r\n{resume}\r\n--{boundary}--\r\n"
    ).encode()
    s, txt = req("POST", "/api/resume", raw=body,
                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    rid = json.loads(txt)["resume_id"]
    print("[2] POST /api/resume ->", s, "resume_id=", rid)

    # 复用（同内容应 reused=True）
    s, txt = req("POST", "/api/resume", raw=body,
                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    print("[3] 重复上传 reused=", json.loads(txt).get("reused"))

    # 生成面试
    jd = "岗位要求：熟悉 Python、FastAPI、Redis；有高并发经验优先。"
    s, txt = req("POST", "/api/interviews",
                 data={"resume_id": rid, "company": "测试公司", "jd": jd, "knowledge_base": "Go+K8s"})
    inter = json.loads(txt)
    iid = inter["id"]
    print("[4] POST /api/interviews ->", s, "questions=", len(inter["questions"]),
          "alignment=", len(inter["alignment"]))

    # 列表
    s, txt = req("GET", "/api/interviews")
    print("[5] GET /api/interviews ->", s, "count=", len(json.loads(txt)))

    # 详情
    s, txt = req("GET", f"/api/interviews/{iid}")
    print("[6] GET /api/interviews/{id} ->", s, "fields ok=",
          all(k in json.loads(txt) for k in ("questions", "alignment", "user_answers")))

    # 对话修改：追加题
    s, txt = req("POST", f"/api/interviews/{iid}/chat",
                 data={"instruction": "追加一道关于 Redis 缓存击穿的题"})
    ch = json.loads(txt)
    print("[7] POST chat(追加) ->", s, "now questions=", len(ch["questions"]), "reply=", ch.get("reply"))

    # 口试评分
    s, txt = req("POST", f"/api/interviews/{iid}/answer",
                 data={"question_id": 1, "user_answer": "我会用互斥锁+逻辑过期防止击穿。"})
    print("[8] POST answer ->", s, "grade=", json.loads(txt).get("grade"))

    # 标记已面试
    s, _ = req("PUT", f"/api/interviews/{iid}", data={"status": "interviewed"})
    st = json.loads(req("GET", f"/api/interviews/{iid}")[1])["status"]
    print("[9] PUT status ->", s, "status=", st)

    # 删除
    s, _ = req("DELETE", f"/api/interviews/{iid}")
    print("[10] DELETE ->", s)

    print("\nALL SMOKE TESTS PASSED" if s == 200 else "SOME FAILED")


if __name__ == "__main__":
    main()
