"""从独立知识库检索已索引的正式面试题。"""
import httpx

from backend.exceptions import AppError
from career_agent.config import AgentSettings


# 每次查询一个考点；返回记录中的 question_id 供面试模块去重和选题。
async def search_questions(topic: str, distance_limit: float | None = None) -> list[dict]:
    """按具体技术考点搜索面试题，返回有真实题号的候选题和参考答案。"""
    # 未配置时直接提示，避免把空密钥发给知识库。
    key = AgentSettings().knowledge_api_key.get_secret_value().strip()
    if not key:
        raise AppError(503, 'KNOWLEDGE_KEY_MISSING', '请在工作台 .env 中配置 KNOWLEDGE_API_KEY。')
    try:
        # 知识库独立运行在本机 8200 端口，等待嵌入和召回时让出事件循环。
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8200', timeout=180) as client:
            # 每个考点最多取四条候选，给主问题和追问保留选择空间。
            # 共享密钥放入请求头，由知识库在开始检索前核对。
            # topic 是这次要查的考点，例如“支付幂等”。limit 只限制本次返回几道候选题。
            response = await client.post('/api/search', headers={'X-API-Key': key},
                                         json={'topic': topic, 'limit': 4, 'distance_limit': distance_limit})
        # 先识别 413，让上层停止本场检索并保留超限原因。
        if response.status_code == 413:
            raise AppError(413, 'KNOWLEDGE_REQUEST_TOO_LARGE', '知识库检索请求过大，本场面试已停止。')
        # 单独提示配置不匹配，便于用户核对两端密钥。
        if response.status_code == 401:
            raise AppError(401, 'KNOWLEDGE_AUTH_FAILED', '知识库鉴权失败，请核对两端 KNOWLEDGE_API_KEY。')
        response.raise_for_status()
    # 连接失败及其他 HTTP 错误统一转为页面可展示的知识库错误。
    except httpx.HTTPError as exc:
        # 保留原始请求异常，让运行服务打印具体的连接、超时或响应错误。
        raise AppError(502, 'KNOWLEDGE_UNAVAILABLE', '题库检索失败，请检查 8200 知识库服务及其嵌入模型。') from exc
    # 保留知识库的召回顺序。空列表表示查询成功但没有可用的已索引题目。
    return response.json()['items']
