"""应用健康状态与演示入口。"""
from ..config import Settings
from ..database import Database
from ..exceptions import AppError


class SystemService:
    def __init__(self, database: Database, settings: Settings, handler):
        """保留数据库、配置和已加载入口，供健康检查与演示模式判断使用。"""
        self.database = database
        self.settings = settings
        self.handler = handler

    def health(self):
        """返回数据库可查询状态、模式和入口存在性；数据库异常向上抛出。

        agent_ready 只说明入口不为 None，不代表已经验证模型或工具能够调用。
        """
        self.database.ping()
        return {
            'database': 'ok',
            'mode': self.settings.agent_mode,
            'agent_ready': self.handler is not None,
        }

    def examples(self):
        """仅在 demo 模式返回固定资料副本；真实模式抛 AppError，不生成真实业务结果。"""
        if self.settings.agent_mode != 'demo':
            raise AppError(404, 'NOT_FOUND', '当前不是演示模式')
        from ..demo import examples
        return examples()
