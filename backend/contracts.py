"""应用与 Agent 的数据契约；只定义输入输出，不包含提示词、推理或算法。"""
from dataclasses import dataclass, field
from typing import Callable, Literal

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
#     展示模型允许附加字段；业务模型 ProfileData、JobData、QuestionData 按各自定义校验。
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
#   返回：profile_draft，直接交付提取的画像及求职条件；用户完整确认后统一保存。
#
# extract_jobs：招聘信息提取
#   场景：用户上传招聘截图，或粘贴招聘原文。
#   请求：input.document_id 引用 image 资料，或 input.text 提供原文。
#   Agent 从 command.human_input["text"] 读取招聘原文。
#   返回：job_drafts，供用户编辑后入库。
#
# index_knowledge：题库导入与索引
#   场景：用户导入本地 MD 题库。
#   输入：input.document_id 引用 knowledge 资料。
#   返回：knowledge_result，展示处理摘要。
#   业务约定：Agent 自行保存元数据和索引，不能仅靠返回值完成入库。
#
# evaluate：岗位评估
#   场景：用户在岗位行评估匹配度与通勤；发起前须确认完整个人资料；岗位未披露字段允许为空。
#   输入：input.job_ids 只传当前一个岗位的 ID。
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
#   请求：input.text 为消息，context 标识关联对象；Agent 从 human_input["text"] 读消息。
#   回答：chat；提出资料修改时使用 chat + needs_input。
#   确认：应用处理聊天提案并给出回执；仅补充问题的回答会续接聊天 Agent。
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
    action: Action = Field(title='任务动作')

    # context：页面关联上下文；可省略，默认 {}，未固定内部 key。
    #   示例：{"type": "job", "id": "job_001"}。
    context: dict = Field(title='页面上下文', default_factory=dict)

    # input：动作参数；可省略，默认 {}，业务前提见上方 action 说明。
    #   示例：{"job_ids": ["job_001"]}。
    input: dict = Field(title='任务参数', default_factory=dict)


class RunInput(Payload):
    """恢复运行的人工输入；input_id 必须匹配当前待处理输入，防止重复确认。"""

    # input_id：应用返回的 pending_input.id；必填字符串，服务检查是否仍有效。
    #   示例："input_001"。
    input_id: str = Field(title='待处理输入ID')

    # decision：用户决定；必填。
    #   取值：answer 回答、confirm 确认提案、reject 拒绝提案、finish 结束。
    #   匹配规则：
    #     proposal：仅接受 confirm / reject。
    #     question：仅接受 answer / finish，answer 须有非空 text 或 data。
    #   结束方式：面试 finish 续接 Agent 生成报告，其他动作直接结束，不再调用 Agent。
    #   示例："answer"。
    decision: Literal['answer', 'confirm', 'reject', 'finish'] = Field(title='用户决定')

    # text：用户文字；可省略，默认 ""，最多 20000 字符。
    #   示例："办公地址是杭州西湖区……"。
    text: str = Field(title='补充文字', default='', max_length=20000)

    # data：结构化补充；可省略，默认 {}，内部格式由 Agent 与界面约定。
    #   示例：{"city": "杭州"}。
    data: dict = Field(title='结构化补充', default_factory=dict)


# ---------------------------------------------------------------------------
# 2. Agent 返回结构：状态、结果类别、展示数据和续接状态
# ---------------------------------------------------------------------------


