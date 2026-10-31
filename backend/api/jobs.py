"""岗位池的增删改查接口。"""
from fastapi import APIRouter, Query, Response

from ..schemas import JobData, JobUpdate
from .dependencies import JobServiceDep

router = APIRouter(prefix='/jobs', tags=['岗位'])


@router.get('')
def jobs(service: JobServiceDep, q: str = '', offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
    return service.list(q, offset, limit)


@router.post('', status_code=201)
def create_job(body: JobData, service: JobServiceDep):
    return service.save(body.model_dump())


@router.get('/{key}')
def get_job(key: str, service: JobServiceDep):
    return service.get(key)


@router.put('/{key}')
def update_job(key: str, body: JobUpdate, service: JobServiceDep):
    return service.save(body.data.model_dump(), key, body.revision)


@router.delete('/{key}', status_code=204)
def delete_job(key: str, service: JobServiceDep):
    service.delete(key)
    return Response(status_code=204)
