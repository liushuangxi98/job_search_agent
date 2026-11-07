"""聊天模型配置；仅在实际调用时读取，演示模式无需配置模型密钥。"""
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / '.env', env_file_encoding='utf-8',
        extra='ignore', str_strip_whitespace=True,
    )
    llm_model: str = Field(title='聊天模型名称', default='deepseek-flash', min_length=1)
    llm_api_key: SecretStr = Field(title='模型API密钥', default=SecretStr(''), repr=False)
    llm_base_url: str = Field(title='模型服务地址', default='https://api.deepseek.com', min_length=1)

