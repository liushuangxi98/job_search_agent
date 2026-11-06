"""有上下文的聊天、澄清和资料修改提案；资料写入由后端确认流程负责。"""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.contracts import AgentResponse, ChangeProposal, PendingInput
from . import llm
from .prompts import CHAT_PROMPTS


class ChatDecision(BaseModel):
    """向用户回答、请求补充，或提出一项待确认的资料修改。"""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    msg_kind: Literal['reply', 'question', 'proposal'] = Field(
        description='reply：直接回答；question：缺信息需用户补充；proposal：修改内容已明确，交给用户确认。',
    )
    message: str = Field(
        min_length=1, description='唯一的展示文字；question 时写完整问题，proposal 时说明待确认的修改。',
    )
    proposal: ChangeProposal | None = Field(
        default=None, description='仅 proposal 类别填写结构化修改数据，其他类别为 null。',
    )

    @model_validator(mode='after')
    def check_proposal(self):
        """只有修改提案需要结构化修改内容，普通回答和补充问题都不能携带它。"""
        if (self.msg_kind == 'proposal') != (self.proposal is not None):
            raise ValueError('只有 msg_kind 为 proposal 时才必须填写 proposal。')
        return self


async def reply(command, emit) -> AgentResponse:
    """用应用传入的快照和会话上下文回答，交回决策和服务商报告的用量。"""
    messages = [
        {'role': 'system', 'content': CHAT_PROMPTS},
        {'role': 'user', 'content': json.dumps(command.snapshot, ensure_ascii=False, allow_nan=False)},
    ]
    if command.summary:
        messages.append({'role': 'assistant', 'content': '较早对话摘要：' + command.summary.text})
        messages.extend(command.history[command.summary.covered_messages:])
    else:
        messages.extend(command.history)
    messages.append({'role': 'user', 'content': command.human_input['text']})

    await emit('status', '正在结合当前资料生成回答')
    # DeepSeek 指定工具时需要关闭思考模式。工具参数承载决策，raw 保留用量。
    model = llm.build_chat().with_structured_output(
        ChatDecision, method='function_calling', include_raw=True,
        extra_body={'thinking': {'type': 'disabled'}},
    )
    result = await model.ainvoke(messages)
    usage = result['raw'].usage_metadata
    # 无效决策也可能已计费，随失败结果返回服务商实际上报的用量。
    decision = result['parsed']
    if decision is None:
        return AgentResponse(status='failed', kind='chat', data={'message': '未收到有效的聊天决策。'}, usage=usage)
    return _build_response(decision, usage)


def _build_response(decision: ChatDecision, usage: dict | None) -> AgentResponse:
    """把模型决策转换为普通回答、补充问题或修改提案；只组装结果，不执行修改。"""
    if decision.msg_kind == 'reply':
        return AgentResponse(
            status='completed', kind='chat', data={'message': decision.message}, usage=usage,
        )
    elif decision.msg_kind == 'question':
        return AgentResponse(
            status='needs_input', kind='chat', data={'message': decision.message}, usage=usage,
            pending_input=PendingInput(kind='question', prompt=decision.message).model_dump(),

        )
    else:
        return AgentResponse(
            status='needs_input', kind='chat', data={'message': decision.message}, usage=usage,
            pending_input=PendingInput(kind='proposal', prompt=decision.message, proposal=decision.proposal).model_dump())

