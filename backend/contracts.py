"""应用与 Agent 的数据契约；只定义输入输出，不包含提示词、推理或算法。"""
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .schemas import Payload, JobData, ProfileData, QuestionData

# 阅读导航
#   1. Action / RunStart / RunInput：用户请求什么，怎样补充输入。
#   2. AgentResponse：Agent 返回结果时使用的外层结构。
#   3. 结果模型：查阅 data 及嵌套对象的具体字段。
#   4. ChangeProposal / PendingInput：资料修改与人工确认。
#   5. RESULT_SCHEMAS：按 kind 快速查 data 必填 key 和最小示例。
#   6. AgentCommand / ApplicationServices：Agent 接收的上下文与应用服务。
#
# 阅读约定
#   必填与默认值：
#     “必填”表示 JSON 必须有这个 key；“可省略”表示省略后使用默认值。
#     必填不等于非空；未设 min_length 的字符串允许 ""，未设最小长度的列表允许 []。
#     可省略不等于可填 None；只有标注 | None 的字段接受 JSON null。
#   模型规则与业务约定：
#     只有类型、Field 和校验器中的规则会被模型强制检查，业务约定由 Agent 实现。
#     Payload 子类拒绝未声明的 key，并去除字符串首尾空白，见 schemas.Payload。
#     展示模型允许附加字段；嵌套 ProfileData、JobDraft、QuestionData 按各自定义校验。
#   示例：
#     ID 仅展示用途，实际使用真实资料、岗位或题目 ID；模型不统一校验 ID 格式。


# ---------------------------------------------------------------------------
# 1. 用户请求与人工输入
# ---------------------------------------------------------------------------
#
# Action：用户要完成的业务动作
#
# parse_resume：简历解析
#   场景：用户上传简历。
#   输入：input.document_id 引用 resume 资料。
#   返回：profile_draft，提取经历、技能和依据；用户确认后才保存画像。
#
# extract_jobs：招聘信息提取
#   场景：用户上传招聘截图，或粘贴招聘原文。
#   输入：input.document_id 引用 image 资料，或 input.text 提供原文。
#   返回：job_drafts，供用户编辑后入库。
#
# index_knowledge：题库导入与索引
#   场景：用户导入本地 MD 题库。
#   输入：input.document_id 引用 knowledge 资料。
#   返回：knowledge_result，展示处理摘要。
#   业务约定：Agent 自行保存元数据和索引，不能仅靠返回值完成入库。
#
# evaluate：岗位评估
#   场景：用户比较已选岗位；发起前须确认画像。
#   输入：input.job_ids 为 1—10 个岗位 ID。
#   返回：evaluation_report；信息不足但已有可用结果时可返回 partial。
#
# interview：岗位面试练习
#   场景：用户针对一个岗位练习；发起前须确认画像。
#   输入：input.job_id 为岗位 ID。
#   出题：interview_question + needs_input。
#   结束：完成面试或收到 finish 后返回 interview_report。
#
# chat：聊天与资料补充
#   场景：用户补充资料，或追问当前岗位、报告。
#   输入：input.text 为消息，context 标识关联对象。
#   回答：chat；提出资料修改时使用 chat + needs_input。
#   确认：应用先保存提案再续接 Agent，收到 confirm 不要重复修改；reject 不保存。
#
# 通用规则：
#   上述为业务推荐映射，运行服务不强制校验 action 与 kind 是否配对。
#   任意动作需要澄清时可暂时返回 chat + needs_input，恢复后仍收到原 action。
#   真实模式的资料和文本前提由 RunService 检查；demo 可不提供资料以演示固定结果。
Action = Literal['parse_resume', 'extract_jobs', 'index_knowledge', 'evaluate', 'interview', 'chat']


