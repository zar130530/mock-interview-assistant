"""面试助手智能体 · 后端 API（FastAPI）。"""
import os
import json
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from db import init_db, get_conn, now_iso
from resume_parser import parse_resume, content_hash
from llm import generate_interview, modify_interview, grade_answer

BASE = Path(__file__).resolve().parent
FRONTEND = BASE.parent / "frontend"

init_db()

app = FastAPI(title="面试助手智能体")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------- 运行日志（持久化到 logs/app.log） -----------------------------
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"

logger = logging.getLogger("interview-agent")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
_fh.setFormatter(_fmt)
logger.addHandler(_fh)
_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)
logger.addHandler(_sh)

logger.info("=" * 60)
logger.info("服务启动（app.log 已开启，日志文件：%s）", LOG_FILE)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info("REQ %s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("REQ FAIL %s %s", request.method, request.url.path)
        raise
    logger.info("RES %s %s -> %s", request.method, request.url.path, response.status_code)
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("未捕获异常 %s %s：%s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": f"服务器内部错误：{type(exc).__name__}: {exc}"})


def llm_error_detail(e: Exception) -> str:
    """把 LLM 调用异常翻译成对用户友好的提示，便于前端直接展示。"""
    name = type(e).__name__
    msg = str(e) or name
    low = msg.lower()
    if "connection" in low or "connecttimeout" in low or "timed out" in low or "dns" in low or "network" in low:
        hint = "无法连接模型服务：请检查 base_url 是否正确、目标地址当前是否可达（网络 / 代理 / 防火墙）。"
    elif "401" in msg or "authentication" in low or "api key" in low or "invalid" in low or "apikey" in low:
        hint = "API Key 无效或鉴权失败，请检查 Key 是否正确、是否过期。"
    elif "404" in msg or ("model" in low and "not found" in low):
        hint = "模型名或接口路径不存在：请检查 base_url 与 model 配置是否匹配服务商。"
    elif "429" in msg or "rate limit" in low:
        hint = "请求频率超限（429）：请稍后重试，或降低并发。"
    else:
        hint = "模型调用失败。"
    return f"{hint} 错误详情：{name}: {msg[:300]}"


# 阻塞调用（PDF 解析 / LLM）放进线程池执行并加硬超时，
# 避免同步调用占满事件循环导致整个应用“卡死”。
async def run_blocking(fn, *args, timeout: int = 120):
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(None, fn, *args), timeout)
    except asyncio.TimeoutError:
        raise HTTPException(504, "处理超时：简历解析或模型调用耗时过长，请重试或更换文件/模型配置")


# ----------------------------- 请求模型 -----------------------------
class InterviewCreate(BaseModel):
    resume_id: int
    company: str
    jd: str
    knowledge_base: str = ""
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None


class InterviewUpdate(BaseModel):
    company: str | None = None
    status: str | None = None          # pending | interviewed
    knowledge_base: str | None = None


class ChatReq(BaseModel):
    instruction: str
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None


class AnswerReq(BaseModel):
    question_id: int
    user_answer: str
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None


