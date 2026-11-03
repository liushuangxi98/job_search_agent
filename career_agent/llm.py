"""
模块名称:  llm.py
所属项目:  job_search_agent
创建时间:  2026/11/3 16:50
作者:      刘双喜 <1604030114@qq.com>
功能描述:
"""
from pydantic import SecretStr
from langchain_openai import ChatOpenAI
from career_agent.config import AgentSettings

settings = AgentSettings()

def build_chat():
    llm = ChatOpenAI(
        model=settings.llm_model,
        api_key=SecretStr(settings.llm_api_key),
        base_url=settings.llm_base_url,
        timeout=180,
        max_retries=2,
    )
    return llm