class RunStart(Payload):
    """发起运行的请求；action 选择处理能力，input 放任务输入，context 标识关联对象。"""

    # action：用户要执行的动作；必填，限上方 6 个值。
    #   示例："evaluate"。
    action: Action

    # context：页面关联上下文；可省略，默认 {}，未固定内部 key。
    #   示例：{"type": "job", "id": "job_001"}。
    context: dict = Field(default_factory=dict)

    # input：动作参数；可省略，默认 {}，业务前提见上方 action 说明。
    #   示例：{"job_ids": ["job_001"]}。
    input: dict = Field(default_factory=dict)


class RunInput(Payload):
    """恢复运行的人工输入；input_id 必须匹配当前待处理输入，防止重复确认。"""

    # input_id：应用返回的 pending_input.id；必填字符串，服务检查是否仍有效。
    #   示例："input_001"。
    input_id: str

    # decision：用户决定；必填。
    #   取值：answer 回答、confirm 确认提案、reject 拒绝提案、finish 结束。
    #   匹配规则：
    #     proposal：仅接受 confirm / reject。
    #     question：仅接受 answer / finish，answer 须有非空 text 或 data。
    #   结束方式：面试 finish 续接 Agent 生成报告，其他动作直接结束，不再调用 Agent。
    #   示例："answer"。
    decision: Literal['answer', 'confirm', 'reject', 'finish']

    # text：用户文字；可省略，默认 ""，最多 20000 字符。
    #   示例："办公地址是杭州西湖区……"。
    text: str = Field(default='', max_length=20000)

    # data：结构化补充；可省略，默认 {}，内部格式由 Agent 与界面约定。
    #   示例：{"city": "杭州"}。
    data: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 2. Agent 返回结构：状态、结果类别、展示数据和续接状态
# ---------------------------------------------------------------------------


class AgentResponse(Payload):
    """Agent 交回的结果；kind 决定 data 结构，state 只用于同一运行的内存续接。"""

    # status：当前动作的处理状态；可省略，默认 "completed"。
    #
    #   needs_input：暂停，等待用户回答或确认提案。
    #     适用：地址存在歧义，或需要确认资料修改。
    #     要求：提供有效 pending_input；界面停止轮询，等待用户提交。
    #     恢复：应用生成输入 id，通过 command.human_input 交回回答或决定。
    #
    #   completed：当前动作完成。
    #     适用：画像草稿、最终报告或普通聊天回答已经生成。
    #     保存：草稿仍需用户确认，只有评估/面试报告会由应用自动保存。
    #
    #   partial：当前动作结束，但已有可用且不完整的结果。
    #     适用：通勤查询失败，而其他评估项已经完成。
    #     要求：data 仍须结构合法，未知分数/通勤值填 None。
    #     保存：评估/面试报告会保存；此状态不等待输入，也不自动续跑。
    #
    #   failed：当前动作失败，无法交付成功报告。
    #     适用：关键模型请求失败，无法形成可用结果。
    #     要求：data 仍按 kind 校验；通常以 chat 结果说明失败原因。
    #     示例：kind="chat"，data={"message": "失败原因"}。
    #
    #   通用规则：running 由应用管理；上述 4 个状态均不触发自动重试。
    status: Literal['needs_input', 'completed', 'partial', 'failed'] = 'completed'

    # kind：结果类别；必填，限下方 7 个值；对应模型、业务场景与必填 key 见 RESULT_SCHEMAS 上方。
    #   示例："evaluation_report"；"error" 是运行服务生成的内部结果类型，Agent 不能返回它。
    kind: Literal['profile_draft', 'job_drafts', 'knowledge_result', 'evaluation_report',
                  'interview_question', 'interview_report', 'chat']

    # data：展示数据；外层可省略，默认 {}，但随后仍须通过 kind 对应的模型校验。
    #   示例：kind="chat" 时填 {"message": "请补充工作地点"}，不能把 AgentResponse 再嵌入 data。
    data: dict = Field(default_factory=dict)

    # pending_input：待用户回答或确认的结构；可省略，默认 None。
    #   基础校验：所有状态都要求 dict | None，且整体响应可被 JSON 序列化。
    #   内容校验：仅 needs_input 时按 PendingInput 校验，并创建待处理输入。
    #   其他状态：不会创建待处理输入。
    #   示例：{"kind": "question", "prompt": "工作地点在哪里？"}。
    pending_input: dict | None = None

    # state：Agent 下一轮需要的状态；可省略，默认 {}。
    #   示例：{"round": 1, "answers": []}。
    #   应用仅在内存中保存并通过 command.state 交回；本字段不是数据库持久化或完整聊天记忆。
    state: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 3. 结果数据模型：data 的字段与嵌套结构
