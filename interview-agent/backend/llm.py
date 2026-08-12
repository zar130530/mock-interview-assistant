"""LLM 调用层：OpenAI 兼容接口（GPT / DeepSeek / 通义 / 本地 Ollama）。
前端可传入 api_key/base_url/model，优先级高于环境变量；无 key 时回退 Mock。
"""
import os
import json
import logging
from openai import OpenAI

from prompts import (
    INTERVIEWER_SYSTEM_PROMPT,
    MODIFY_SYSTEM_PROMPT,
    build_interview_user_prompt,
    build_modify_user_prompt,
    build_grade_user_prompt,
)
from rag import search_questions

logger = logging.getLogger("interview-agent.llm")

DEFAULT_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
DEFAULT_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
DEFAULT_API_KEY = os.getenv("LLM_API_KEY", "")
MOCK = os.getenv("MOCK", "0") == "1"


def _resolve(api_key, base_url, model):
    return (
        api_key or DEFAULT_API_KEY,
        base_url or DEFAULT_BASE_URL,
        model or DEFAULT_MODEL,
    )


def _client(api_key, base_url):
    return OpenAI(api_key=api_key or "EMPTY", base_url=base_url)


def _extract_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1:
        text = text[s : e + 1]
    return json.loads(text)


def _chat(client, model, system, user, temperature=0.7):
    """优先 json 模式，失败则回退普通模式并解析。两次都失败则抛出异常。"""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=temperature,
        )
        return _extract_json(resp.choices[0].message.content)
    except Exception as e:
        logger.warning("JSON 模式调用失败，回退普通模式：%s: %s（base_url=%s, model=%s）",
                       type(e).__name__, str(e)[:300], getattr(client, "base_url", "?"), model)
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    return _extract_json(resp.choices[0].message.content)


def generate_interview(resume_text, jd, company, knowledge_base="", api_key=None, base_url=None, model=None):
    api_key, base_url, model = _resolve(api_key, base_url, model)
    # RAG 检索：从力扣题库中召回真实候选题目，注入上下文，杜绝模型编造算法题
    retrieved = search_questions(jd, resume_text, k=6)
    if MOCK or not api_key:
        return mock_generate(resume_text, jd, company, knowledge_base, retrieved)
    client = _client(api_key, base_url)
    user = build_interview_user_prompt(resume_text, jd, company, knowledge_base, retrieved)
    return _chat(client, model, INTERVIEWER_SYSTEM_PROMPT, user)


def modify_interview(current_state, instruction, api_key=None, base_url=None, model=None):
    api_key, base_url, model = _resolve(api_key, base_url, model)
    if MOCK or not api_key:
        return mock_modify(current_state, instruction)
    client = _client(api_key, base_url)
    user = build_modify_user_prompt(current_state, instruction)
    return _chat(client, model, MODIFY_SYSTEM_PROMPT, user)


def grade_answer(question, user_answer, api_key=None, base_url=None, model=None):
    api_key, base_url, model = _resolve(api_key, base_url, model)
    if MOCK or not api_key:
        return {
            "grade": "合格",
            "comment": "（Mock 模式）你的回答结构清晰，覆盖了主要要点。接入真实模型后可获得针对性点评。",
            "improvement": "建议补充具体项目数据与量化结果，并用 STAR 法组织表述。",
        }
    client = _client(api_key, base_url)
    user = build_grade_user_prompt(question, user_answer)
    return _chat(client, model, "你是严格但友善的技术面试官，只输出 JSON。", user, temperature=0.5)


# ------------------------- Mock 回退（无密钥跑通流程用） -------------------------
def _pick_resume_bits(resume_text, max_lines=3, max_chars=300):
    """从简历中抽取与项目/实习相关的原文片段，用于 Mock 答案引用简历证据。
    优先抽实习/项目经历行（含项目名/量化指标），其次才是技能与课程行。"""
    lines = [l.strip() for l in resume_text.splitlines() if l.strip()]
    hi = ("实习", "项目", "负责", "参与", "实现", "开发", "优化", "系统", "平台", "接口", "模块", "实训")
    mid = ("QPS", "并发", "耗时", "架构", "性能", "部署", "上线", "引擎", "识别", "抽取")
    skip = ("主修课程", "课程", "教育背景", "个人信息", "联系方式", "毕业院校", "住址")
    cand = [l for l in lines if len(l) >= 8 and not any(s in l for s in skip)]
    hi_hits = [l for l in cand if any(k in l for k in hi)]
    if not hi_hits:
        mid_hits = [l for l in cand if any(k in l for k in mid)]
        pool = mid_hits or [l for l in cand][:1]
    else:
        # 命中强关键词越多的行越像经历正文，优先取（同分保持原文顺序）
        pool = sorted(hi_hits, key=lambda l: -sum(k in l for k in hi))
    return "；".join(pool[:max_lines])[:max_chars]


