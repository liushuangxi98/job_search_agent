"""已确认记忆接口。"""
from fastapi import APIRouter, Response

from ..schemas import MemoryData
from .dependencies import MemoryServiceDep

router = APIRouter(tags=['记忆'])


@router.get('/memories')
def memories(service: MemoryServiceDep):
    """返回最近最多 100 条已确认记忆及全部记忆总数，结构为 items/total。"""
    return service.list()


@router.post('/memories', status_code=201)
def create_memory(body: MemoryData, service: MemoryServiceDep):
    """保存 body 中的记忆内容、类别和依据，返回 201 和记录对象。"""
    return service.save(body.model_dump())


@router.put('/memories/{key}')
def update_memory(key: str, body: MemoryData, service: MemoryServiceDep):
    """用 body 替换 key 对应的记忆并返回记录对象；记忆不存在时返回 404。"""
    return service.save(body.model_dump(), key)


@router.delete('/memories/{key}', status_code=204)
def delete_memory(key: str, service: MemoryServiceDep):
    """删除 key 对应的记忆记录，返回无正文的 204；不存在时返回 404。"""
    service.delete(key)
    return Response(status_code=204)
