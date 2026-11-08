"""
模块名称:  evaluate.py
所属项目:  job_search_agent
创建时间:  2026/11/7 22:25
作者:      刘双喜 <1604030114@qq.com>
功能描述: 根据确认画像和岗位快照评估匹配程度，并查询单程公交和驾车路线。
"""
import json

from backend.contracts import AgentResponse, JobMatchAnalysis, ReportJob, RequirementMatch
from backend.exceptions import AppError
from .llm import build_chat
from .prompts import EVALUATE_PROMPT
from .tools.amap import get_route, search_places


def match_score(matches: list[RequirementMatch]) -> float | None:
    """根据模型对每条岗位要求的判断，算出这份求职材料的百分制匹配分。"""
    # 没有要求可评，或每一项都缺少依据时，报告显示分数未知。
    if not any(item.assessment != '资料不足' for item in matches):
        return None
    # 每条要求权重相同，分母是全部要求数。例如两项符合、一项资料不足，得 66.7 分。
    points = {'符合': 1, '部分符合': 0.5, '不符合': 0, '资料不足': 0}
    return round(100 * sum(points[item.assessment] for item in matches) / len(matches), 1)


async def match_job(profile, job, emit):
    """用画像和当前 JD 调一次模型，返回匹配报告和本次模型用量。"""
    job_data = job['data']
    await emit('status', f"正在评估 {job_data['company']} · {job_data['title']}")
    # 系统消息说明评估规则，用户消息提供这位候选人的画像和当前岗位的资料。
    messages = [
        {'role': 'system', 'content': EVALUATE_PROMPT},
        {'role': 'user', 'content': json.dumps({'profile': profile, 'job': job_data}, ensure_ascii=False)},
    ]
    model = build_chat().with_structured_output(
        JobMatchAnalysis, method='function_calling', include_raw=True,
        extra_body={'thinking': {'type': 'disabled'}},
    )
    result = await model.ainvoke(messages)
    analysis = result['parsed']
    usage = result['raw'].usage_metadata
    # 模型已经被调用。即使没有解析出有效结果，也把这次用量随失败原因一并返回。
    if analysis is None:
        return None, usage
    # 模型给出逐项判断，程序按同一套规则算分，并用快照中的岗位 ID 关联报告。
    row = ReportJob(**analysis.model_dump(), job_id=job['id'], match_score=match_score(analysis.matches))
    return row.model_dump(), usage


async def evaluate(command, emit) -> AgentResponse:
    """首次算匹配分并搜索两组地点；用户选好起终点后，直接查路线并交付报告。"""
    job, = command.snapshot['jobs']
    if command.human_input['decision'] == 'start':
        profile = command.snapshot['profile']['data']
        report, usage = await match_job(profile, job, emit)
        if report is None:
            return AgentResponse(status='failed', kind='chat', usage=usage,
                                 data={'message': f"未能解析岗位「{job['data']['title']}」的评估结果。"})
        # 即使两端各只有一个地点，也要给用户一次核对机会。同时保存候选坐标和匹配结果。
        await emit('status', '正在搜索通勤起点和办公地点')
        places = {'start': await search_places(profile['commute_origin']),
                  'end': await search_places(job['data']['address'])}
        prompt = '请选择通勤起点和办公地点，再确认查询路线。'
        names = {side: [place['name'] for place in options] for side, options in places.items()}
        return AgentResponse(status='needs_input', kind='chat',
                             data={'message': prompt, 'place_choices': names},
                             pending_input={'kind': 'question', 'prompt': prompt},
                             state={'report': report, 'places': places}, usage=usage)

    # 确认时直接用上次保存的报告，以及页面选中的两个地点。
    report = command.state['report']
    places = command.state['places']
    answer = command.human_input['data']
    start_place = places['start'][int(answer['start']) - 1]
    end_place = places['end'][int(answer['end']) - 1]
    report['commutes'] = await calculate_commute(start_place, end_place)
    return AgentResponse(status='completed', kind='evaluation_report', data={'jobs': [report]})

async def calculate_commute(start_place: dict, end_place: dict) -> dict:
    """用用户选好的两个地点查公交和驾车，直接返回报告所需的两种路线。"""
    routes = {}
    for mode in ('public_transit', 'driving'):
        route = await get_route(start_place, end_place, mode)
        endpoints = f"{start_place['name']} → {end_place['name']}"
        route['route_summary'] = (endpoints + '\n' + route['route_summary']).rstrip()
        routes[mode] = route
    return routes