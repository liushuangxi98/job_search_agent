"""当前画像和求职条件接口。"""
from fastapi import APIRouter

from ..schemas import PreferencesUpdate, ProfileUpdate
from .dependencies import CandidateServiceDep

# FastAPI 按模型声明从 JSON 请求体生成 body 并校验字段，model_dump() 再将其转为服务接收的字典。
# 画像和条件共用修订号；任一保存后，另一个页面持有的旧修订号也会失效。
router = APIRouter(tags=['候选人'])


@router.get('/profile')
def profile(service: CandidateServiceDep):
    """返回画像 data、修订号 revision 和确认状态 confirmed；记录缺失时返回 404。"""
    return service.get('profile')


@router.put('/profile')
def update_profile(body: ProfileUpdate, service: CandidateServiceDep):
    """保存 body.data 和 body.confirmed，返回保存后的画像、修订号及确认状态。
    body.revision 必须等于当前修订号；冲突返回 409，尚未初始化资料返回 503。
    """
    return service.update('profile', body.data.model_dump(), body.revision, body.confirmed)


@router.get('/preferences')
def preferences(service: CandidateServiceDep):
    """返回条件 data、共享修订号 revision 和画像确认状态 confirmed；记录缺失时返回 404。"""
    return service.get('preferences')


@router.put('/preferences')
def update_preferences(body: PreferencesUpdate, service: CandidateServiceDep):
    """保存 body.data 中的条件，返回 data、递增后的 revision 和画像确认状态 confirmed。
    body.revision 用于防止覆盖新资料；冲突返回 409，尚未初始化资料返回 503。
    """
    return service.update('preferences', body.data.model_dump(), body.revision)