class ChatSummary(Payload):
    """text 是 Agent 生成的摘要，covered_messages 是它覆盖的原始消息条数。"""
    text: str = Field(title='历史摘要', min_length=1)
    covered_messages: int = Field(title='已覆盖消息数', gt=0)


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
    status: Literal['needs_input', 'completed', 'partial', 'failed'] = Field(default='completed', title='运行状态')

    # kind：结果类别；必填，限下方 7 个值；对应模型、业务场景与必填 key 见 RESULT_SCHEMAS 上方。
    #   示例："evaluation_report"；"error" 是运行服务生成的内部结果类型，Agent 不能返回它。
    kind: Literal['profile_draft', 'job_drafts', 'knowledge_result', 'evaluation_report',
                  'interview_question', 'interview_report', 'chat'] = Field(title='结果类别')

    # data：展示数据；外层可省略，默认 {}，但随后仍须通过 kind 对应的模型校验。
    #   示例：kind="chat" 时填 {"message": "请补充工作地点"}，不能把 AgentResponse 再嵌入 data。
    data: dict = Field(title='结果数据', default_factory=dict)

    # pending_input：待用户回答或确认的结构；可省略，默认 None。
    #   基础校验：所有状态都要求 dict | None，且整体响应可被 JSON 序列化。
    #   内容校验：仅 needs_input 时按 PendingInput 校验，并创建待处理输入。
    #   其他状态：不会创建待处理输入。
    #   示例：{"kind": "question", "prompt": "工作地点在哪里？"}。
    pending_input: dict | None = Field(default=None, title='待处理输入')

    # state：Agent 下一轮需要的状态；可省略，默认 {}。
    #   示例：{"round": 1, "answers": []}。
    #   应用仅在内存中保存并通过 command.state 交回；本字段不是数据库持久化或完整聊天记忆。
    state: dict = Field(title='续接状态', default_factory=dict)

    # 本次模型响应的 usage_metadata：输入、输出、总 token 数及可选明细。
    # 已调用但未上报则传 None；本轮没有模型调用则省略此字段。
    usage: dict | None = Field(default=None, title='模型用量')

    # 成功后用此摘要更新会话；None 表示沿用已有摘要，原文始终保留。
    summary: ChatSummary | None = Field(default=None, title='历史摘要')


# ---------------------------------------------------------------------------
# 3. 结果数据模型：data 的字段与嵌套结构
# ---------------------------------------------------------------------------


# 3.1 ReportJob：评估报告中的单个岗位
class RequirementMatch(Payload):
    """把岗位要求对应到个人经历证据；缺少依据时标为“资料不足”。"""
    # requirement：岗位的一项要求；必填非空字符串；示例："具备 Python 开发经验"。
    requirement: str = Field(title='岗位要求', min_length=1, description='从本岗位 JD 提取的一项具体要求。')

    # evidence：该要求对应的经历依据；默认 ""，无长度上限；示例："维护 Python 接口平台"。
    evidence: str = Field(title='匹配依据', default='', description='画像中支持本项判断的经历或技能依据；缺少依据时留空，不编造经历。')

    # assessment：有明确依据时判断符合程度，证据不足时保留“资料不足”。
    #   默认“资料不足”；示例：“部分符合”，不要把缺少证据直接断言为没有能力。
    assessment: Literal['符合', '部分符合', '不符合', '资料不足'] = Field(title='匹配判断', default='资料不足', description='有明确差距时填“不符合”；缺少判断依据时填“资料不足”。')


class JobMatchAnalysis(BaseModel):
    """模型根据一个岗位和候选人画像填写的分析。"""
    # matches：要求与经历的对应分析；默认 []，每项为 RequirementMatch。
    #   示例：[{"requirement": "Python", "evidence": "接口平台项目", "assessment": "符合"}]。
    matches: list[RequirementMatch] = Field(title='逐项匹配', default_factory=list, description='逐项对应 JD 要求与候选人经历的分析列表；各项依据须属于当前候选人和岗位。')

    supporting_experiences: str = Field(title='最有利的经历', default='',
        description='只写对投递非常有利的具体经历及其满足的要求。每条独占一行，从 1. 开始编号，没有则填空字符串。')
    key_requirement: str = Field(title='最不利的关键要求', default='',
        description='用一段话说明最影响投递的一条重要要求，以及明确不满足它的依据。没有则填空字符串。')

    # recommendation：针对本岗位的建议；默认 ""，无长度上限；示例："先确认地点再决定是否投递"。
    recommendation: str = Field(title='求职建议', default='', description='针对本岗位的求职建议，结合匹配依据和已知限制。')

    # evidence：附加引用依据；默认 []，元素结构不固定，须可 JSON 序列化。
    #   示例：[{"source": "JD", "text": "要求 Python 开发经验"}]。
    evidence: list[dict[str, str]] = Field(title='补充依据', default_factory=list,
        description='引用本岗位或画像中实际用于判断的原文，每项填写 source（来源）和 text（原文）。')