# ---------------------------------------------------------------------------


# 3.1 ReportJob：评估报告中的单个岗位
class ReportJob(BaseModel):
    """岗位报告的展示结构；分数为百分制或 None，应用只校验范围而不计算权重。"""
    # 允许附加字段；这些展示模型不统一裁剪字符串空白，不要依赖它替 Agent 清理内容。
    model_config = {'extra': 'allow'}

    # job_id：被评估岗位 ID；必填字符串，无长度限制。
    #   示例："job_001"，业务上应对应输入快照中的岗位。
    job_id: str

    # company：公司名称；必填字符串，未限制长度或禁止空串。
    #   示例："星河科技"。
    company: str

    # title：岗位名称；必填字符串，未限制长度或禁止空串。
    #   示例："Agent 应用开发工程师"。
    title: str

    # match_score：匹配度分数；可省略，默认 None；有限数值 0—100，None 表示未知。
    #   示例：82。
    match_score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)

    # salary_score：薪资符合度分数，不是薪资金额；可省略，默认 None；有限数值 0—100。
    #   示例：90。
    salary_score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)

    # commute_score：通勤便利度分数，不是分钟数；可省略，默认 None；有限数值 0—100。
    #   示例：70。
    commute_score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)

    # total_score：总分；可省略，默认 None；有限数值 0—100，任一分项为 None 时总分也必须为 None。
    #   业务约定由 Agent 按 60% 匹配 + 25% 薪资 + 15% 通勤计算；模型不验证公式。
    #   示例：82.2。
    total_score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)

    # gaps：能力或经历差距；可省略，默认 []，字符串列表且不限条数。
    #   示例：["缺少 RAG 项目证据"]。
    gaps: list[str] = Field(default_factory=list)

    # recommendation：投递建议及解释；可省略，默认 ""，无长度限制。
    #   示例："建议投递，补充工具异常处理案例。"。
    recommendation: str = ''

    # missing：尚缺的资料或证据；可省略，默认 []，字符串列表且不限条数。
    #   示例：["办公地址待确认"]。
    missing: list[str] = Field(default_factory=list)

    # evidence：结论依据；可省略，默认 []，未固定元素结构或数量；业务上应返回可追溯的 JSON 数据。
    #   示例：[{"source": "JD", "text": "要求熟悉 RAG"}]；不要把示例内部 key 当成强制 schema。
    evidence: list = Field(default_factory=list)

    # commutes：不同出行方式的单程路线；可省略，默认 {}。
    #   字典 key：业务上使用 public_transit（公交）或 driving（驾车），模型未限定枚举。
    #   字典值：按 CommuteResult 校验；缺少路线时可省略该项。
    #   示例：{"driving": {"duration_minutes": 35, "distance_km": 12.5}}。
    commutes: dict[str, 'CommuteResult'] = Field(default_factory=dict)

    # Pydantic 在字段校验后自动执行此方法，ValueError 会汇总为模型校验异常。
    @model_validator(mode='after')
    def no_total_for_missing_dimensions(self):
        """分项未知时禁止填写总分；返回校验后的自身，违规时抛 ValueError。"""
        if self.total_score is not None and any(x is None for x in (self.match_score, self.salary_score, self.commute_score)):
            raise ValueError('分项缺失时总分必须为空')
        return self


