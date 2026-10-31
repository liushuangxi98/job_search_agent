"""应用与 Agent 的数据契约；只定义输入输出，不包含提示词、推理或算法。"""
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .schemas import Payload, JobData, ProfileData, QuestionData

Action = Literal['parse_resume', 'extract_jobs', 'index_knowledge', 'evaluate', 'interview', 'chat']


class RunStart(Payload):
    """发起运行的请求；action 选择处理能力，input 放任务输入，context 标识关联对象。"""
    action: Action
    context: dict = Field(default_factory=dict)
    input: dict = Field(default_factory=dict)


class RunInput(Payload):
    """恢复运行的人工输入；input_id 必须匹配当前待处理输入，防止重复确认。"""
    input_id: str
    decision: Literal['answer', 'confirm', 'reject', 'finish']
    text: str = Field(default='', max_length=20000)
    data: dict = Field(default_factory=dict)


class AgentResponse(Payload):
    """Agent 交回的结果；kind 决定 data 结构，state 只用于同一运行的内存续接。"""
    # running 由应用管理；needs_input 还需提供有效 pending_input，partial 表示已有部分结果。
    status: Literal['needs_input', 'completed', 'partial', 'failed'] = 'completed'
    kind: Literal['profile_draft', 'job_drafts', 'knowledge_result', 'evaluation_report',
                  'interview_question', 'interview_report', 'chat']
    data: dict = Field(default_factory=dict)
    pending_input: dict | None = None
    state: dict = Field(default_factory=dict)


class ReportJob(BaseModel):
    """岗位报告的展示结构；分数为百分制或 None，应用只校验范围而不计算权重。"""
    model_config = {'extra': 'allow'}
    job_id: str
    company: str
    title: str
    match_score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    salary_score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    commute_score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    total_score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    gaps: list[str] = Field(default_factory=list)
    recommendation: str = ''
    missing: list[str] = Field(default_factory=list)
    evidence: list = Field(default_factory=list)
    commutes: dict[str, 'CommuteResult'] = Field(default_factory=dict)

    # Pydantic 在字段校验后自动执行此方法，ValueError 会汇总为模型校验异常。
    @model_validator(mode='after')
    def no_total_for_missing_dimensions(self):
        """分项未知时禁止填写总分；返回校验后的自身，违规时抛 ValueError。"""
        if self.total_score is not None and any(x is None for x in (self.match_score, self.salary_score, self.commute_score)):
            raise ValueError('分项缺失时总分必须为空')
        return self


class CommuteResult(BaseModel):
    """单程通勤结果；时长为分钟、距离为公里，None 表示未知而非零耗时或零距离。"""
    model_config = {'extra': 'allow'}
    duration_minutes: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    distance_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class DisplayData(BaseModel):
    """允许 Agent 保留附加展示字段，子类只校验界面必需的已声明字段。"""
    model_config = {'extra': 'allow'}
    notice: str = ''


class ProfileDraftData(DisplayData):
    profile: ProfileData


class JobDraft(JobData):
    # 抽取结果可以缺字段；正式保存为岗位前，用户须在预览中补齐。
    company: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)


class JobDraftsData(DisplayData):
    """供用户预览的岗位草稿列表；返回这些数据本身不会新增数据库岗位。"""
    jobs: list[JobDraft]


class KnowledgeResultData(DisplayData):
    """题库处理摘要；返回题目列表不代表应用已经替 Agent 保存元数据或向量。"""
    questions: list[QuestionData] = Field(default_factory=list)
    coverage: list[str] = Field(default_factory=list)


class EvaluationReportData(DisplayData):
    title: str = '岗位比较报告'
    summary: str = ''
    jobs: list[ReportJob]


class InterviewQuestionData(DisplayData):
    """当前面试问题；round 只允许 1—8，来源信息随问题展示。
    总轮数含追问不超过 8 的约定，由 Agent 负责计数和结束。
    """
    round: int = Field(ge=1, le=8)
    question: str = Field(min_length=1)
    question_id: str
    source: str


class DimensionData(BaseModel):
    """面试维度及百分制得分；score 为 None 表示该维度没有可用评分。"""
    model_config = {'extra': 'allow'}
    name: str
    score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)


class AnswerData(BaseModel):
    model_config = {'extra': 'allow'}
    question: str
    answer: str
    question_id: str = ''
    source: str = ''


class InterviewReportData(DisplayData):
    title: str = '面试报告'
    summary: str = ''
    dimensions: list[DimensionData] = Field(default_factory=list)
    answers: list[AnswerData] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    practice: list[str] = Field(default_factory=list)


class ChatData(DisplayData):
    message: str


class ChangeProposal(Payload):
    """待用户确认的局部修改；revision 是读取时的修订号，不是预期更新后的值。"""
    target: Literal['profile', 'preferences', 'job']
    target_id: str | None = None
    revision: int = Field(ge=0)
    changes: dict = Field(min_length=1)

    @model_validator(mode='after')
    def job_id_required(self):
        """岗位提案必须指定 target_id；校验通过返回自身，否则抛 ValueError。"""
        if self.target == 'job' and not self.target_id:
            raise ValueError('岗位修改提案缺少 target_id')
        return self


class PendingInput(Payload):
    """Agent 提供的问题或提案；应用会另外生成一次性输入 ID，不由 Agent 自行分配。"""
    kind: Literal['question', 'proposal']
    prompt: str = Field(min_length=1)
    proposal: ChangeProposal | None = None

    @model_validator(mode='after')
    def proposal_required(self):
        """要求提案类型携带 proposal；普通问题可不携带，违规时抛 ValueError。"""
        if self.kind == 'proposal' and self.proposal is None:
            raise ValueError('修改提案不能为空')
        return self


RESULT_SCHEMAS = {
    'profile_draft': ProfileDraftData, 'job_drafts': JobDraftsData,
    'knowledge_result': KnowledgeResultData, 'evaluation_report': EvaluationReportData,
    'interview_question': InterviewQuestionData, 'interview_report': InterviewReportData, 'chat': ChatData,
}


def validate_result(kind, data):
    """按已知 kind 校验并返回展示字典；字段错误抛校验异常，未知 kind 抛 KeyError。

    RunService 先校验 AgentResponse，再调用本函数；这里不写数据库或计算评分。
    """
    return RESULT_SCHEMAS[kind].model_validate(data).model_dump()


# dataclass 只生成数据容器的初始化等方法，不像 Pydantic 那样自动校验字段内容。
@dataclass
class AgentCommand:
    """交给 Agent 的运行上下文；snapshot 保留发起时资料，human_input 首次为 None。"""
    run_id: str
    action: str
    input: dict
    context: dict
    snapshot: dict
    state: dict
    human_input: dict | None = None


@dataclass
class ApplicationServices:
    """交给 Agent 的应用存取入口；提供普通资料和文件访问，不负责 Agent 决策。"""
    # 名称保持接入兼容；repository 实际指向业务层 AgentDataService。
    repository: object
    files: object
