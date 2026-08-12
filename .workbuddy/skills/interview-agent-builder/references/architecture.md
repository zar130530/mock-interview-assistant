# 面试助手智能体 · 架构与契约参考

本文件描述重建/扩展面试助手时后端与前端的确切结构、数据模型与 API 契约。实现细节以本文件为准，避免与前端产生契约漂移。

## 1. 技术栈

- **后端**：Python 3.11+ · FastAPI · SQLite（单文件 `interview.db`）· OpenAI 兼容 LLM 客户端（同步 `openai` SDK 置于线程池执行）。
- **前端**：单一 `frontend/index.html`，原生 HTML/CSS/JS，无构建步骤，直接由后端 `GET /` 返回。
- **依赖**（`requirements.txt`）：`fastapi uvicorn openai pdfplumber python-docx python-multipart`。

## 2. 目录布局

```
interview-agent/
├── backend/
│   ├── app.py              # FastAPI 入口：路由 / SQLite / LLM 调用 / 友好报错 / 日志
│   ├── prompts.py          # 提示词与 user-prompt 构造（来自 skill assets/，勿改逻辑）
│   ├── rag.py              # 力扣题库 RAG 检索（来自 skill assets/）
│   ├── leetcode_bank.json  # 64 道真实力扣题（来自 skill assets/）
│   ├── logs/app.log        # RotatingFileHandler 运行日志（5MB×3）
│   └── interview.db        # SQLite 数据
└── frontend/
    └── index.html          # 单页应用
```

## 3. 数据模型（SQLite）

```sql
CREATE TABLE resumes (
  id INTEGER PRIMARY KEY,
  content_hash TEXT UNIQUE,   -- 去重键，sha256(raw_text)
  filename TEXT,
  raw_text TEXT,
  created_at TEXT
);

CREATE TABLE interviews (
  id INTEGER PRIMARY KEY,
  resume_id INTEGER,
  company TEXT,
  position TEXT,
  jd TEXT,
  knowledge_base TEXT,
  alignment TEXT,    -- JSON: [{requirement, resume_evidence, match, note}]
  questions TEXT,    -- JSON: [{id, category, question, answer, focus, scoring}]
  status TEXT,       -- 'pending' | 'interviewed'
  user_answers TEXT, -- JSON: {question_id: answer_text}
  created_at TEXT,
  updated_at TEXT
);
```

简历上传按 `content_hash` 去重：已存在则直接复用 `resume_id`（`reused:true`），避免重复解析与存储。

## 4. API 契约

所有请求/响应为 JSON（`application/json`），文件上传除外。LLM 相关接口在无 `api_key` 时走 Mock 回退；调用异常统一转 **502 + 友好中文**，并记录完整堆栈到 `logs/app.log`。

### 4.1 简历
- `POST /api/resume` — `multipart/form-data`，字段 `file`。
  - 返回：`{resume_id, filename, preview(前500字), reused:bool}`
- `GET /api/resume/latest` — 返回最近一条：`{resume_id, filename, preview}` 或 `null`。

### 4.2 面试 CRUD
- `POST /api/interviews` — body `InterviewCreate`：
  ```json
  {"resume_id":1, "company":"字节跳动", "jd":"...", "knowledge_base":"",
   "api_key":null, "base_url":null, "model":null}
  ```
  - 返回完整面试对象（见 4.5）。
- `GET /api/interviews` — 列表，每项：`{id, company, position, status, created_at, updated_at}`（不含 alignment/questions 大字段）。
- `GET /api/interviews/{iid}` — 完整对象，含 `alignment`/`questions` 已解析的 JSON 结构（后端以 TEXT 存储，接口层 `json.loads`）。
- `PUT /api/interviews/{iid}` — body `InterviewUpdate`：`{company?, status?, knowledge_base?}`。
- `DELETE /api/interviews/{iid}` — 204。

### 4.3 重新生成
- `POST /api/interviews/{iid}/regenerate` — body `RegenerateReq`：`{api_key?, base_url?, model?}`。
  - 复用原 `resume_id/company/jd/knowledge_base` 重新对齐+出题，覆盖 `position/alignment/questions`，`status` 重置 `pending`、`user_answers` 清空，保留 `id/created_at`。
  - 返回完整对象。

### 4.4 对话修改与口试
- `POST /api/interviews/{iid}/chat` — body `ChatReq`：`{instruction, api_key?, base_url?, model?}`。
  - 后端把当前 `alignment+questions` 与指令发给模型，模型只返回 diff（`action/added/updated/deleted/reply`）；后端 `_apply_diff` 合并后持久化。
  - 返回：`{questions, alignment, reply}`。
- `POST /api/interviews/{iid}/answer` — body `AnswerReq`：`{question_id, user_answer, api_key?, base_url?, model?}`。
  - 调用评分提示词，返回：`{grade:"优秀|合格|不合格", comment, improvement}`。

