"""高德通勤工具：搜索地点，按用户选定的两个地点查询路线。"""
import traceback

import httpx

from backend.exceptions import AppError
from career_agent.config import AgentSettings


async def map_request(path: str, **params) -> dict:
    """请求高德接口；只把公开的错误提示交给页面，避免在错误信息中泄露 Key。"""
    key = AgentSettings().amap_api_key.get_secret_value().strip()
    if not key:
        raise AppError(503, 'AMAP_KEY_MISSING', '未配置 AMAP_API_KEY，无法查询通勤。')
    try:
        async with httpx.AsyncClient(base_url='https://restapi.amap.com', timeout=10) as client:
            response = await client.get(path, params={**params, 'key': key})
        if response.status_code == 413:
            raise AppError(413, 'AMAP_REQUEST_TOO_LARGE', '高德请求超过大小限制，本次通勤查询已停止。')
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AppError(502, 'AMAP_UNAVAILABLE', '高德服务不可用或返回了无效响应。') from exc
    if result.get('status') != '1':
        raise AppError(502, 'AMAP_ERROR', f"高德查询失败（错误码 {result.get('infocode', '未知')}）。")
    return result


async def search_places(place_name: str) -> list[dict]:
    """从模糊地名返回精确地点的待选列表。只有一处也返回列表，找不到就返回空列表。"""
    if not place_name.strip():
        return []
    result = await map_request('/v5/place/text', keywords=place_name.strip(), page_size=5)
    places = []
    for place in result.get('pois', []):
        if place.get('location'):
            # 同名地点用城市、区和详细地址区分，坐标及城市编码留给路线接口。
            name = ' '.join(filter(None, ( place.get('cityname'),place.get('adname'), place['name'])))
            places.append({'name': name, 'location': place['location'], 'citycode': place.get('citycode')})
    return places


async def get_route(start_place: dict, end_place: dict, mode: str) -> dict:
    """按已选的起终点查一种路线，返回单程分钟、公里和途经线路。"""
    params = {'origin': start_place['location'], 'destination': end_place['location'], 'show_fields': 'cost'}
    by_bus = mode == 'public_transit'
    # 公交还需要两端各自的城市编码，跨城时两个编码可以不同。
    if by_bus:
        params.update(city1=start_place['citycode'], city2=end_place['citycode'], AlternativeRoute=1)
    path = 'transit/integrated' if by_bus else 'driving'

    # 计算通勤路线
    try:
        result = await map_request('/v5/direction/' + path, **params)
    except AppError as exc:
        if exc.status == 413:
            raise
        # 此处会把错误转成路线提示，先将具体原因打印到控制台。
        traceback.print_exc()
        return {'duration_minutes': None, 'distance_km': None, 'route_summary': exc.message}
    route = result.get('route', {}).get('transits' if by_bus else 'paths')[0]

    # 解析通勤路线
    if by_bus:
        names = []
        for segment in route.get('segments', []):
            if segment.get('walking'):
                names.append('步行')
            bus = segment.get('bus') or {}
            lines = bus.get('buslines') or bus.get('bus_lines') or bus.get('steps') or []
            if lines:
                names.append(lines[0].get('name') or lines[0].get('bus_name'))
            if segment.get('railway'):
                names.append(segment['railway'].get('name') or '铁路')
            if segment.get('taxi'):
                names.append('出租车')
    else:
        names = [step.get('road_name') for step in route.get('steps', [])]
    # 高德的时间和距离单位分别是秒和米。
    seconds = route.get('cost').get('duration')
    meters = route.get('distance')
    return {
        'duration_minutes': round(float(seconds) / 60, 1) if seconds not in (None, '', []) else None,
        'distance_km': round(float(meters) / 1000, 2),
        'route_summary': ' → '.join(filter(None, names)),
    }



