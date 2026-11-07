"""从本地 .env 读取应用配置；模型与工具配置留给用户 Agent。"""
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """按传入参数、环境变量、项目根目录 .env、字段默认值的顺序取得应用配置。"""
    model_config = SettingsConfigDict(env_file=ROOT / '.env', env_file_encoding='utf-8', extra='ignore')
    mysql_host: str = Field(default='127.0.0.1', title='数据库主机')
    mysql_port: int = Field(title='数据库端口', default=3306, ge=1, le=65535)
    mysql_user: str = Field(default='root', title='数据库用户名')
    # repr=False 只隐藏对象打印中的密码，不能替代其他位置的日志脱敏。
    mysql_password: str = Field(title='数据库密码', default='', repr=False)
    mysql_database: str = Field(title='数据库名称', default='job_search_agent', pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
    agent_mode: Literal['demo', 'real'] = Field(default='demo', title='Agent运行模式')
    data_dir: Path = Field(default=ROOT / 'data', title='数据目录')

    @property
    def database_url(self):
        """返回 SQLAlchemy 连接 URL；作为属性访问，并正确处理密码中的特殊字符。"""
        return URL.create('mysql+pymysql', username=self.mysql_user, password=self.mysql_password,
                          host=self.mysql_host, port=self.mysql_port, database=self.mysql_database,
                          query={'charset': 'utf8mb4'})