def mock_generate(resume_text, jd, company, knowledge_base="", retrieved=None):
    bits = _pick_resume_bits(resume_text)
    has_bits = bool(bits)
    retrieved = retrieved or []
    req_lines = [l.strip(" -•") for l in jd.splitlines() if l.strip() and ("熟悉" in l or "掌握" in l or "要求" in l or "经验" in l)]
    if not req_lines:
        req_lines = [l.strip(" -•") for l in jd.splitlines() if l.strip()][:4] or ["相关技术能力"]
    alignment = []
    for r in req_lines[:6]:
        kw = r.split("：")[0].split(":")[0][:8]
        match = "strong" if kw and kw in resume_text else ("partial" if r[:4] in resume_text else "missing")
        alignment.append({
            "requirement": r[:60],
            "resume_evidence": "简历中可见相关表述" if match != "missing" else "未体现",
            "match": match,
            "note": "Mock 自动判断" if match != "missing" else "建议准备相关案例",
        })
    # 简历证据兜底：有片段则引用，无片段则给出通用方法论
    exp_anchor = (f"我简历里最有代表性的一段经历是：{bits}。"
                  "我会先讲清业务背景与我的职责边界，再拆解核心难点（性能/并发/数据问题），"
                  "说明方案选型与对比过的备选方案，最后用可验证的量化指标收尾（耗时、QPS、规模等）。") if has_bits else (
                  "我会选简历中贡献最大的一段经历，按 STAR 组织：背景(S) → 任务(T) → 行动(A，含技术选型与踩坑) → "
                  "量化结果(R，性能/规模/收益)。若被追问细节，我会对照简历逐条展开，不夸大、不虚构。")
    tech_anchor = (f"我会结合简历中『{bits[:80]}』这段经历来展开说明。") if has_bits else (
                  "我会从概念定义、工作机制、适用边界三层展开，再结合我实际项目中的场景说明选型取舍。")
    questions = [
        {"id": 1, "category": "基础技术",
         "question": f"{company} 的岗位对基础要求很高，请说明你最熟悉的一项技术的核心原理，并举例说明你如何在项目中运用它。",
         "answer": (f"结论先行：我最熟悉且用得最多的一项技术是简历中主技能栈的代表。{tech_anchor}"
                    "我会按三步回答：第一，核心原理——用一两句话讲清它解决什么问题、怎么工作；"
                    "第二，项目落地——说明我在哪个业务场景用它、当时的技术选型考量、遇到过什么坑以及如何权衡取舍；"
                    "第三，边界意识——讲清它的局限和适用前提，避免被追问时答不出。整个回答控制在 2 分钟，结论先行、层次分明。"),
         "focus": "基础扎实度与工程落地能力",
         "scoring": "优秀：原理清晰且有真实落地案例与取舍；合格：概念正确但缺少项目印证；不合格：含糊或背概念。"},
        {"id": 2, "category": "基础技术",
         "question": "如果让你为当前项目设计一个缓存方案，你会考虑哪些因素？结合你简历中相关经历谈谈。",
         "answer": (f"我会先明确业务场景的数据特点（读多写少还是强一致），再决定缓存层级与淘汰策略。{tech_anchor}"
                    "重点考虑：缓存与数据库的一致性（旁路缓存/双删/延迟双删）、缓存击穿/穿透/雪崩的防护（互斥锁、布隆过滤器、过期时间加抖动）、"
                    "命中率监控与容量规划。最后用简历中相似经历说明我实际做过哪些取舍，以及线上效果如何。"),
         "focus": "系统设计思维与经验迁移",
         "scoring": "优秀：有完整权衡框架并能结合实际经历；合格：能列出主要因素；不合格：只想到单点方案。"},
        {"id": 3, "category": "基础技术",
         "question": "请讲讲你在简历项目中遇到的一个印象最深的 Bug 或线上问题，你是如何定位和解决的？",
         "answer": ("我会按『现象 → 排查 → 根因 → 修复 → 复盘』五步讲：先描述线上现象与影响范围；"
                    "再讲排查过程，包括我如何缩小范围（看日志、加监控、二分定位、复现实验）；"
                    "然后点出根因，说明它为什么能绕过我当时的预期；修复时讲清改动方案与回归验证；"
                    "最后复盘这类问题如何从机制上避免（如加监控告警、补测试、代码评审关注点）。"
                    "若简历中此类经历较少，我会迁移讲实习中协助排查的经历，确保细节真实。"),
         "focus": "问题排查方法论与复盘能力",
         "scoring": "优秀：有完整排查链条与机制级复盘；合格：能讲清定位过程；不合格：只讲修复不讲方法。"},
        {"id": 4, "category": "基础技术",
         "question": "你简历里提到的主要技术栈中，最不熟悉的是哪一项？如果入职后马上要用，你会怎么补？",
         "answer": ("我会诚实说明不熟悉到哪个程度，不回避。然后给出可执行的补齐路径：第一，先看官方文档与最佳实践，"
                    "建立起『它能做什么、适合什么场景』的整体认知；第二，用最小 Demo 跑通核心链路，把文档知识落地为代码；"
                    "第三，对照现有代码库读关键模块，理解团队的实际用法与约定；第四，主动承担小任务在实践中验证，"
                    "并定期复盘沉淀。同时说明我简历中相近技术栈的迁移优势，让面试官看到学习曲线不会太长。"),
         "focus": "学习能力与诚实度",
         "scoring": "优秀：坦诚且有清晰可执行的补课路径；合格：态度诚恳但方法模糊；不合格：回避或虚假自信。"},
        {"id": 5, "category": "实习项目深挖",
         "question": "请挑选简历中你贡献最大的一段实习/项目经历，用 STAR 讲清背景、难点与量化结果。",
         "answer": exp_anchor,
         "focus": "真实项目贡献与复盘",
         "scoring": "优秀：有量化结果且自洽、能扛追问；合格：有过程无量化；不合格：泛泛而谈。"},
        {"id": 6, "category": "实习项目深挖",
         "question": "在这段经历中，你个人最核心的技术产出是什么？如果重新做一次，你会改变哪些决策？",
         "answer": (f"我会明确区分『团队成果』与『我个人产出』，避免贪功也避免被质疑。{exp_anchor}"
                    "讲清楚我的具体职责边界（负责哪些模块、哪些设计是我主导的）；"
                    "再讲重做时会改变的决策，例如架构选型、技术方案、排期优先级等，并说明改进后的预期收益，"
                    "以此展示复盘深度而不是事后诸葛亮。"),
         "focus": "个人贡献识别与决策复盘",
         "scoring": "优秀：职责边界清晰且有深度复盘；合格：能说清个人产出；不合格：把团队成果当个人成果。"},
        {"id": 7, "category": "实习项目深挖",
         "question": "你的项目经历中涉及『xxx』技术点（面试官从简历挑一个），请展开讲讲它的实现细节和踩坑过程。",
         "answer": (f"我简历中与这个技术点直接相关的部分：{bits}。我会先讲这个技术点在项目里具体解决什么问题，"
                    "再讲实现细节：数据结构/流程/关键代码设计；然后重点讲踩坑过程——我最初是怎么做的、"
                    "为什么不行、查了哪些资料、最终怎么调整；最后总结这个坑带来的认知升级，"
                    "以及我后续在别的场景如何复用这个经验。"),
         "focus": "技术细节深度与真实度",
         "scoring": "优秀：细节真实、有踩坑与认知升级；合格：能讲清实现；不合格：答得笼统或前后矛盾。"},
        {"id": 8, "category": "行为软素质",
         "question": "当面试官给出的需求与你的技术判断冲突时，你会如何处理？请用一段真实经历说明。",
         "answer": ("我会按四步处理：第一，复述确认目标，避免双方理解偏差；第二，用数据或案例说明我的判断依据，"
                    "客观给出风险提示（成本、性能、可维护性）；第三，如果需求仍不可行，主动提供折中方案，"
                    "例如分期实现、灰度验证、先做最小可用版本；第四，对齐共识后全力推进，并在过程中及时同步进展。"
                    "核心原则：对事不对人，目标是共同把事做成，而不是争对错。"),
         "focus": "沟通与协作、向上管理",
         "scoring": "优秀：有方法论且有真实案例支撑；合格：态度与原则正确；不合格：对抗或盲从。"},
        {"id": 9, "category": "行为软素质",
         "question": "描述一次你在实习/项目中主动推动事情进展的经历：你发现了什么问题，做了什么，结果如何？",
         "answer": ("我会选一件体现主动性的事，按『发现问题 → 主动承担 → 推动落地 → 结果影响』讲。"
                    "重点突出：问题是我主动发现的而非被指派的；我如何说服团队/负责人认可这件事值得做；"
                    "落地过程中遇到阻力我怎么协调；最终结果对业务或团队的可见影响（效率提升、成本下降、风险消除等）。"
                    "若简历中有类似经历，我会直接引用对应项目数据，让回答更有说服力。"),
         "focus": "主动性、Owner 意识与影响力",
         "scoring": "优秀：有真实主动案例且结果可量化；合格：有主动性但结果一般；不合格：被动执行型回答。"},
        {"id": 10, "category": "纯算法",
         "question": _mock_algo_question(retrieved),
         "answer": _mock_algo_answer(retrieved),
         "focus": "算法思路、复杂度分析与边界意识（口述，不要求现场写码）",
         "scoring": "优秀：思路正确且能清晰讲出复杂度与边界，并举一反三；合格：思路基本正确但分析不完整；不合格：题型误判或逻辑错误。"},
    ]
    return {"company": company, "position": "（待从 JD 推断）", "alignment": alignment, "questions": questions}


