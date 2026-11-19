"""工作台健康检查。"""
from fastapi import APIRouter

from .dependencies import SystemServiceDep

router = APIRouter(prefix='/health', tags=['系统'])


@router.get('')
def health(service: SystemServiceDep):
    """检查数据库连接，返回 database、mode 和 agent_ready；数据库异常映射为 503。
    agent_ready 只表示已加载 Agent 入口，不代表模型或外部工具已经可用。
    """
    return service.health()
