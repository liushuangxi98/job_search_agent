"""岗位池的增删改查接口。"""
from fastapi import APIRouter, Query, Response

from ..schemas import JobData, JobUpdate
from .dependencies import JobServiceDep

# q、offset、limit 取自 URL 查询字符串；Query 额外约束范围，非法值在进入接口函数前返回 422。
# JSON body 按 JobData/JobUpdate 校验；单条岗位响应含 id、revision、created_at、data。
router = APIRouter(prefix='/jobs', tags=['岗位'])


@router.get('')
def jobs(service: JobServiceDep, q: str = '', offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
    """按 q 搜索公司、岗位名称或城市，跳过 offset 条后最多取 limit 条，返回 items 和匹配总数 total。"""
    return service.list(q, offset, limit)


@router.post('', status_code=201)
def create_job(body: JobData, service: JobServiceDep):
    """按 JSON body 保存岗位，精确重复时复用岗位并合并来源，返回 201 和岗位对象。
    body.evidence 引用的截图必须存在，否则返回 404。
    """
    return service.save(body.model_dump())


@router.get('/{key}')
def get_job(key: str, service: JobServiceDep):
    """按路径中的岗位 ID 返回岗位对象；不存在时返回 404。"""
    return service.get(key)


@router.put('/{key}')
def update_job(key: str, body: JobUpdate, service: JobServiceDep):
    """用 body.data 替换路径中 key 对应的岗位及来源，返回更新后的岗位对象。
    body.revision 必须匹配当前修订号，否则返回 409；岗位或来源资料缺失时返回 404。
    """
    return service.save(body.data.model_dump(), key, body.revision)


@router.delete('/{key}', status_code=204)
def delete_job(key: str, service: JobServiceDep):
    """按路径中的岗位 ID 删除岗位及来源关联，返回无正文的 204；岗位不存在时返回 404。"""
    service.delete(key)
    return Response(status_code=204)
