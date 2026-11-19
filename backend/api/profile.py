"""当前用户的简历画像和求职条件。"""
from fastapi import APIRouter, Query

from ..schemas import ProfileUpdate
from .dependencies import ProfileServiceDep

router = APIRouter(prefix='/profile', tags=['简历画像'])


@router.get('')
def get_profile(service: ProfileServiceDep):
    return service.get()


@router.put('')
def save_profile(body: ProfileUpdate, service: ProfileServiceDep):
    return service.save(body.profile.model_dump(), body.revision)


@router.delete('')
def delete_profile(service: ProfileServiceDep, revision: int = Query(ge=0)):
    return service.delete(revision)
