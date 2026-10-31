"""健康检查和明确标记的固定演示数据。"""
from fastapi import APIRouter

from .dependencies import SystemServiceDep

router = APIRouter(tags=['系统'])


@router.get('/health')
def health(service: SystemServiceDep):
    """检查数据库连接，返回 database、mode 和 agent_ready；数据库异常映射为 503。
    agent_ready 只表示已加载 Agent 入口，不代表模型或外部工具已经可用。
    """
    return service.health()


@router.get('/demo/examples')
def demo_examples(service: SystemServiceDep):
    """返回画像、条件、岗位及演示提示的固定样例，不写入数据库；非演示模式返回 404。"""
    return service.examples()