def _mock_algo_question(retrieved):
    """Mock 第 10 题题干：直接采用 RAG 检索出的力扣真实题目。"""
    q = retrieved[0] if retrieved else None
    if not q:
        return ("纯算法题：请讲一讲「两数之和」的解题思路（哈希表法），并说明时间复杂度与空间复杂度，"
                "以及如果数组有序还可以怎么优化。不要求现场写完整代码。")
    return (f"纯算法题（力扣第 {q['no']} 题 · {q['title']}，{q['difficulty']}）：{q['desc']}"
            f" 示例：{q['examples']} 请先讲清你的解题思路与选型理由，再给出时间/空间复杂度，"
            "并补充边界条件与可能的优化方向。不要求现场写完整代码。")


def _mock_algo_answer(retrieved):
    """Mock 第 10 题参考回答：基于题库 hint 组织可背诵思路稿。"""
    q = retrieved[0] if retrieved else None
    if not q:
        return ("我会用哈希表：遍历时把「目标值减当前值」的补数存入哈希表，之后遇到补数即返回下标。"
                "时间复杂度 O(n)，空间 O(n)；若数组有序，可改用双指针夹逼做到 O(1) 空间。"
                "边界：数组只有两个元素、存在重复值时取哪一组下标。")
    return (f"结论先行：这是力扣第 {q['no']} 题「{q['title']}」，属于{q['difficulty']}难度。"
            f"我的思路：{q['hint']} 复杂度上，我会先给出时间与空间复杂度，并解释为什么不能更优；"
            "边界条件我会主动覆盖空输入、单元素、极端取值等情况；最后给出优化方向（空间换时间、"
            "能否降低一维）并延伸一两个变式，展示对这类题型的系统掌握。")


def mock_modify(current_state, instruction):
    qs = current_state.get("questions", [])
    max_id = max([q["id"] for q in qs], default=0)
    added = [{
        "id": max_id + 1,
        "category": "追加题",
        "question": f"（Mock 追加）围绕你的指令「{instruction[:40]}」出一道追问。",
        "answer": "（Mock）参考回答要点。",
        "focus": "指令相关",
        "scoring": "优秀/合格/不合格三档。",
    }]
    return {"action": "append", "added": added, "updated": [], "deleted": [], "reply": "（Mock）已按你的指令追加 1 道题。"}
