"""健康检查和明确标记的固定演示数据。"""
from fastapi import APIRouter

from .dependencies import SystemServiceDep

router = APIRouter(tags=['系统'])


@router.get('/health')
def health(service: SystemServiceDep):
    return service.health()


@router.get('/demo/examples')
def demo_examples(service: SystemServiceDep):
    return service.examples()