### 4.5 完整面试对象形状
```json
{
  "id": 12,
  "resume_id": 3,
  "company": "字节跳动",
  "position": "后端开发工程师",
  "jd": "...",
  "knowledge_base": "",
  "alignment": [{"requirement":"熟悉 Python","resume_evidence":"实习:...","match":"strong","note":"..."}],
  "questions": [
    {"id":1,"category":"基础技术","question":"...","answer":"...","focus":"...","scoring":"..."},
    {"id":10,"category":"纯算法","question":"力扣第146题 · LRU缓存 ...","answer":"...","focus":"...","scoring":"..."}
  ],
  "status": "pending",
  "user_answers": {},
  "created_at": "2026-08-12T...",
  "updated_at": "2026-08-12T..."
}
```

## 5. LLM 层模式（app.py）

- 客户端：OpenAI 兼容，`base_url/api_key/model` 可每次请求覆盖（前端设置 / 请求体传入），缺省读后端环境变量。
- **JSON 模式 + 回退**：优先 `response_format={"type":"json_object"}`；非 JSON 返回时先记日志再尝试从文本抽取首个 JSON 块。
- **阻塞隔离**：`run_blocking(fn, *args, timeout)` = `asyncio.to_thread` + `asyncio.wait_for`。解析超时 30s，模型调用超时 120s；超时抛 504。
- **Mock 回退**：无 `api_key` 时 `mock_generate()` 返回约 10 题结构化数据（4 基础 / 3 深挖 / 2 素质 / 1 纯算法），第 10 题直接采用力扣题库真实题（如 146 LRU），保证整条流程可离线演示。
- **友好报错**：`llm_error_detail(e)` 把异常翻译成中文（连接超时/Key 无效/模型不存在/429 限流），三处 LLM 调用均 `try/except → 502`；全局 `@app.exception_handler(Exception)` 兜底裸 500。

## 6. RAG（rag.py + leetcode_bank.json）

- `search_questions(jd, resume_text="", k=6)`：从 JD+简历抽取技术关键词 → 经 `_KW_TAGS`/`_ROLE_TAGS` 展开为力扣标签 → 对题库逐题打分（标签命中 +3，标题关键词 +5，描述 +1，JD 明示难度加权）→ 降序返回前 k 道。**全部来自题库，杜绝编造**。
- 无关键词命中 → 兜底返回经典高频题 `[1,15,3,206,146,53,102,215,300,322]`。
- 题库 schema：`{no, title, difficulty("简单|中等|困难"), tags:[...], desc, examples, hint}`。
- `prompts.py` 的 `build_interview_user_prompt(..., retrieved)` 把检索结果注入"# 力扣题库（RAG 检索候选）"段，并强制最后一道纯算法题从中选题。

## 7. 前端约定（frontend/index.html）

- 单一文件，原生 JS。关键函数（保持命名稳定以便维护）：`apiSettings, fetchTO, errDetail, toast, busyBtn, refreshKeyMask, renderPresets, llmServiceName, syncMockHint, showSavedResume, loadLatestResume, setStep, loadList, mkBtn, mkConfirmBtn, openDetail, renderDetail, toggleAnswer, submitAnswer, openEdit, download, esc, openModal, closeModal, themeInit, renderChat, renderList, skeletonRows, emptyState, catBadge`。
- **契约**：所有 API 错误用 `errDetail()` 解析后端 JSON 的 `detail` 字段并 Toast 展示；`fetchTO` 用 `AbortController` 超时。
- **坑点**：iframe 预览沙箱会静默拦截原生 `confirm()` → 删除按钮用 `mkConfirmBtn` 二次点击确认；重新生成按钮用 `mkBtn` 单击直接执行。API Key 仅存 `localStorage` 并打码。
- 题型分类彩色徽章：`基础=brand / 深挖=info / 素质=warn / 纯算法=ok`。

## 8. 运行命令

```
# 用已安装依赖的 Python 3.10+ 运行（建议 virtualenv）
cd interview-agent/backend
python app.py        # 或 .venv/Scripts/python app.py（Windows）、.venv/bin/python（macOS/Linux）
# 服务 http://127.0.0.1:8000 ，GET / 返回前端
```

## 9. 已知坑点清单（务必遵守）

1. 禁用原生 `confirm()`（iframe 沙箱静默拦截）→ 删除用二次点击确认。
2. 重新生成按钮必须单击直接执行，勿加二次确认。
3. 启动后端必须用 venv Python。
4. DeepSeek：`base_url=https://api.deepseek.com`，`model=deepseek-v4-flash|deepseek-v4-pro`（旧名已弃用）。
5. LLM 失败必须 502+友好中文+记日志，且不破坏已保存数据读取。
6. API Key 粘贴后打码，仅存 localStorage。
7. 力扣算法题严禁模型编造，必须来自 `leetcode_bank.json` 检索候选。
