"""从阿里云增强搜索获取公司业务资料，保留正文和可核对的网页来源。"""
import httpx

from backend.exceptions import AppError
from career_agent.config import AgentSettings


async def search_company(queries: list[str]) -> list[dict[str, str]]:
    # 接收已经根据 JD 规划好的查询词，工具负责执行搜索并整理资料。
    key = AgentSettings().aliyun_search_api_key.get_secret_value().strip()
    if not key:
        raise AppError(503, 'SEARCH_KEY_MISSING', '请在 .env 中配置 ALIYUN_SEARCH_API_KEY。')
    sources = {}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # 按规划顺序检索；任一请求失败立即向调用方报告，不自动重试。
            for query in queries:
                response = await client.post('https://cloud-iqs.aliyuncs.com/search/unified',
                    headers={'Authorization': f'Bearer {key}'},
                    json={'query': query, 'engineType': 'GenericAdvanced',
                          'contents': {'mainText': True, 'rerankScore': True}})
                if response.status_code == 413:
                    raise AppError(413, 'SEARCH_TOO_LARGE', '联网搜索请求过大，本次评估已停止。')
                # 鉴权、额度和服务错误交给下面的统一出口，阻止继续生成无依据的联网结论。
                response.raise_for_status()
                # 保留接口返回的全部网页结果，按链接合并重复项，顺序沿用服务端排序。
                for item in response.json()['pageItems']:
                    url = item['link']
                    if url.startswith(('https://', 'http://')):
                        sources.setdefault(url, {'title': item['title'], 'url': url,
                            'content': item.get('mainText') or item.get('snippet') or '',
                            'published_time': item.get('publishedTime') or ''})
    except httpx.HTTPStatusError as exc:
        # 页面只显示状态码，原始异常链供控制台定位鉴权、额度或服务问题。
        raise AppError(502, 'SEARCH_FAILED', f'阿里云搜索失败（HTTP {exc.response.status_code}），请检查凭证、开通状态及额度。') from exc
    except (httpx.RequestError, ValueError, KeyError) as exc:
        raise AppError(502, 'SEARCH_UNAVAILABLE', '联网搜索连接失败或返回格式异常，请稍后重新发起评估。') from exc
    # 来源随报告保存，之后查看历史报告时仍能找到当时的依据。
    return list(sources.values())
