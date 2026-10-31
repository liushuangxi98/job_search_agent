"""当前画像和求职条件接口。"""
from fastapi import APIRouter

from ..schemas import PreferencesUpdate, ProfileUpdate
from .dependencies import CandidateServiceDep

router = APIRouter(tags=['候选人'])


@router.get('/profile')
def profile(service: CandidateServiceDep):
    return service.get('profile')


@router.put('/profile')
def update_profile(body: ProfileUpdate, service: CandidateServiceDep):
    return service.update('profile', body.data.model_dump(), body.revision, body.confirmed)


@router.get('/preferences')
def preferences(service: CandidateServiceDep):
    return service.get('preferences')


@router.put('/preferences')
def update_preferences(body: PreferencesUpdate, service: CandidateServiceDep):
    return service.update('preferences', body.data.model_dump(), body.revision)
