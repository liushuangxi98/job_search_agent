"""唯一的 Agent 接入位置，在应用创建时选择固定样例或用户异步入口。"""
from importlib import import_module


def load_handler(mode):
    if mode == 'demo':
        from .demo import handle
        return handle
    try:
        # 此模块由你自行编写；真实模式找不到入口时必须明确报错，不能回退演示。
        return getattr(import_module('career_agent.agent'), 'handle')
    except (ImportError, AttributeError):
        return None