class ReportJob(JobMatchAnalysis):
    """单岗结果；程序根据岗位快照关联 ID，计算匹配分并填写通勤。"""
    job_id: str = Field(title='岗位ID', description='本次快照中被评估的岗位 ID。')
    match_score: float | None = Field(title='匹配分', default=None, ge=0, le=100, allow_inf_nan=False,
        description='已确认材料对岗位必备要求的覆盖分，无法评分时为 null。')

    # commutes：默认 {}，public_transit / driving 分别对应公交、驾车，值按 CommuteResult 校验。
    #   示例：{"driving": {"duration_minutes": 35, "distance_km": 12, "route_summary": "经城市道路到园区"}}。
    commutes: dict[str, 'CommuteResult'] = Field(title='通勤结果', default_factory=dict, description='单程通勤结果，键使用 public_transit（公共交通）和 driving（驾车）；数值依据实际地图查询结果。')


# 3.2 CommuteResult：单程路线的时长与距离
class CommuteResult(BaseModel):
    """单程通勤结果；时长为分钟、距离为公里，None 表示未知而非零耗时或零距离。"""
    model_config = {'extra': 'allow'}

    # duration_minutes：单程时长，单位分钟；可省略，默认 None；非负有限数值，未知不填 0。
    #   示例：45。
    duration_minutes: float | None = Field(title='单程时间（分钟）', default=None, ge=0, allow_inf_nan=False, description='单程通勤时长，单位分钟；未知或查询失败时为 null，不填 0。')

    # distance_km：单程距离，单位公里；可省略，默认 None；非负有限数值。
    #   示例：18.2。
    distance_km: float | None = Field(title='单程距离（公里）', default=None, ge=0, allow_inf_nan=False, description='单程通勤距离，单位公里；未知或查询失败时为 null。')
    # route_summary：主要线路或路径；默认 ""，无长度上限；未知时空串，不编造路线。
    #   示例："步行到地铁站 → 地铁 1 号线 → 步行到公司"。
    route_summary: str = Field(title='主要路线', default='', description='实际查询得到的主要线路或路径说明；未知时留空，不编造路线。')


# 3.3 DisplayData：7 种结果共用的展示字段
class DisplayData(BaseModel):
    """允许 Agent 保留附加展示字段，子类只校验界面必需的已声明字段。"""
    model_config = {'extra': 'allow'}

    # notice：展示结果可继承此提示字段；可省略，默认 ""，无长度限制。
    #   示例："部分信息缺失，请核对后再保存。"；不是 status，也不负责触发确认流程。
    notice: str = Field(title='结果提示', default='', description='给用户的结果提示，例如资料缺失或固定样例说明。')


# 3.6 KnowledgeResultData：题库导入与索引摘要
class KnowledgeResultData(DisplayData):
    """题库处理摘要；返回题目列表不代表应用已经替 Agent 保存元数据或向量。"""

    # questions：导入后展示的题目；可省略，默认 []，每项为 schemas.QuestionData，列表不限条数。
    #   示例：[{"question": "什么是 RAG？", "tags": ["RAG"]}]；真实入库由 Agent 另外执行。
    questions: list[QuestionData] = Field(title='题目列表', default_factory=list, description='本次题库处理得到的题目列表，包含题干、答案及来源；返回列表本身不代表元数据或向量已保存。')

    # coverage：题库覆盖的知识点名称；可省略，默认 []，不限条数的字符串列表，不是覆盖率数值。
    #   示例：["RAG", "工具调用"]。
    coverage: list[str] = Field(title='覆盖知识点', default_factory=list, description='本次题库覆盖的知识点名称列表，例如 RAG、工具调用；不是覆盖率分数。')


