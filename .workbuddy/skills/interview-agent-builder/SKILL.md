---
name: interview-agent-builder
description: This skill should be used when building, rebuilding, or extending the "面试助手智能体" (Mock Interview Assistant) — a tool that takes a candidate's resume plus a target job description (JD), aligns skills, and generates a realistic technical interview (questions + reference answers + scoring) in the voice of that company's interviewer. Trigger on requests like "做一个面试助手", "生成模拟面试", "面试陪练", "基于简历和JD出题", or any task to scaffold/rebuild a FastAPI+SQLite single-HTML app with an OpenAI-compatible LLM layer, RAG over a LeetCode bank, mock fallback, and friendly error handling.
agent_created: true
---

# 面试助手智能体（Interview Agent Builder）

## Overview

This skill packages the proven architecture and interview-domain knowledge of the 面试助手智能体: a web app where a user uploads a resume, pastes a target company's JD, and gets a high-fidelity mock interview — skill alignment, ~10 questions (basic / project-deep-dive / behavioral / pure-algorithm), each with a背诵-ready reference answer, plus conversational editing and oral self-test grading. Invoke it to scaffold the app from scratch, rebuild it after changes, or extend it (new question types, more RAG sources, export formats, etc.).

The crown jewels are: (1) the **prompt design** (question mix, answer spec, algorithm-RAG rule) in `references/prompt-design.md`, and (2) the **copy-paste backend modules** in `assets/` (`prompts.py`, `rag.py`, `leetcode_bank.json`). Read the reference before touching prompts; copy the assets into the backend rather than rewriting them.

## Architecture at a glance

- **Backend**: Python FastAPI + SQLite, single process. LLM via OpenAI-compatible client (`base_url`/`api_key`/`model` overridable per-request). No key → deterministic **Mock fallback** so the full flow works offline.
- **Frontend**: a single `index.html` (vanilla JS, no build). Talks to the backend over REST. API key lives only in `localStorage`, masked after entry.
- **RAG**: `rag.py` retrieves real LeetCode problems from `leetcode_bank.json` by mapping JD/resume keywords → tags → scored ranking. The last question is forced to be a real LeetCode problem — **never let the model invent algorithm problems**.
- **Resilience**: every LLM call wrapped in try/except → HTTP 502 with a friendly Chinese message + full stack trace logged to `backend/logs/app.log`. Long calls run in a thread pool with a timeout (504 on exceed).

Full file layout, data model, and API contract are in `references/architecture.md`.

## When to use

- "帮我做一个面试助手 / 模拟面试工具"
- "基于我的简历和这家公司的 JD 出一套面试题"
- "给面试助手加 XXX 功能"（口试评分、导出、更多题库、对话改写…）
- "重建 / 重启 / 重构面试助手前端或后端"
- Any task that needs: resume parsing, JD alignment, LLM question generation with structured JSON, or a LeetCode-RAG algorithm question.

## Rebuild workflow (from scratch)

1. **Scaffold backend** at `backend/`:
   - `requirements.txt`: `fastapi uvicorn openai pdfplumber python-docx python-multipart`.
   - Copy `assets/prompts.py`, `assets/rag.py`, `assets/leetcode_bank.json` verbatim into `backend/`.
   - Write `app.py` following `references/architecture.md` (routes, SQLite schema, `run_blocking` timeout wrapper, `_apply_diff` for chat edits, friendly 502 handler). Keep all route paths exactly as documented so the frontend contract stays intact.
2. **Scaffold frontend** `frontend/index.html` as a single page: resume upload + JD textarea + company field → `POST /api/resume` then `POST /api/interviews`; a saved-list view (CRUD: view / delete-with-double-confirm / mark-interviewed / regenerate); a detail view rendering alignment + question cards (category badge, collapsible answer, oral self-test panel); a settings modal for LLM key. Honor the pitfalls below.
3. **Run** with a Python 3.10+ interpreter that has the dependencies from `requirements.txt` installed (e.g. a virtualenv: `python -m venv .venv && .venv\Scripts\python -m pip install -r requirements.txt` on Windows, or the equivalent on macOS/Linux):
   `python app.py` from `backend/`. Serve on `http://127.0.0.1:8000`. Open it in the built-in browser via `present_files` (in WorkBuddy) or any browser.
4. **Smoke test**: generate a mock interview with no key (Mock path), then with a key if available. Verify JSON shape, alignment, ~10 questions with the algorithm question drawn from the bank.

## Extend workflow

- **New question type / stricter answers**: edit the system prompt in `assets/prompts.py` (and mirror the rationale in `references/prompt-design.md`). Never make the model fabricate algorithm problems — keep the RAG rule.
- **More RAG coverage**: add entries to `assets/leetcode_bank.json` (schema: `no,title,difficulty,tags,desc,examples,hint`) and/or extend the `_KW_TAGS`/`_ROLE_TAGS` maps in `assets/rag.py`.
- **Frontend tweak**: edit `frontend/index.html` only — it is a static file, changes are live on reload, no backend restart needed.
- After any change, re-run the smoke test and re-validate against `references/architecture.md`.

## Critical pitfalls (learned the hard way)

- **Never use native `confirm()` in the iframe preview** — the sandbox silently swallows it, making delete buttons "do nothing". Use a double-click-to-confirm pattern (`mkConfirmBtn`).
- **Regenerate button must be single-click** (no double confirm) — wrapping it in confirm makes users think it's broken.
- **Always start the server with the venv Python**; the system Python lacks `fastapi`.
- **DeepSeek config**: `base_url = https://api.deepseek.com`, `model = deepseek-v4-flash` / `deepseek-v4-pro`. The old names `deepseek-chat` / `deepseek-reasoner` are deprecated (2026-07-24).
- **LLM failures must degrade gracefully**: return 502 + friendly Chinese, log the trace, and never break already-saved data reads.
- **Mask the API key** in the UI after paste; persist only in `localStorage`.

## Resources

### references/
- `architecture.md` — directory layout, SQLite schema (`resumes`, `interviews`), full API contract (every route + request/response shape), LLM layer pattern, RAG design, frontend contract, run command, pitfall list.
- `prompt-design.md` — the interview prompt spec: system prompt, question mix (4 basic / 3 deep-dive / 2 behavioral / 1 pure-algorithm), reference-answer writing rules, algorithm-RAG rule, alignment spec, conversational-edit diff protocol, oral grading, JSON schema, safety boundaries.

### assets/
- `prompts.py` — proven interviewer/modify/grade prompts + user-prompt builders. Copy into backend; do not rewrite.
- `rag.py` — LeetCode RAG retriever (keyword→tag→scored ranking). Copy into backend.
- `leetcode_bank.json` — 64 real LeetCode problems used as the algorithm-question knowledge base. Copy into backend.

### scripts/
- (none required; the assets above are copied directly)