# 3.2 CommuteResult：单程路线的时长与距离
class CommuteResult(BaseModel):
    """单程通勤结果；时长为分钟、距离为公里，None 表示未知而非零耗时或零距离。"""
    model_config = {'extra': 'allow'}

    # duration_minutes：单程时长，单位分钟；可省略，默认 None；非负有限数值，未知不填 0。
    #   示例：45。
    duration_minutes: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    # distance_km：单程距离，单位公里；可省略，默认 None；非负有限数值。
    #   示例：18.2。
    distance_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)


# 3.3 DisplayData：7 种结果共用的展示字段
class DisplayData(BaseModel):
    """允许 Agent 保留附加展示字段，子类只校验界面必需的已声明字段。"""
    model_config = {'extra': 'allow'}

    # notice：下方 7 种 data 均继承此提示字段；可省略，默认 ""，无长度限制。
    #   示例："部分信息缺失，请核对后再保存。"；不是 status，也不负责触发确认流程。
    notice: str = ''


# 3.4 ProfileDraftData：简历解析后的画像草稿
class ProfileDraftData(DisplayData):

    # profile：简历解析出的画像草稿；必填，结构为 schemas.ProfileData；继承可选 notice。
    #   示例：{"summary": "四年 Python 测试开发", "skills": ["Python"]}；返回草稿不会自动覆盖画像。
    profile: ProfileData


# 3.5 JobDraft / JobDraftsData：单个岗位草稿与草稿列表
class JobDraft(JobData):
    # 抽取结果可以缺字段；正式保存为岗位前，用户须在预览中补齐。
    #
    # 继承字段：与 schemas.JobData 的校验完全相同。
    #   city：默认 ""，最多 100 字符；例："杭州"。
    #   address：默认 ""，最多 300 字符；例："杭州市西湖区某园区"。
    #   salary_min / salary_max：各默认 None，元/月整数 1—10000000；例：20000 / 30000。
    #     两端已知时下限不得超过上限，草稿仍执行继承的薪资校验器。
    #   salary_note：默认 ""，最多 500 字符；例："13 薪，奖金另计"。
    #   jd_text：默认 ""，最多 50000 字符；例："负责 Agent 工具调用开发"。
    #   notes：默认 ""，最多 5000 字符；例："需核实具体工作地点"。
    #   source_ids：默认 []，最多 30 个字符串 ID；例：["image_001"]，保存时检查来源存在。
    #   以上继承字段均可省略；本类仅放宽下面的 company、title。

    # company：草稿公司名；可省略，默认 None，也允许 ""；非 None 时最多 200 字符。
    #   示例："星河科技"。
    company: str | None = Field(default=None, max_length=200)

    # title：草稿岗位名；可省略，默认 None，也允许 ""；非 None 时最多 200 字符。
    #   示例："Agent 开发"。
    title: str | None = Field(default=None, max_length=200)


class JobDraftsData(DisplayData):
    """供用户预览的岗位草稿列表；返回这些数据本身不会新增数据库岗位。"""

    # jobs：待预览的岗位草稿；必填列表，每项是 JobDraft，模型允许 []，未限制列表长度。
    #   示例：[{"company": null, "title": "Agent 开发", "source_ids": ["image_001"]}]（JSON）。
    jobs: list[JobDraft]


# 3.6 KnowledgeResultData：题库导入与索引摘要
class KnowledgeResultData(DisplayData):
    """题库处理摘要；返回题目列表不代表应用已经替 Agent 保存元数据或向量。"""

    # questions：导入后展示的题目；可省略，默认 []，每项为 schemas.QuestionData，列表不限条数。
    #   示例：[{"question": "什么是 RAG？", "tags": ["RAG"]}]；真实入库由 Agent 另外执行。
    questions: list[QuestionData] = Field(default_factory=list)

    # coverage：题库覆盖的知识点名称；可省略，默认 []，不限条数的字符串列表，不是覆盖率数值。
    #   示例：["RAG", "工具调用"]。
    coverage: list[str] = Field(default_factory=list)


