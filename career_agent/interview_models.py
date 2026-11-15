"""模型负责生成计划和决策，Plan 还保存本场面试的历史及进度。"""
from typing import Literal

from pydantic import BaseModel, Field


Grade = Literal['完美', '合格', '了解', '欠缺']
# 键的顺序也是考察顺序；值为该来源的主问题上限，追加追问不占新的名额。
TOPIC_LIMITS = {'岗位扩展': 1, '岗位与简历交集': 3, '简历经历': 1}


class Topic(BaseModel):
    """一道主问题所考察的能力；追问一直沿用它的 ID。"""
    id: str = Field(min_length=1, description='本场唯一的考点 ID，如 Topic1')
    name: str = Field(min_length=1, description='单一、具体的知识点，使用题库常见术语，如数据驱动测试、边界值分析')
    group: Literal['岗位与简历交集', '简历经历', '岗位扩展'] = Field(
        description='交集要求岗位与简历均有直接依据且匹配度高；简历经历选岗位未涉及的经历；岗位扩展选简历未涉及的岗位要求')


# 只把需要模型填写的字段注册为规划工具，历史和轮次由应用管理。
class TopicPlan(BaseModel):
    """开场交给模型填写的部分。运行时的进度和历史由程序填写。"""
    ask_plan: list[Topic] = Field(description='按提问顺序排列的考点')
    reason: str = Field(min_length=1, description='按考点 ID 简述选择依据；交集考点需同时指出岗位要求、简历经历和共同知识点')


class Plan(TopicPlan):
    """本场面试的续接数据，等待用户前写入 state，下次用同一个 state 恢复。"""
    topic_index: int = Field(default=0, description='正在考察的考点下标，从零开始')
    # 展示给用户的题目和交流也记在 history；报告按原顺序读取这些消息。
    history: list[dict] = Field(default_factory=list, description='按时间排列的模型交互、工具结果和用户与面试官的对话')


class ActionResult(BaseModel):
    """本次动作需要的数据，按动作填写对应字段，其余字段使用默认值。"""
    # 工具名用来分发调用，args 保存对应函数的入参。
    tool: str = Field(default='', description='需要工具时填写工具名称，如 search_questions')
    args: dict = Field(default_factory=dict, description='工具入参；search_questions 使用 {"queries": ["关键词"]}')
    question_id: str = Field(default='', description='展示主问时填写检索结果字典中的题目 ID')
    message: str = Field(default='', description='展示主问时填写题库原题全文；追问时填写题干；沟通交流时填写解释')
    reference: str = Field(default='', description='主问填写题库参考答案；追问可填写参考答案，其他动作留空')
    source: str = Field(default='', description='主问填写“题库原题 · 知识库第 N 题”，N 为 sort_order；追问填写“现场追问”')
    hint: str = Field(default='', description='展示主问或继续追问时给出的启发式提示')
    comment: str = Field(default='', description='本轮真实作答的点评；沟通交流留空')
    grade: Grade | None = Field(default=None, description='根据本考点截至本次的作答评价，无法判断或沟通交流填 null')


class DecisionNext(BaseModel):
    """所有面试动作都使用同一返回格式，校验通过后交给外层执行。"""
    action: Literal['需要工具', '展示主问', '跳过考点', '继续追问', '新的题目', '沟通交流', '面试结束'] = Field(
        description='根据当前考点、用户输入和历史决定下一步动作')
    result: ActionResult = Field(description='本次动作需要的参数和点评')
    reason: str = Field(min_length=1, description='用一句话说明为什么选择这个动作')


class AnswerReport(BaseModel):
    """模型根据历史生成的一道已答问题的复盘。"""
    question: str = Field(description='历史中实际问出的题干，保留原文')
    answer: str = Field(description='用户对这道题的实际作答，保留原文')
    feedback: str = Field(description='结合这次作答给出的点评')
    question_id: str = Field(default='', description='题库原题的 ID，现场追问留空')
    source: str = Field(default='', description='复制出题时记录的出处')


class TopicReport(BaseModel):
    """模型综合一个考点下的问答和交流给出的评价。"""
    name: str = Field(description='面试计划中的考点名称')
    grade: Grade | None = Field(description='本考点的综合评价，未考察或证据不足时填 null')
    summary: str = Field(description='评价依据；没有作答时说明未考察或证据不足')
    rounds: list[int] = Field(description='支持该评价的问答轮号；主问和追问按出现顺序从 1 编号，澄清不另计轮')


class Report(BaseModel):
    """模型根据整场历史生成总体反馈、逐题点评和考点评价。"""
    summary: str = Field(description='基于已答问题的总体表现')
    answers: list[AnswerReport] = Field(description='按提问顺序整理已作答题目的点评，未作答的题目省略')
    topics: list[TopicReport] = Field(description='面试计划中每个考点的评价，包括未考察的考点')
    weaknesses: list[str] = Field(description='有实际作答依据的薄弱点')
    practice: list[str] = Field(description='针对这些薄弱点的练习建议')
    reason: str = Field(min_length=1, description='用一句话指出支持整体评价的具体发言或表现')
