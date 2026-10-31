"""唯一的 Agent 接入位置，在应用创建时选择固定样例或用户异步入口。"""
from importlib import import_module


def load_handler(mode):
    """返回对应模式的处理入口，供运行服务以 await 调用。

    demo 返回固定样例；加载真实入口遇到 ImportError 或 AttributeError 时返回 None。
    其他异常继续向上抛出，可能导致应用创建失败。
    None 由上层报告未接入，不回退到样例；这里不校验 handle 是否可调用。
    """
    if mode == 'demo':
        from .demo import handle
        return handle
    try:
        # 此模块由你自行编写；真实模式找不到入口时必须明确报错，不能回退演示。
        return getattr(import_module('career_agent.agent'), 'handle')
    except (ImportError, AttributeError):
        return None
