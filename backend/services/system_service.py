"""应用健康状态与演示入口。"""
from ..config import Settings
from ..database import Database
from ..exceptions import AppError


class SystemService:
    def __init__(self, database: Database, settings: Settings, handler):
        self.database = database
        self.settings = settings
        self.handler = handler

    def health(self):
        self.database.ping()
        return {
            'database': 'ok',
            'mode': self.settings.agent_mode,
            'agent_ready': self.handler is not None,
        }

    def examples(self):
        if self.settings.agent_mode != 'demo':
            raise AppError(404, 'NOT_FOUND', '当前不是演示模式')
        from ..demo import examples
        return examples()
