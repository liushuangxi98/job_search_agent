"""固定演示数据接口。"""
from fastapi import APIRouter

from .dependencies import SystemServiceDep

router = APIRouter(prefix='/demo', tags=['系统'])


@router.get('/examples')
def demo_examples(service: SystemServiceDep):
    """返回画像、条件、岗位及演示提示的固定样例，不写入数据库；非演示模式返回 404。"""
    return service.examples()
