"""
模块名称:  config.py
所属项目:  job_search_agent
创建时间:  2026/11/3 17:07
作者:      刘双喜 <1604030114@qq.com>
功能描述:
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent

class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / '.env', env_file_encoding='utf-8', extra='ignore')
    llm_model: str = 'deepseek-flash'
    llm_api_key: str = ''
    llm_base_url: str = 'https://api.deepseek.com'

