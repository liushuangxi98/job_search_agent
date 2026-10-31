"""运行状态与人工输入接口，运行流程由 RunService 管理。"""
from fastapi import APIRouter, Query

from ..contracts import RunInput, RunStart
from .dependencies import RunServiceDep

router = APIRouter(prefix='/runs', tags=['Agent 运行'])


@router.post('', status_code=202)
async def start_run(body: RunStart, service: RunServiceDep):
    return await service.start(body)


@router.get('')
def current_run(service: RunServiceDep):
    return service.current()


@router.get('/{key}')
def get_run(key: str, service: RunServiceDep, after: int = Query(0, ge=0)):
    return service.get(key, after)


@router.post('/{key}/input', status_code=202)
async def submit_input(key: str, body: RunInput, service: RunServiceDep):
    return await service.respond(key, body)
