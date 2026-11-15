"""
模块名称:  parse_resume.py
所属项目:  job_search_agent
创建时间:  2026/11/5 22:15
作者:      刘双喜 <1604030114@qq.com>
功能描述:
"""
import json
import traceback
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.text.paragraph import Paragraph
from pypdf import PdfReader
from pydantic import ValidationError

from .llm import build_chat
from .prompts import GEI_RESUME_PROMPTS
from backend.contracts import AgentResponse
from backend.schemas import ProfileData
from backend.services.material_validation import check_dict_missing
from backend.exceptions import AppError


async def get_resume(command, file_path, emit) -> AgentResponse:
    """用应用传入的快照和会话上下文回答，交回决策和服务商报告的用量。"""
    profile_base = command.state or command.snapshot['profile']['data']
    messages = [
        {'role': 'system', 'content': GEI_RESUME_PROMPTS},
        {'role': 'user', 'content': extract_resume_text(file_path.resolve(command.input["document_id"]))},
        {'role': 'user', 'content': json.dumps(profile_base, ensure_ascii=False, allow_nan=False)},
    ]
    messages.extend(command.history)
    if command.human_input.get('text'):
        messages.append({'role': 'user', 'content': command.human_input['text']})

    await emit('status', '正在更新简历信息')
    # DeepSeek 指定工具时需要关闭思考模式。工具参数承载决策，raw 保留用量。
    model = build_chat().with_structured_output(
        ProfileData, method='function_calling', include_raw=True,
        extra_body={'thinking': {'type': 'disabled'}},
    )
    result = await model.ainvoke(messages)
    usage = result['raw'].usage_metadata
    decision = result['parsed']

    # 无效决策也可能已计费，随失败结果返回服务商实际上报的用量。
    if decision is None:
        if result.get('parsing_error'):
            traceback.print_exception(result['parsing_error'])
        return AgentResponse(status='failed', kind='chat', data={'message': '未收到有效的聊天决策。'}, usage=usage)
    update = {**profile_base, **decision.model_dump(exclude_unset=True)}
    try:
        update = ProfileData.model_validate(update).model_dump()
    except ValidationError:
        traceback.print_exc()
        return AgentResponse(
            status='failed', kind='chat', data={'message': '补充内容与画像字段约束冲突，请核对后重新发起。'}, usage=usage,
        )
    missing = check_dict_missing(update, ProfileData)
    if missing:  # 简历需要补充
        question = '还需补充：' + '、'.join(item['label'] for item in missing) + '。可以一次补齐，也可以先回答其中几项。'
        ret = AgentResponse(
            status='needs_input', kind='chat', data={'message': question},
            pending_input=dict(kind='question', prompt=question), usage=usage, state=update
        )
        return ret
    else:
        return AgentResponse(status='completed', kind='profile_draft', data=update, usage=usage)

def extract_resume_text(path: Path) -> str:
    """读取已定位的 PDF/DOCX，返回带原文位置的非空正文；格式、加密或空正文错误抛 AppError。"""
    try:
        if path.suffix.lower() == '.pdf':
            text = _read_pdf(path)
        elif path.suffix.lower() == '.docx':
            text = _read_docx(path)
        else:
            raise AppError(422, 'RESUME_FORMAT', '简历仅支持 PDF 和 DOCX。')
    except AppError:
        raise
    except Exception as exc:
        # 第三方解析库面对损坏文件时异常类型不一，在文件读取边界给出统一提示。
        raise AppError(422, 'RESUME_READ_FAILED', '无法读取简历，请检查文件是否损坏或换成文本版 PDF/DOCX。') from exc
    if not text.strip():
        raise AppError(422, 'RESUME_NO_TEXT', '简历中没有可提取的文字；扫描 PDF 请先做 OCR 或换成文本版。')
    return text


def _read_pdf(path: Path) -> str:
    parts = []
    with path.open('rb') as stream:
        reader = PdfReader(stream)
        if reader.is_encrypted:
            raise AppError(422, 'RESUME_ENCRYPTED', '请先解密 PDF 再上传。')
        for page_number, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or '').strip()
            if text:
                parts.append(f'[第 {page_number} 页]\n{text}')
    return '\n\n'.join(parts)


def _read_docx(path: Path) -> str:
    parts = []
    # 段落和表格按文档顺序读取，位置标记可供模型填写 evidence.locator。
    for block_number, block in enumerate(Document(path).iter_inner_content(), 1):
        if isinstance(block, Paragraph):
            if block.text.strip():
                parts.append(f'[段落 {block_number}]\n{block.text.strip()}')
        else:
            for row_number, row in enumerate(block.rows, 1):
                text = ' | '.join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if text:
                    parts.append(f'[表格 {block_number}，第 {row_number} 行]\n{text}')
    return '\n\n'.join(parts)
