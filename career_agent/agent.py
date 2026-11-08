"""Agent 唯一入口：分发已实现动作，统一返回后端契约。"""
from backend.contracts import AgentCommand, AgentResponse, ApplicationServices
from .chat import reply
from .parse_resume import get_resume
from .extract_job import get_job
from .evaluate import evaluate


async def handle(command: AgentCommand, services: ApplicationServices, emit) -> AgentResponse:
    """接收一次运行或人工续接；尚未实现的动作明确失败。"""
    if command.action == 'chat':
        return await reply(command, emit)
    elif command.action == 'parse_resume':
        return await get_resume(command, services.files, emit)
    elif command.action == 'extract_jobs':
        if command.human_input['decision'] == 'start':
            services.clear_history(command)
        return await get_job(command, services, emit)
    elif command.action == 'evaluate':
        return await evaluate(command, emit)
    return AgentResponse(
        status='failed', kind='chat',
        data={'message': f'动作 {command.action} 尚未实现。'},
    )

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
# evaluate：岗位评估
#   场景：用户在岗位行评估匹配度与通勤；发起前须确认完整个人资料和岗位信息盲区。
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


# ---------------------------------------------------------------------------
# 5. kind 速查与结果校验
# ---------------------------------------------------------------------------
#
# 通用规则：
#   kind 决定 data 的校验模型，字段细节见上方对应模型。
#   报告和聊天结果支持附加展示字段。
#   下面的最小示例只保证结构合法，不代表业务任务已充分完成。
#
# profile_draft：简历解析后的画像草稿
#   data 直接是 ProfileData，包含个人经历和求职条件。
#   保存：解析/诊断后仍是草稿，个人必填项完整才能统一确认，不能标成信息盲区。
#   示例：{"name": "王某", "skills": ["Python"]}。
#
# job_drafts：待导入的岗位草稿
#   data 直接是一个 JobData 字典；等待用户预览保存。
#   示例：{"title": "Agent 开发工程师"}。
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
#
# RESULT_SCHEMAS = {
#     'profile_draft': ProfileData, 'job_drafts': JobData,
#     'evaluation_report': EvaluationReportData,
#     'interview_question': InterviewQuestionData, 'interview_report': InterviewReportData, 'chat': ChatData,
# }