# 3.7 EvaluationReportData：岗位比较报告
class EvaluationReportData(DisplayData):

    # title：报告标题；可省略，默认 "岗位比较报告"，无长度限制。
    #   示例："杭州岗位比较"。
    title: str = '岗位比较报告'

    # summary：比较结论摘要；可省略，默认 ""，无长度限制。
    #   示例："A 岗位更匹配，B 岗位缺通勤信息。"。
    summary: str = ''

    # jobs：每岗评估结果；必填列表，每项为 ReportJob，允许 []，不强制核对与输入岗位的对应关系。
    #   示例：[{"job_id": "job_001", "company": "星河科技", "title": "Agent 开发"}]。
    jobs: list[ReportJob]


# 3.8 InterviewQuestionData：当前一轮面试问题
class InterviewQuestionData(DisplayData):
    """当前面试问题；round 只允许 1—8，来源信息随问题展示。
    总轮数含追问不超过 8 的约定，由 Agent 负责计数和结束。
    """

    # round：当前轮号；必填整数，范围 1—8。
    #   示例：1。业务上追问也计一轮，由 Agent 累计。
    round: int = Field(ge=1, le=8)

    # question：本轮题干；必填字符串，至少 1 字符，无长度上限。
    #   示例："如何处理工具请求超时？"。
    question: str = Field(min_length=1)

    # question_id：题目或追问标识；必填字符串，但未禁止空串或校验数据库存在性。
    #   示例："question_001"。
    question_id: str

    # source：供用户核对的来源说明；必填字符串，但未禁止空串或限制长度。
    #   示例："agent题库.md#工具超时"。
    source: str


# 3.9 DimensionData / AnswerData：面试评分维度与问答记录
class DimensionData(BaseModel):
    """面试维度及百分制得分；score 为 None 表示该维度没有可用评分。"""
    model_config = {'extra': 'allow'}

    # name：评分维度名称；必填字符串，未设长度约束。
    #   示例："工具调用与异常处理"。
    name: str

    # score：维度得分；可省略，默认 None；有限数值 0—100，None 表示未考察或无法判断。
    #   示例：75。
    score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)


class AnswerData(BaseModel):
    model_config = {'extra': 'allow'}

    # question：当轮实际题干；必填字符串，未设长度约束或禁止空串。
    #   示例："为什么需要重排？"。
    question: str

    # answer：用户当轮作答原文；必填字符串，未设长度约束，允许 ""。
    #   示例："重排用于提高候选相关性。"。
    answer: str

    # question_id：与出题时对应的题目 ID；可省略，默认 ""，无格式校验。
    #   示例："question_001"。
    question_id: str = ''

    # source：本题来源位置或说明；可省略，默认 ""，无长度约束。
    #   示例："rag题库.md#重排"。
    source: str = ''


# 3.10 InterviewReportData：面试结束后的复盘报告
class InterviewReportData(DisplayData):

    # title：报告标题；可省略，默认 "面试报告"，无长度限制。
    #   示例："Agent 开发面试复盘"。
    title: str = '面试报告'

    # summary：整体表现摘要；可省略，默认 ""，无长度限制。
    #   示例："工具调用基础清晰，检索评测需加强。"。
    summary: str = ''

    # dimensions：各维度得分；可省略，默认 []，每项为 DimensionData，不限条数。
    #   示例：[{"name": "工具调用", "score": 75}]。
    dimensions: list[DimensionData] = Field(default_factory=list)

    # answers：已完成的问答记录；可省略，默认 []，每项为 AnswerData，模型未限制记录数量。
    #   示例：[{"question": "什么是 RAG？", "answer": "检索增强生成。", "question_id": "question_001"}]。
    answers: list[AnswerData] = Field(default_factory=list)

    # weaknesses：薄弱项说明；可省略，默认 []，不限条数的字符串列表。
    #   示例：["缺少检索效果评估方法"]。
    weaknesses: list[str] = Field(default_factory=list)

    # practice：后续练习建议；可省略，默认 []，不限条数的字符串列表。
    #   示例：["用固定题集比较召回策略"]。
    practice: list[str] = Field(default_factory=list)


