"""按需读取模型配置并创建可复用的聊天模型对象。"""
from functools import lru_cache

from langchain_openai import ChatOpenAI

from backend.exceptions import AppError
from .config import AgentSettings


@lru_cache(maxsize=1)
def build_chat():
    """首次使用时读取配置并复用模型对象；修改 .env 后重启服务。"""
    settings = AgentSettings()
    if not settings.llm_api_key.get_secret_value().strip():
        raise AppError(503, 'MODEL_NOT_CONFIGURED', '请在 .env 中填写 LLM_API_KEY，再重启服务。')
    return ChatOpenAI(
        model=settings.llm_model, api_key=settings.llm_api_key,
        base_url=settings.llm_base_url, timeout=180,
        # 不隐式重试模型操作，包含 413 在内的失败交由当前运行明确报告。
        max_retries=0,
    )

async def ainvoke(messages: list, client: ChatOpenAI|None = None):
    if client is None:
        client = build_chat()
    ret = client.ainvoke(messages)
    return ret