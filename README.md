# 面试助手智能体 · Mock Interview Assistant

一个面向「已通过简历初筛、进入面试阶段」求职者的模拟面试工具。上传简历 + 粘贴目标公司 JD，智能体自动完成：

- **技能对齐**：把简历技能与公司要求做匹配度分析（strong / partial / missing）。
- **高仿真出题**：以「该公司面试官」的口吻生成约 10 道面试题 —— 基础(4) / 项目深挖(3) / 综合素质(2) / 纯算法(1)。
- **标准回答**：每题附 150~300 字、可直接背诵的参考回答，项目/实习题强制引用简历真实信息（项目名、技术栈、量化数据），绝不编造。
- **算法题 RAG**：最后一道纯算法题从内置 64 道**真实力扣题库**中按 JD 关键词检索召回，严禁模型即兴编造。
- **对话式修改**：用自然语言追加 / 改写 / 删除题目，或作答后由模型评分点评，形成「出题—练习—反馈」闭环。
- **口试自测**：每题可自测作答，模型给出「优秀 / 合格 / 不合格」评级与改进建议。

## 技术架构

| 层 | 技术 |
|---|---|
| 后端 | Python · FastAPI · SQLite（无外部依赖，单进程） |
| 前端 | 单文件 `index.html`（原生 JS，零构建） |
| 大模型 | OpenAI 兼容接口（`base_url` / `api_key` / `model` 可配置）；**无 Key 时自动 Mock 回退**，完整链路离线可跑 |
| 检索 | `rag.py` 关键词→算法标签映射打分，从 `leetcode_bank.json` 召回真实题目 |
| 工程化 | 异步线程池 + 硬超时；统一异常 → 502 友好中文 + 持久化日志；前端响应式 + 骨架屏 + 暗色主题 |

## 快速开始

```bash
cd interview-agent/backend
python -m venv .venv && .venv\Scripts\python -m pip install -r requirements.txt
python app.py          # 默认 http://127.0.0.1:8000 ，GET / 返回前端
```

浏览器打开 `http://127.0.0.1:8000` 即可使用。可选：在页面「设置」中填入 OpenAI 兼容接口的 `base_url` / `api_key` / `model` 以启用真实大模型。

> DeepSeek 示例：`base_url=https://api.deepseek.com`，`model=deepseek-v4-flash`（或 `deepseek-v4-pro`）。

## 目录结构

```
interview-agent/
├─ backend/
│  ├─ app.py                 # FastAPI 路由、SQLite、超时/异常处理
│  ├─ prompts.py             # 面试官/改写/评分提示词（参考 assets/）
│  ├─ rag.py                 # 力扣题库 RAG 检索
│  ├─ leetcode_bank.json     # 64 道真实力扣题知识库
│  ├─ llm.py                 # OpenAI 兼容 LLM 层 + Mock 回退
│  ├─ resume_parser.py       # 简历解析（PDF/Word/文本）
│  └─ smoke_test.py          # 10 项冒烟测试
└─ frontend/
   └─ index.html             # 单页前端

docs/                        # 提示词设计、UI 评审、简历素材
.workbuddy/skills/          # 项目级 Skill：interview-agent-builder
```

## 项目级 Skill

本仓库附带 [`interview-agent-builder`](.workbuddy/skills/interview-agent-builder) 项目级 Skill，封装了已验证的架构契约与面试出题领域知识（题型配比、参考回答规范、算法 RAG 规则、口试评分体系）。在 WorkBuddy 中打开本仓库即可自动加载，用于重建或扩展该应用。

## 许可证

MIT
