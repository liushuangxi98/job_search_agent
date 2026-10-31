"""题目元数据和已确认记忆接口。"""
from fastapi import APIRouter, Query, Response

from ..schemas import MemoryData, QuestionData
from .dependencies import KnowledgeServiceDep

router = APIRouter(tags=['题库与记忆'])


@router.get('/knowledge/questions')
def questions(service: KnowledgeServiceDep, q: str = '', offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
    return service.list('questions', q, offset, limit)


@router.post('/knowledge/questions', status_code=201)
def create_question(body: QuestionData, service: KnowledgeServiceDep):
    return service.save('questions', body.model_dump())


@router.put('/knowledge/questions/{key}')
def update_question(key: str, body: QuestionData, service: KnowledgeServiceDep):
    return service.save('questions', body.model_dump(), key)


@router.delete('/knowledge/questions/{key}', status_code=204)
def delete_question(key: str, service: KnowledgeServiceDep):
    service.delete('questions', key)
    return Response(status_code=204)


@router.get('/memories')
def memories(service: KnowledgeServiceDep):
    return service.list('memories')


@router.post('/memories', status_code=201)
def create_memory(body: MemoryData, service: KnowledgeServiceDep):
    return service.save('memories', body.model_dump())


@router.put('/memories/{key}')
def update_memory(key: str, body: MemoryData, service: KnowledgeServiceDep):
    return service.save('memories', body.model_dump(), key)


@router.delete('/memories/{key}', status_code=204)
def delete_memory(key: str, service: KnowledgeServiceDep):
    service.delete('memories', key)
    return Response(status_code=204)