# 3.7 EvaluationReportData：岗位评估报告
class EvaluationReportData(DisplayData):

    # title：报告标题；可省略，默认 "岗位评估报告"，无长度限制。
    #   示例："Agent 开发岗位分析"。
    title: str = Field(title='评估报告标题', default='岗位评估报告', description='岗位评估报告的展示标题。')

    # summary：匹配及通勤摘要；可省略，默认 ""，无长度限制，不给出跨岗位排名。
    #   示例："经历与岗位部分匹配，具体办公地址尚未披露。"。
    summary: str = Field(title='评估概述', default='', description='本次岗位匹配与通勤结果的总体说明，不给出跨岗位综合排名。')

    # jobs：每岗评估结果；必填列表，每项为 ReportJob，允许 []，不强制核对与输入岗位的对应关系。
    #   示例：[{"job_id": "job_001", "company": "星河科技", "title": "Agent 开发"}]。
    jobs: list[ReportJob] = Field(title='岗位评估结果', description='本次选中岗位的逐项评估结果，每项须关联对应的真实岗位 ID。')


# 3.8 InterviewQuestionData：当前一轮面试问题
class InterviewQuestionData(DisplayData):
    """当前面试问题；round 只允许 1—8，来源信息随问题展示。
    总轮数含追问不超过 8 的约定，由 Agent 负责计数和结束。
    """

    # round：当前轮号；必填整数，范围 1—8。
    #   示例：1。业务上追问也计一轮，由 Agent 累计。
    round: int = Field(title='面试轮次', ge=1, le=8, description='本场面试当前轮号，范围 1—8；主问题和追问均占一轮。')

    # question：本轮题干；必填字符串，至少 1 字符，无长度上限。
    #   示例："如何处理工具请求超时？"。
    question: str = Field(title='本轮题目', min_length=1, description='本轮实际展示给用户的完整面试题或追问题干。')

    # question_id：题目或追问标识；必填字符串，但未禁止空串或校验数据库存在性。
    #   示例："question_001"。
    question_id: str = Field(title='题目标识', description='当前题目的 ID；题库题使用检索到的 ID，自拟追问使用可区分的标识，不伪造题库记录。')

    # source：供用户核对的来源说明；必填字符串，但未禁止空串或限制长度。
    #   示例："agent题库.md#工具超时"。
    source: str = Field(title='题目来源', description='题目来源及位置；自拟追问须明确标注生成性质，不将推测考点冒充公司真实面经。')


# 3.9 DimensionData / AnswerData：面试评分维度与问答记录
class DimensionData(BaseModel):
    """面试维度及百分制得分；score 为 None 表示该维度没有可用评分。"""
    model_config = {'extra': 'allow'}

    # name：评分维度名称；必填字符串，未设长度约束。
    #   示例："工具调用与异常处理"。
    name: str = Field(title='评价维度', description='被评价的面试能力维度名称。')

    # score：维度得分；可省略，默认 None；有限数值 0—100，None 表示未考察或无法判断。
    #   示例：75。
    score: float | None = Field(title='维度得分', default=None, ge=0, le=100, allow_inf_nan=False, description='该维度的百分制得分，范围 0—100；未考察或无法判断时为 null。')


class AnswerData(BaseModel):
    model_config = {'extra': 'allow'}

    # question：当轮实际题干；必填字符串，未设长度约束或禁止空串。
    #   示例："为什么需要重排？"。
    question: str = Field(title='实际题干', description='用户当轮实际回答的题干。')

    # answer：用户当轮作答原文；必填字符串，未设长度约束，允许 ""。
    #   示例："重排用于提高候选相关性。"。
    answer: str = Field(title='用户回答', description='用户当轮作答的原文，保留原意，不补写用户未说的内容。')

    # question_id：与出题时对应的题目 ID；可省略，默认 ""，无格式校验。
    #   示例："question_001"。
    question_id: str = Field(title='题目标识', default='', description='与该次作答对应的题目 ID；无记录时留空。')

    # source：本题来源位置或说明；可省略，默认 ""，无长度约束。
    #   示例："rag题库.md#重排"。
    source: str = Field(title='题目来源', default='', description='该题的来源位置或自拟追问说明；无记录时留空。')


