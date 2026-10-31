"""从本地 .env 读取应用配置；模型与工具配置留给用户 Agent。"""
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', env_file_encoding='utf-8', extra='ignore')
    mysql_host: str = '127.0.0.1'
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_user: str = 'root'
    mysql_password: str = Field(default='', repr=False)
    mysql_database: str = Field(default='job_search_agent', pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
    agent_mode: Literal['demo', 'real'] = 'demo'
    data_dir: Path = ROOT / 'data'

    @property
    def database_url(self):
        return URL.create('mysql+pymysql', username=self.mysql_user, password=self.mysql_password,
                          host=self.mysql_host, port=self.mysql_port, database=self.mysql_database,
                          query={'charset': 'utf8mb4'})
