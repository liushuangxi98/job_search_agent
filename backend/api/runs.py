"""运行状态与人工输入接口，运行流程由 RunService 管理。"""
from fastapi import APIRouter, Query, Response

from ..contracts import RunInput, RunStart
from .dependencies import KnowledgeServiceDep, RunServiceDep

# FastAPI 从 JSON 请求体校验 body，从路径读取 key；服务另建后台任务执行 Agent。
# 202 表示请求已受理，页面通过 GET 轮询结果；后台失败写入运行状态，不改变已发出的 HTTP 响应。
# 运行的 result、pending_input、report_id 尚未产生时为 None，返回到页面时是 JSON null。
router = APIRouter(prefix='/runs', tags=['Agent 运行'])


@router.post('', status_code=202)
async def start_run(body: RunStart, service: RunServiceDep):
    """按 body 的 action/input/context 创建运行和输入快照，返回 202 及当前运行数据。
    已有活动或所需画像未确认返回 409，Agent 未接入返回 503，输入无效或所引记录缺失返回 422/404。
    """
    # await 等待服务完成受理；服务已安排后台执行，因此这里无需等 Agent 生成最终结果。
    return await service.start(body)


@router.get('')
def current_run(service: RunServiceDep):
    """返回 active 对应的当前运行；没有运行中或等待输入的活动时，active 为 None（JSON null）。"""
    return service.current()


@router.get('/{key}')
def get_run(key: str, service: RunServiceDep, after: int = Query(0, ge=0)):
    """按路径中的运行 ID 查询状态，仅返回序号大于查询参数 after 的事件。
    响应含 id、status、events、next_seq、result、pending_input、report_id 等；运行丢失时返回 404 RUN_EXPIRED。
    """
    return service.get(key, after)


@router.post('/{key}/confirm-questions', status_code=204)
async def confirm_questions(key: str, service: RunServiceDep, knowledge: KnowledgeServiceDep):
    """确认本次 MD 导入产生的题目；题目内容由服务端从运行结果读取。"""
    await service.confirm_questions(key, knowledge)
    return Response(status_code=204)


@router.post('/{key}/input', status_code=202)
async def submit_input(key: str, body: RunInput, service: RunServiceDep):
    """以 key 定位运行、body.input_id 匹配待处理输入，提交回答或决定，返回 202 及运行数据。
    确认提案会先保存修改；运行丢失返回 404，输入失效或修订号冲突返回 409，决定不合法返回 422。
    """
    return await service.respond(key, body)
