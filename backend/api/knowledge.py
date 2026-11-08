"""题目元数据和已确认记忆接口。"""
from fastapi import APIRouter, Query, Response

from ..schemas import MemoryData, QuestionData
from .dependencies import KnowledgeServiceDep

# JSON body 按题目或记忆模型校验，key 取自路径；单条响应含 id、data、created_at。
# 列表响应为 items/total；这些接口维护数据库记录，向量索引由 Agent 自行维护。
router = APIRouter(tags=['题库与记忆'])


@router.get('/knowledge/questions')
def questions(service: KnowledgeServiceDep, q: str = '', offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
    """按查询参数 q 搜索题干，以 offset/limit 分页，返回 items 和匹配总数 total。
    offset 为跳过的条数，limit 为本次最多条数；不满足 Query 范围约束时返回 422。
    """
    return service.list('questions', q, offset, limit)


@router.put('/knowledge/questions/{key}')
def update_question(key: str, body: QuestionData, service: KnowledgeServiceDep):
    """用 body 替换 key 对应的题目并返回记录对象；题目或指定的来源资料不存在时返回 404。"""
    return service.save('questions', body.model_dump(), key)


@router.delete('/knowledge/questions/{key}', status_code=204)
def delete_question(key: str, service: KnowledgeServiceDep):
    """删除 key 对应的题目记录，返回无正文的 204；不存在时返回 404。"""
    service.delete('questions', key)
    return Response(status_code=204)


@router.get('/memories')
def memories(service: KnowledgeServiceDep):
    """返回最近最多 100 条已确认记忆及全部记忆总数，结构为 items/total。"""
    return service.list('memories')


@router.post('/memories', status_code=201)
def create_memory(body: MemoryData, service: KnowledgeServiceDep):
    """保存 body 中的记忆内容、类别和依据，返回 201 和记录对象。"""
    return service.save('memories', body.model_dump())


@router.put('/memories/{key}')
def update_memory(key: str, body: MemoryData, service: KnowledgeServiceDep):
    """用 body 替换 key 对应的记忆并返回记录对象；记忆不存在时返回 404。"""
    return service.save('memories', body.model_dump(), key)


@router.delete('/memories/{key}', status_code=204)
def delete_memory(key: str, service: KnowledgeServiceDep):
    """删除 key 对应的记忆记录，返回无正文的 204；不存在时返回 404。"""
    service.delete('memories', key)
    return Response(status_code=204)
