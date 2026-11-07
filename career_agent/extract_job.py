"""
模块名称:  extract_job.py
所属项目:  job_search_agent
创建时间:  2026/11/6 22:51
作者:      刘双喜 <1604030114@qq.com>
功能描述:
"""
import json
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.text.paragraph import Paragraph
from pypdf import PdfReader
from pydantic import ValidationError

from .llm import build_chat
from .prompts import GEI_JOB_PROMPTS
from backend.contracts import AgentResponse, PendingInput
from backend.schemas import ProfileData, JobData
from backend.services.material_validation import check_dict_missing
from backend.exceptions import AppError
from .tools.local_ocr import ocr_image
from .tools.local_ocr_primary import ocr_primary


async def get_job(command, services, emit) -> AgentResponse:
    # 获取模型输入
    if command.input.get('document_id'):
        text_ocr = ocr_image(services.files.resolve(command.input["document_id"]))
        text_ocr_address = ocr_image(services.files.resolve(command.input["document_id"]), map_target_only=True)
        text = f"招聘信息：{text_ocr}。公司地址单独识别：{text_ocr_address}"
    else:
        text = command.human_input['text']
    messages = [
        {'role': 'system', 'content': GEI_JOB_PROMPTS},
        {'role': 'user', 'content': text},
        {'role': 'user', 'content': json.dumps(command.state, ensure_ascii=False, allow_nan=False)}
    ]
    messages.extend(command.history)
    if command.human_input.get('text'):
        messages.append({'role': 'user', 'content': command.human_input['text']})

    # 调用模型
    model = build_chat().with_structured_output(
        JobData, method='function_calling', include_raw=True, extra_body={'thinking': {'type': 'disabled'}}
    )

    # 获取模型输出
    result = await model.ainvoke(messages)
    decision = result['parsed']
    usage = result['raw'].usage_metadata

    # 检验
    if decision is None:
        return AgentResponse(
            status='failed', kind='chat',
            data={'message': '未能提取有效岗位信息。'}, usage=usage,
        )
    # 优先保留历史，然后是模型本次获取的信息。 核对信息。
    update = {**command.state, **decision.model_dump(exclude_unset=True)}
    try:
        update = JobData.model_validate(update).model_dump()
    except ValidationError:
        return AgentResponse(
            status='failed', kind='chat', data={'message': '补充内容与岗位字段约束冲突，请核对后重新发起。'}, usage=usage,
        )
    if decision.complete is None:
        return AgentResponse(
            status='failed', kind='chat',
            data={'code': 'JOB_TEXT_FOLDED', 'message': '岗位正文被折叠，请展开后重新截图。'},
            usage=usage,
        )

    # 返回
    miss = check_dict_missing(update, JobData)
    if miss:
        question = '还请补充：' + '、'.join([i['label'] for i in miss]) + '，可以一次或者多次补齐。'
        return AgentResponse(status='needs_input', kind='chat',
                             data={'message': question}, pending_input=dict(kind='question', prompt=question),
                             usage=usage, state=update
                             )
    else:
        return AgentResponse(status='completed', kind='job_drafts', data=update, usage=usage)