# 3.11 ChatData：聊天、澄清或失败说明
class ChatData(DisplayData):

    # message：展示给用户的回答、澄清或失败说明；必填字符串，未设长度约束或禁止空串。
    #   示例："请补充公司工作地点，以便计算通勤。"。
    message: str


# ---------------------------------------------------------------------------
# 4. 人工交互：资料修改提案与待处理输入
# ---------------------------------------------------------------------------


class ChangeProposal(Payload):
    """待用户确认的局部修改；revision 是读取时的修订号，不是预期更新后的值。"""

    # target：要修改的对象；必填，限 profile（画像）、preferences（条件）、job（岗位）。
    #   示例："preferences"。
    target: Literal['profile', 'preferences', 'job']

    # target_id：对象 ID；可省略、默认 None，但 target="job" 时必须为非空字符串。
    #   示例："job_001"。
    #   画像、条件是单用户当前记录，不需要此 ID；该字段未设置长度上限。
    target_id: str | None = None

    # revision：读取目标记录时的修订号；必填非负整数。
    #   示例：3。应用保存时还会检查是否已过期。
    revision: int = Field(ge=0)

    # changes：局部修改字段，不是完整资料；必填非空字典，至少 1 项。
    #   示例：{"city": "杭州"}。
    #   应用按 target 检查允许字段与合并后的值，岗位提案不能改 source_ids；这里只检查字典非空。
    changes: dict = Field(min_length=1)

    @model_validator(mode='after')
    def job_id_required(self):
        """岗位提案必须指定 target_id；校验通过返回自身，否则抛 ValueError。"""
        if self.target == 'job' and not self.target_id:
            raise ValueError('岗位修改提案缺少 target_id')
        return self


class PendingInput(Payload):
    """Agent 提供的问题或提案；应用会另外生成一次性输入 ID，不由 Agent 自行分配。"""

    # kind：交互类型；必填，question 为补充问题，proposal 为资料修改确认。
    #   示例："question"。
    kind: Literal['question', 'proposal']

    # prompt：展示给用户的问题或确认提示；必填字符串，去首尾空白后至少 1 字符，无上限。
    #   示例："是否把期望城市改为杭州？"。
    prompt: str = Field(min_length=1)

    # proposal：修改内容；可省略、默认 None，但 kind="proposal" 时必须给出 ChangeProposal。
    #   示例：{"target": "preferences", "revision": 3, "changes": {"city": "杭州"}}。
    proposal: ChangeProposal | None = None

    @model_validator(mode='after')
    def proposal_required(self):
        """要求提案类型携带 proposal；普通问题可不携带，违规时抛 ValueError。"""
        if self.kind == 'proposal' and self.proposal is None:
            raise ValueError('修改提案不能为空')
        return self