class RegenerateReq(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None


# ----------------------------- 工具 -----------------------------
def _row_to_dict(row):
    return dict(row)


def _apply_diff(questions, diff):
    qs = [dict(q) for q in questions]
    # 支持单 action 或 mixed；允许 deleted/updated/added 同时存在
    for did in diff.get("deleted") or []:
        qs = [q for q in qs if q["id"] != did]
    for u in diff.get("updated") or []:
        for q in qs:
            if q["id"] == u.get("id"):
                q.update({k: v for k, v in u.get("patch", {}).items() if k != "id"})
    # 先按可能已有的 id 排序，再统一重排，保证最终顺序与 LLM 返回顺序一致
    added_raw = diff.get("added") or []
    for a in added_raw:
        na = dict(a)
        na.pop("id", None)
        na["id"] = max([q["id"] for q in qs], default=0) + 1
        qs.append(na)
    return qs


# ----------------------------- 简历 -----------------------------
@app.post("/api/resume")
async def upload_resume(file: UploadFile = File(...)):
    data = await file.read()
    try:
        text = await run_blocking(parse_resume, data, file.filename or "resume.txt", timeout=30)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"简历解析失败：{e}")
    h = content_hash(text)
    conn = get_conn()
    row = conn.execute("SELECT * FROM resumes WHERE content_hash=?", (h,)).fetchone()
    if row:
        conn.close()
        return {"resume_id": row["id"], "filename": row["filename"],
                "preview": text[:500], "reused": True}
    cur = conn.execute(
        "INSERT INTO resumes (content_hash, filename, raw_text, created_at) VALUES (?,?,?,?)",
        (h, file.filename, text, now_iso()),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return {"resume_id": rid, "filename": file.filename, "preview": text[:500], "reused": False}


@app.get("/api/resume/latest")
def latest_resume():
    conn = get_conn()
    row = conn.execute("SELECT * FROM resumes ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if not row:
        return None
    return {"resume_id": row["id"], "filename": row["filename"], "preview": row["raw_text"][:500]}


# ----------------------------- 面试 -----------------------------
@app.post("/api/interviews")
async def create_interview(body: InterviewCreate):
    conn = get_conn()
    res = conn.execute("SELECT raw_text FROM resumes WHERE id=?", (body.resume_id,)).fetchone()
    if not res:
        conn.close()
        raise HTTPException(404, "简历不存在，请先上传")
    try:
        result = await run_blocking(generate_interview, res["raw_text"], body.jd, body.company,
                                    body.knowledge_base, body.api_key, body.base_url, body.model, timeout=120)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("生成模拟面试失败：company=%s, base_url=%s, model=%s",
                         body.company, body.base_url, body.model)
        raise HTTPException(502, llm_error_detail(e))
    ts = now_iso()
    cur = conn.execute(
        """INSERT INTO interviews
           (resume_id, company, position, jd, knowledge_base, alignment, questions, status, user_answers, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (body.resume_id, body.company, result.get("position", ""),
         body.jd, body.knowledge_base,
         json.dumps(result.get("alignment", []), ensure_ascii=False),
         json.dumps(result.get("questions", []), ensure_ascii=False),
         "pending", "{}", ts, ts),
    )
    conn.commit()
    iid = cur.lastrowid
    conn.close()
    return get_interview_obj(iid)


@app.get("/api/interviews")
def list_interviews():
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, company, position, status, created_at, "
        "(SELECT COUNT(*) FROM json_each(questions)) AS qcount "
        "FROM interviews ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


@app.get("/api/interviews/{iid}")
def get_interview_obj(iid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM interviews WHERE id=?", (iid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "面试记录不存在")
    d = _row_to_dict(row)
    d["alignment"] = json.loads(d["alignment"] or "[]")
    d["questions"] = json.loads(d["questions"] or "[]")
    d["user_answers"] = json.loads(d["user_answers"] or "{}")
    return d


@app.put("/api/interviews/{iid}")
def update_interview(iid: int, body: InterviewUpdate):
    conn = get_conn()
    row = conn.execute("SELECT * FROM interviews WHERE id=?", (iid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "面试记录不存在")
    fields, vals = [], []
    if body.company is not None:
        fields.append("company=?"); vals.append(body.company)
    if body.status is not None:
        fields.append("status=?"); vals.append(body.status)
    if body.knowledge_base is not None:
        fields.append("knowledge_base=?"); vals.append(body.knowledge_base)
    fields.append("updated_at=?"); vals.append(now_iso())
    conn.execute(f"UPDATE interviews SET {','.join(fields)} WHERE id=?", vals + [iid])
    conn.commit()
    conn.close()
    return get_interview_obj(iid)


@app.delete("/api/interviews/{iid}")
def delete_interview(iid: int):
    conn = get_conn()
    conn.execute("DELETE FROM interviews WHERE id=?", (iid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/interviews/{iid}/regenerate")
async def regenerate_interview(iid: int, body: RegenerateReq):
    """针对该公司重新生成模拟面试：复用原 JD / 知识库，重新对齐并出题，覆盖原记录。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM interviews WHERE id=?", (iid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "面试记录不存在")
    res = conn.execute("SELECT raw_text FROM resumes WHERE id=?", (row["resume_id"],)).fetchone()
    if not res:
        conn.close()
        raise HTTPException(404, "关联简历不存在，请先上传简历")
    try:
        result = await run_blocking(
            generate_interview, res["raw_text"], row["jd"], row["company"],
            row["knowledge_base"] or "", body.api_key, body.base_url, body.model, timeout=120)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("重新生成模拟面试失败：id=%s, company=%s, base_url=%s, model=%s",
                         iid, row["company"], body.base_url, body.model)
        raise HTTPException(502, llm_error_detail(e))
    ts = now_iso()
    conn.execute(
        """UPDATE interviews
           SET position=?, alignment=?, questions=?, status=?, user_answers=?, updated_at=?
           WHERE id=?""",
        (result.get("position", ""),
         json.dumps(result.get("alignment", []), ensure_ascii=False),
         json.dumps(result.get("questions", []), ensure_ascii=False),
         "pending", "{}", ts, iid),
    )
    conn.commit()
    conn.close()
    return get_interview_obj(iid)


@app.post("/api/interviews/{iid}/chat")
async def chat_modify(iid: int, body: ChatReq):
    conn = get_conn()
    row = conn.execute("SELECT * FROM interviews WHERE id=?", (iid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "面试记录不存在")
    # 联表取简历原文，让修改/追加题有完整上下文
    resume_text = ""
    if row["resume_id"]:
        rrow = conn.execute("SELECT raw_text FROM resumes WHERE id=?", (row["resume_id"],)).fetchone()
        if rrow:
            resume_text = rrow["raw_text"] or ""
    current = {
        "company": row["company"],
        "jd": row["jd"] or "",
        "knowledge_base": row["knowledge_base"] or "",
        "resume_text": resume_text,
        "alignment": json.loads(row["alignment"] or "[]"),
        "questions": json.loads(row["questions"] or "[]"),
    }
    try:
        diff = await run_blocking(modify_interview, current, body.instruction, body.api_key, body.base_url, body.model, timeout=120)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("对话修改失败：iid=%s, base_url=%s, model=%s", iid, body.base_url, body.model)
        raise HTTPException(502, llm_error_detail(e))
    new_questions = _apply_diff(current["questions"], diff)
    new_alignment = diff.get("alignment") or current["alignment"]
    conn.execute(
        "UPDATE interviews SET questions=?, alignment=?, updated_at=? WHERE id=?",
        (json.dumps(new_questions, ensure_ascii=False),
         json.dumps(new_alignment, ensure_ascii=False), now_iso(), iid),
    )
    conn.commit()
    conn.close()
    return {"questions": new_questions, "alignment": new_alignment, "reply": diff.get("reply", "")}


@app.post("/api/interviews/{iid}/answer")
async def answer_question(iid: int, body: AnswerReq):
    conn = get_conn()
    row = conn.execute("SELECT * FROM interviews WHERE id=?", (iid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "面试记录不存在")
    questions = json.loads(row["questions"] or "[]")
    q = next((x for x in questions if x["id"] == body.question_id), None)
    if not q:
        conn.close()
        raise HTTPException(404, "题目不存在")
    try:
        feedback = await run_blocking(grade_answer, q, body.user_answer, body.api_key, body.base_url, body.model, timeout=120)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("口试评分失败：iid=%s, question_id=%s, base_url=%s, model=%s",
                         iid, body.question_id, body.base_url, body.model)
        raise HTTPException(502, llm_error_detail(e))
    answers = json.loads(row["user_answers"] or "{}")
    answers[str(body.question_id)] = {"answer": body.user_answer, "feedback": feedback}
    conn.execute("UPDATE interviews SET user_answers=?, updated_at=? WHERE id=?",
                 (json.dumps(answers, ensure_ascii=False), now_iso(), iid))
    conn.commit()
    conn.close()
    return feedback


# ----------------------------- 静态页 -----------------------------
@app.get("/")
def index():
    return FileResponse(str(FRONTEND / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
