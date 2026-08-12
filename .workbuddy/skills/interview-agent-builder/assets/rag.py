"""RAG 检索模块：从力扣题库知识库（leetcode_bank.json）中检索真实题目。

核心思路：从 JD + 简历中提取技术关键词，映射到力扣标签（数组/哈希表/双指针/动态规划…），
对题库逐题打分排序，返回候选真实题目列表。全程不产生新题，杜绝模型编造。
"""
import json
import re
from pathlib import Path

_BANK = None
_BANK_PATH = Path(__file__).resolve().parent / "leetcode_bank.json"


def load_bank():
    """惰性加载题库，只读一次。"""
    global _BANK
    if _BANK is None:
        with open(_BANK_PATH, "r", encoding="utf-8") as f:
            _BANK = json.load(f)
    return _BANK


# 关键词 → 力扣标签映射（命中一个词，展开到多个相关标签）
_KW_TAGS = [
    ("动态规划", ["动态规划"]), ("dp", ["动态规划"]),
    ("回溯", ["回溯"]), ("贪心", ["贪心"]),
    ("哈希", ["哈希表"]), ("双指针", ["双指针"]),
    ("滑动窗口", ["滑动窗口"]), ("滑窗", ["滑动窗口"]),
    ("二分", ["二分查找"]),
    ("链表", ["链表"]), ("linked list", ["链表"]),
    ("栈", ["栈", "单调栈"]), ("队列", ["队列"]),
    ("二叉树", ["二叉树", "树", "深度优先搜索", "广度优先搜索"]),
    ("树", ["树", "二叉树", "深度优先搜索"]),
    ("dfs", ["深度优先搜索", "树", "二叉树"]),
    ("bfs", ["广度优先搜索", "矩阵", "树"]),
    ("图", ["图", "拓扑排序"]), ("拓扑", ["拓扑排序"]),
    ("排序", ["排序"]), ("堆", ["堆"]),
    ("位运算", ["位运算"]), ("前缀和", ["前缀和"]),
    ("递归", ["递归"]), ("字符串", ["字符串"]),
    ("数组", ["数组"]), ("矩阵", ["矩阵"]),
    ("设计", ["设计"]), ("分治", ["分治"]),
    ("并查集", ["并查集"]), ("字典树", ["字典树"]), ("trie", ["字典树"]),
    ("单调栈", ["单调栈", "栈"]),
    ("最近公共祖先", ["树", "深度优先搜索"]),
    ("最小生成树", ["图"]), ("最短路", ["图"]),
    ("lru", ["设计", "链表"]), ("缓存", ["设计", "链表"]),
]

# 角色/语言关键词 → 常考标签（JD 提到语言或岗位时追加偏好）
_ROLE_TAGS = [
    ("python", ["哈希表", "数组", "字符串", "滑动窗口", "双指针"]),
    ("java", ["哈希表", "数组", "链表", "树", "字符串"]),
    ("golang", ["哈希表", "数组", "字符串", "链表"]),
    ("go", ["哈希表", "数组", "字符串", "链表"]),
    ("c++", ["哈希表", "数组", "链表", "字符串"]),
    ("前端", ["字符串", "数组", "动态规划"]),
    ("后端", ["哈希表", "数组", "字符串", "滑动窗口", "双指针", "链表"]),
    ("算法", ["动态规划", "贪心", "二分查找", "回溯", "排序"]),
    ("数据结构", ["链表", "栈", "队列", "树", "哈希表", "堆"]),
    ("数据", ["动态规划", "前缀和", "哈希表"]),
]

_DIFF_BONUS = {"简单": 1, "中等": 2, "困难": 3}
# 无任何关键词时的兜底高频题（力扣经典题号）
_POPULAR = [1, 15, 3, 206, 146, 53, 102, 215, 300, 322]


def _tech_keywords(text):
    """从文本中提取命中的技术关键词集合（去重）。"""
    text = (text or "").lower()
    kws = set()
    for kw, _tags in _KW_TAGS:
        if kw in text:
            kws.add(kw)
    for role, _tags in _ROLE_TAGS:
        if role in text:
            kws.add(role)
    return kws


def _collect_tags(kws):
    """把关键词展开为标签集合。"""
    tags = set()
    for kw in kws:
        for table in (_KW_TAGS, _ROLE_TAGS):
            for k, ts in table:
                if k == kw:
                    tags.update(ts)
    return tags


def _difficulty_bonus(jd):
    """JD 中明示难度要求时给对应难度加权。"""
    jd = jd or ""
    if "困难" in jd:
        return {"困难": 2, "中等": 1, "简单": 0}
    if "中等" in jd:
        return {"中等": 1, "简单": 0, "困难": 0}
    return {}


def search_questions(jd, resume_text="", k=6):
    """检索 k 道候选题目。

    返回：[{no,title,difficulty,tags,desc,examples,hint}, ...]，按相关度降序。
    候选题全部来自题库，保证真实；无关键词命中时返回经典高频题。
    """
    bank = load_bank()
    kws = _tech_keywords(jd) | _tech_keywords(resume_text)
    if not kws:
        by_no = {q["no"]: q for q in bank}
        return [by_no[n] for n in _POPULAR if n in by_no][:k]

    tags = _collect_tags(kws)
    diff_bonus = _difficulty_bonus(jd)
    title_kws = {kw for kw in kws if len(kw) >= 2 and re.search(r"[\u4e00-\u9fa5a-z]+", kw)}

    scored = []
    for q in bank:
        s = 0
        for t in tags:
            if t in q["tags"]:
                s += 3
        # 关键词出现在标题/描述中时强加权
        qtitle, qdesc = q["title"].lower(), q["desc"].lower()
        for kw in title_kws:
            if kw in qtitle:
                s += 5
            elif kw in qdesc:
                s += 1
        s += diff_bonus.get(q["difficulty"], 0)
        scored.append((s, q))

    scored.sort(key=lambda x: -x[0])
    hits = [q for s, q in scored if s > 0]
    if not hits:
        by_no = {q["no"]: q for q in bank}
        hits = [by_no[n] for n in _POPULAR if n in by_no]
    return hits[:k]