# ---------------------------------------------------------------------------
# 5. kind 速查与结果校验
# ---------------------------------------------------------------------------
#
# 通用规则：
#   kind 决定 data 的校验模型，字段细节见上方对应模型。
#   7 种 data 均可省略 notice（默认 ""），并允许附加展示字段。
#   下面的最小示例只保证结构合法，不代表业务任务已充分完成。
#
# profile_draft：简历解析后的画像草稿
#   必填：profile，对应 ProfileData；嵌套字段均有默认值。
#   保存：不会自动覆盖画像，空画像不能被用户确认。
#   示例：{"profile": {}}。
#
# job_drafts：待导入的岗位草稿
#   必填：jobs，元素对应 JobDraft；等待用户预览保存。
#   空值：[] 合法，表示没有可展示草稿，不代表成功提取出岗位。
#   示例：{"jobs": []}。
#
# knowledge_result：题库导入或索引摘要
#   必填：无；questions、coverage 默认 []。
#   保存：题目元数据和向量索引仍由 Agent 实际写入。
#   示例：{}。
#
# evaluation_report：岗位评估报告
#   必填：jobs；每项对应 ReportJob，必填 job_id、company、title。
#   示例：
#     {"jobs": [
#       {"job_id": "job_001", "company": "星河科技", "title": "Agent 开发"}
#     ]}
#
# interview_question：当前一轮面试问题
#   必填：round、question、question_id、source。
#   等待：配合 status="needs_input" 和问题类型的 pending_input 使用。
#   示例：
#     {
#       "round": 1, "question": "什么是 RAG？",
#       "question_id": "question_001", "source": "rag.md"
#     }
#
# interview_report：结束后的面试复盘
#   必填：无；标题有默认值，其余字段默认空字符串或列表。
#   业务：有问答证据时应填 answers；空报告合法，不代表已完成有效面试。
#   示例：{}。
#
# chat：回答、澄清、提案说明或失败提示
#   必填：message。
#   提案：本体放 AgentResponse.pending_input，不放 data。
#   示例：{"message": "请补充办公地址"}。
#
# 校验与保存：
#   RunService 先校验 AgentResponse，再按这里校验 data，status 不会跳过字段校验。
#   整体响应还须能被 JSON 序列化，不能夹带 Path、文件对象、NaN 或无穷大。
#   仅评估/面试报告在 completed 或 partial 状态下由应用自动保存快照报告。
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


# ---------------------------------------------------------------------------
# 6. Agent 接收的命令与应用服务
# ---------------------------------------------------------------------------
#
# dataclass 只生成数据容器的初始化等方法，不像 Pydantic 那样自动校验字段内容。
@dataclass
class AgentCommand:
    """交给 Agent 的运行上下文；snapshot 保留发起时资料，human_input 首次为 None。"""

    # run_id：应用必传的运行 ID；无默认值，dataclass 不校验格式。
    #   示例："run_001"。
    run_id: str

    # action：应用必传的原始动作；无默认值，取值已由 RunStart 校验。
    #   示例："interview"。
    action: str

    # input：应用必传的初始任务参数；无默认值，恢复时仍是原始输入。
    #   示例：{"job_id": "job_001"}。
    input: dict

    # context：应用必传的关联上下文；无默认值，可为 {}。
    #   示例：{"type": "job", "id": "job_001"}。
    context: dict

    # snapshot：应用必传的发起时资料；无默认值。
    #   内容：profile / preferences / jobs，按动作再带 document 或关联对象。
    #   生命周期：确认提案后快照仍不更新，修改后的资料须通过 services 重新查询。
    #   示例：snapshot["profile"] 的内容（Python）：
    #     {
    #       "revision": 3, "confirmed": True,
    #       "data": {"skills": ["Python"]}
    #     }
    snapshot: dict

    # state：应用必传的上一轮 AgentResponse.state，初次为 {}。
    #   示例：{"round": 1}。
    #   修改这个副本不会自动保存；需要续接的内容须随本轮 AgentResponse.state 返回。
    state: dict

    # human_input：人工输入；可省略，默认 None；首次调用为 None。
    #   恢复结构：对应 RunInput；收到 decision="confirm" 时修改已保存，不能重复执行。
    #   示例：
    #     {
    #       "input_id": "input_001", "decision": "answer",
    #       "text": "我的回答", "data": {}
    #     }
    human_input: dict | None = None


@dataclass
class ApplicationServices:
    """交给 Agent 的应用存取入口；提供普通资料和文件访问，不负责 Agent 决策。"""

    # repository：名称保持接入兼容；repository 实际指向业务层 AgentDataService。
    #   应用必传的资料存取对象；无默认值，不是 SQLAlchemy Session。
    #   示例：repository.job("job_001")。
    repository: object

    # files：应用必传的资料文件服务；无默认值。
    #   示例：files.resolve("image_001") 返回受控的本地 Path。
    files: object