# 3.10 InterviewReportData：面试结束后的复盘报告
class InterviewReportData(DisplayData):

    # title：报告标题；可省略，默认 "面试报告"，无长度限制。
    #   示例："Agent 开发面试复盘"。
    title: str = Field(title='面试报告标题', default='面试报告', description='本场模拟面试复盘报告的展示标题。')

    # summary：整体表现摘要；可省略，默认 ""，无长度限制。
    #   示例："工具调用基础清晰，检索评测需加强。"。
    summary: str = Field(title='表现概述', default='', description='基于本场实际问答的整体表现说明。')

    # dimensions：各维度得分；可省略，默认 []，每项为 DimensionData，不限条数。
    #   示例：[{"name": "工具调用", "score": 75}]。
    dimensions: list[DimensionData] = Field(title='维度评价', default_factory=list, description='本场各能力维度的评价；只按实际回答评分，未考察的维度得分为 null。')

    # answers：已完成的问答记录；可省略，默认 []，每项为 AnswerData，模型未限制记录数量。
    #   示例：[{"question": "什么是 RAG？", "answer": "检索增强生成。", "question_id": "question_001"}]。
    answers: list[AnswerData] = Field(title='问答记录', default_factory=list, description='本场已完成的问答记录；尚未回答的题目和结束指令不作为已作答内容。')

    # weaknesses：薄弱项说明；可省略，默认 []，不限条数的字符串列表。
    #   示例：["缺少检索效果评估方法"]。
    weaknesses: list[str] = Field(title='薄弱项', default_factory=list, description='根据本场回答识别的薄弱项，每项应能对应实际答题表现。')

    # practice：后续练习建议；可省略，默认 []，不限条数的字符串列表。
    #   示例：["用固定题集比较召回策略"]。
    practice: list[str] = Field(title='练习建议', default_factory=list, description='针对已识别薄弱项的后续练习建议。')


# 3.11 ChatData：聊天、澄清或失败说明
class ChatData(DisplayData):

    # message：展示给用户的回答、澄清或失败说明；必填字符串，未设长度约束或禁止空串。
    #   示例："请补充公司工作地点，以便计算通勤。"。
    message: str = Field(title='回复内容', description='向用户展示的回答、补充问题或失败原因；具体修改内容通过 pending_input 中的提案传递。')


# ---------------------------------------------------------------------------
# 4. 人工交互：资料修改提案与待处理输入
# ---------------------------------------------------------------------------


class ChangeProposal(Payload):
    """待用户确认的局部修改；revision 是读取时的修订号，不是预期更新后的值。"""

    # target：要修改的对象；必填，限 profile（包含求职条件的画像）、job（岗位）。
    #   示例："profile"。
    target: Literal['profile', 'job'] = Field(title='修改对象', description='修改个人画像或求职条件选 profile，修改当前岗位选 job。')

    # target_id：对象 ID；可省略、默认 None，但 target="job" 时必须为非空字符串。
    #   示例："job_001"。
    #   画像、条件是单用户当前记录，不需要此 ID；该字段未设置长度上限。
    target_id: str | None = Field(title='目标记录ID', default=None, description='job 必填快照中当前岗位的 id；profile 省略或填 null。')

    # revision：读取目标记录时的修订号；必填非负整数。
    #   示例：3。应用保存时还会检查是否已过期。
    revision: int = Field(title='当前修订号', ge=0, description='复制快照中目标记录当前的 revision，不能自行递增。')

    # changes：局部修改字段，不是完整资料；必填非空字典，至少 1 项。
    #   示例：{"city": "杭州"}。
    #   应用按 target 检查允许字段与合并后的值，岗位提案不能改 evidence；这里只检查字典非空。
    changes: dict = Field(title='待修改字段', min_length=1, description='只填写要修改的业务字段及新值；画像和岗位的提取依据 evidence 均为只读。')

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
    kind: Literal['question', 'proposal'] = Field(title='输入类别', description='等待输入的类别：question 请求补充回答，proposal 请求确认或拒绝资料修改。')

    # prompt：展示给用户的问题或确认提示；必填字符串，去首尾空白后至少 1 字符，无上限。
    #   示例："是否把期望城市改为杭州？"。
    prompt: str = Field(title='提示文字', min_length=1, description='实际展示给用户的问题或修改确认提示，须清楚说明需要补充或确认的内容。')

    # proposal：修改内容；可省略、默认 None，但 kind="proposal" 时必须给出 ChangeProposal。
    #   示例：{"target": "profile", "revision": 3, "changes": {"city": "杭州"}}。
    proposal: ChangeProposal | None = Field(title='修改提案', default=None, description='待确认的结构化修改内容；kind 为 proposal 时必须提供，普通补充问题可省略或为 null。')

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
#   简历和岗位草稿直接使用业务模型；其余展示对象可带 notice。
#   下面的最小示例只保证结构合法，不代表业务任务已充分完成。
#
# profile_draft：简历解析后的画像草稿
#   data 直接是 ProfileData，包含个人经历和求职条件。
#   保存：解析/诊断后仍是草稿，个人必填项完整才能统一确认，不能标成信息盲区。
#   示例：{"name": "王某", "skills": ["Python"]}。
#
# job_drafts：待导入的岗位草稿
#   一次运行交付一个 JobData 字典，等待用户预览保存。
#   示例：{"title": "Agent 开发工程师"}。
#
# knowledge_result：题库导入或索引摘要
#   必填：无；questions、coverage 默认 []。
#   保存：题目元数据和向量索引仍由 Agent 实际写入。
#   示例：{}。
#
# evaluation_report：岗位评估报告
#   必填：jobs；每项通过 job_id 关联本次快照。
#   示例：
#     {"jobs": [
#       {"job_id": "job_001"}
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
    'profile_draft': ProfileData, 'job_drafts': JobData,
    'knowledge_result': KnowledgeResultData, 'evaluation_report': EvaluationReportData,
    'interview_question': InterviewQuestionData, 'interview_report': InterviewReportData,
    'chat': ChatData,
}


