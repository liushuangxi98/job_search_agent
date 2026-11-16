"""当前画像和求职条件接口。"""
from fastapi import APIRouter, Query

from ..schemas import CandidateUpdate, ProfileUpdate
from .dependencies import CandidateServiceDep

# FastAPI 按模型声明从 JSON 请求体生成 body 并校验字段，model_dump() 再将其转为服务接收的字典。
# 整份画像使用一个修订号，包含经历、能力和求职条件。
router = APIRouter(tags=['候选人'])


@router.get('/candidate')
def candidate(service: CandidateServiceDep):
    """同屏读取画像、条件及缺项，旧资料不完整时提示先完善。"""
    return service.get_all()


@router.put('/candidate')
def confirm_candidate(body: CandidateUpdate, service: CandidateServiceDep):
    """统一确认并保存个人资料，失败时不产生部分写入。"""
    return service.confirm(body.profile.model_dump(), body.revision)


@router.get('/profile')
def profile(service: CandidateServiceDep):
    """返回画像 data、修订号 revision 和确认状态 confirmed；记录缺失时返回 404。"""
    return service.get()


@router.delete('/candidate')
def delete_candidate(service: CandidateServiceDep, revision: int = Query(ge=0)):
    """按页面读取的版本删除画像，返回可重新填写的空资料。"""
    return service.delete(revision)


@router.put('/profile')
def update_profile(body: ProfileUpdate, service: CandidateServiceDep):
    """保存 body.data 和 body.confirmed，返回保存后的画像、修订号及确认状态。
    body.revision 必须等于当前修订号；冲突返回 409，尚未初始化资料返回 503。
    """
    return service.update(body.data.model_dump(), body.revision, body.confirmed)
