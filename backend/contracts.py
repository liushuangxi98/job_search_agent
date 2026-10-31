"""应用与 Agent 的数据契约；只定义输入输出，不包含提示词、推理或算法。"""
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .schemas import Payload, JobData, ProfileData, QuestionData

Action = Literal['parse_resume', 'extract_jobs', 'index_knowledge', 'evaluate', 'interview', 'chat']


class RunStart(Payload):
    action: Action
    context: dict = Field(default_factory=dict)
    input: dict = Field(default_factory=dict)


class RunInput(Payload):
    input_id: str
    decision: Literal['answer', 'confirm', 'reject', 'finish']
    text: str = Field(default='', max_length=20000)
    data: dict = Field(default_factory=dict)


class AgentResponse(Payload):
    status: Literal['needs_input', 'completed', 'partial', 'failed'] = 'completed'
    kind: Literal['profile_draft', 'job_drafts', 'knowledge_result', 'evaluation_report',
                  'interview_question', 'interview_report', 'chat']
    data: dict = Field(default_factory=dict)
    pending_input: dict | None = None
    state: dict = Field(default_factory=dict)


class ReportJob(BaseModel):
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

    @model_validator(mode='after')
    def no_total_for_missing_dimensions(self):
        if self.total_score is not None and any(x is None for x in (self.match_score, self.salary_score, self.commute_score)):
            raise ValueError('分项缺失时总分必须为空')
        return self


class CommuteResult(BaseModel):
    model_config = {'extra': 'allow'}
    duration_minutes: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    distance_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class DisplayData(BaseModel):
    model_config = {'extra': 'allow'}
    notice: str = ''


class ProfileDraftData(DisplayData):
    profile: ProfileData


class JobDraft(JobData):
    # 抽取结果可以缺字段；正式保存为岗位前，用户须在预览中补齐。
    company: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)


class JobDraftsData(DisplayData):
    jobs: list[JobDraft]


class KnowledgeResultData(DisplayData):
    questions: list[QuestionData] = Field(default_factory=list)
    coverage: list[str] = Field(default_factory=list)


class EvaluationReportData(DisplayData):
    title: str = '岗位比较报告'
    summary: str = ''
    jobs: list[ReportJob]


class InterviewQuestionData(DisplayData):
    round: int = Field(ge=1, le=8)
    question: str = Field(min_length=1)
    question_id: str
    source: str


class DimensionData(BaseModel):
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
    target: Literal['profile', 'preferences', 'job']
    target_id: str | None = None
    revision: int = Field(ge=0)
    changes: dict = Field(min_length=1)

    @model_validator(mode='after')
    def job_id_required(self):
        if self.target == 'job' and not self.target_id:
            raise ValueError('岗位修改提案缺少 target_id')
        return self


class PendingInput(Payload):
    kind: Literal['question', 'proposal']
    prompt: str = Field(min_length=1)
    proposal: ChangeProposal | None = None

    @model_validator(mode='after')
    def proposal_required(self):
        if self.kind == 'proposal' and self.proposal is None:
            raise ValueError('修改提案不能为空')
        return self


RESULT_SCHEMAS = {
    'profile_draft': ProfileDraftData, 'job_drafts': JobDraftsData,
    'knowledge_result': KnowledgeResultData, 'evaluation_report': EvaluationReportData,
    'interview_question': InterviewQuestionData, 'interview_report': InterviewReportData, 'chat': ChatData,
}


def validate_result(kind, data):
    return RESULT_SCHEMAS[kind].model_validate(data).model_dump()


@dataclass
class AgentCommand:
    run_id: str
    action: str
    input: dict
    context: dict
    snapshot: dict
    state: dict
    human_input: dict | None = None


@dataclass
class ApplicationServices:
    # 名称保持接入兼容；repository 实际指向业务层 AgentDataService。
    repository: object
    files: object