def validate_result(kind, data):
    """按结果类别校验数据，返回可展示的业务字典。"""
    return RESULT_SCHEMAS[kind].model_validate(data).model_dump()


# ---------------------------------------------------------------------------
# 6. Agent 接收的命令与应用服务
# ---------------------------------------------------------------------------
#
# dataclass 只生成数据容器的初始化等方法，不像 Pydantic 那样自动校验字段内容。
@dataclass
class AgentCommand:
    """交给 Agent 的运行上下文；snapshot 保留发起时资料，human_input 表示本轮输入。"""

    # run_id：应用必传的运行 ID；无默认值，dataclass 不校验格式。
    #   示例："run_001"。
    run_id: str

    # action：应用必传的原始动作；无默认值，取值已由 RunStart 校验。
    #   示例："interview"。
    action: str

    # input：初始任务参数；恢复时仍保留文件、岗位 ID 等参数，用户文字统一放入 human_input。
    #   示例：{"job_id": "job_001"}。
    input: dict

    # context：应用必传的关联上下文；无默认值，可为 {}。
    #   示例：{"type": "job", "id": "job_001"}。
    context: dict

    # snapshot：应用必传的发起时资料；无默认值。
    #   内容：profile / jobs，按动作再带 document 或关联对象。
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

    # human_input：应用必传的本轮输入字典。
    #   decision：首次为 start，续接为 answer / confirm / reject / finish。
    #   text：模型和历史共用的输入文字；仅上传文件或选岗位时为 ""。
    #   data：原始结构化补充，默认 {}；非空时应用同时将它附在 text 中。
    #   input_id：首次为 None，续接使用已核对的一次性输入 ID。
    #   收到 decision="confirm" 时修改已保存，不能重复执行。
    #   示例：
    #     {
    #       "input_id": "input_001", "decision": "answer",
    #       "text": "我的回答", "data": {}
    #     }
    human_input: dict

    # 同一会话、同一 action 的历史副本，仅含 role（user / assistant）和 content。
    # 所有动作均提供；本轮用户文字另外通过 human_input 传递。
    history: list[dict[str, str]] = field(default_factory=list)
    summary: ChatSummary | None = None

    # 上一次模型响应的用量；首次调用或上次未上报时为 None。
    previous_usage: dict | None = None

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

    # 同步调用 services.clear_history(command)，清空本分组及本次命令的历史和摘要。
    clear_history: Callable[[AgentCommand], None]
