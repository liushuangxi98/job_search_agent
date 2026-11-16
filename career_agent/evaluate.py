"""
模块名称:  evaluate.py
所属项目:  job_search_agent
创建时间:  2026/11/7 22:25
作者:      刘双喜 <1604030114@qq.com>
功能描述: 根据确认画像和岗位快照评估匹配程度，并查询单程公交和驾车路线。
"""
import json
from pydantic import BaseModel, Field

from backend.contracts import AgentResponse, BusinessAnalysis, JobMatchAnalysis, ReportJob, RequirementMatch
from backend.exceptions import AppError
from .llm import build_chat
from .prompts import EVALUATE_PROMPT, COMPANY_SEARCH_PROMPT, BUSINESS_ANALYSIS_PROMPT
from .tools.amap import get_route, search_places
from .tools.company_search import search_company


class SearchKeywords(BaseModel):
    # 公司和岗位由程序补齐，模型只提取 JD 中有依据的业务关键词。
    keywords: list[str] = Field(description='最多三组简短业务关键词，包含具体业务、系统或任务，不重复公司名和岗位名。')


async def make_search_queries(job):
    """先读岗位职责与要求，提取能够查到公司业务和实际工作场景的搜索词。"""
    # 规划只接收招聘信息，避免把候选人的经历误当成公司的业务。
    materials = {key: job.get(key) for key in ('company', 'title', 'city', 'job_description', 'job_requirement')}
    # 沿用现有模型和结构化输出方式，搜索词与最终分析分两步生成。
    model = build_chat().with_structured_output(
        SearchKeywords, method='function_calling', include_raw=True,
        extra_body={'thinking': {'type': 'disabled'}},
    )
    result = await model.ainvoke([
        {'role': 'system', 'content': COMPANY_SEARCH_PROMPT},
        {'role': 'user', 'content': json.dumps(materials, ensure_ascii=False)},
    ])
    # 失败直接向运行层报告，避免回退到已知命中率低的岗位标题搜索。
    plan = result['parsed']
    if plan is None:
        raise AppError(502, 'SEARCH_PLAN_FAILED', '未能生成有效的岗位业务搜索词。')
    # 合并重复和空白项后最多查三次，控制本次规划的搜索范围。
    keywords = list(dict.fromkeys(item.strip() for item in plan.keywords if item.strip()))[:3]
    if not keywords:
        raise AppError(502, 'SEARCH_PLAN_EMPTY', '未能从岗位资料中提取有效搜索词。')
    # 每条实际查询都保留招聘信息中的公司全名、岗位原名，再补充具体业务方向。
    queries = [f"{job['company']} {job['title']} {item}" for item in keywords]
    # 保留本次规划用量，稍后与分析阶段一起上报。
    return queries, result['raw'].usage_metadata


def match_score(matches: list[RequirementMatch]) -> float | None:
    """根据模型对每条岗位要求的判断，算出这份求职材料的百分制匹配分。"""
    # 没有要求可评，或每一项都缺少依据时，报告显示分数未知。
    if not any(item.assessment != '资料不足' for item in matches):
        return None
    # 每条要求权重相同，分母是全部要求数。例如两项符合、一项资料不足，得 66.7 分。
    points = {'符合': 1, '部分符合': 0.5, '不符合': 0, '资料不足': 0}
    return round(100 * sum(points[item.assessment] for item in matches) / len(matches), 1)


async def analyze_business(profile, job_data, sources):
    """独立生成业务讲解，消息中包含简历、JD 和本次搜索的全部来源。"""
    # 每次重新构造消息列表，业务讲解不读取匹配模型的对话或结果。
    messages = [
        {'role': 'system', 'content': BUSINESS_ANALYSIS_PROMPT},
        {'role': 'user', 'content': json.dumps({'profile': profile, 'job': job_data, 'business_sources': sources}, ensure_ascii=False)},
    ]
    # 只允许业务字段进入结构化结果，匹配字段由另一份结果提供。
    model = build_chat().with_structured_output(
        BusinessAnalysis, method='function_calling', include_raw=True,
        extra_body={'thinking': {'type': 'disabled'}},
    )
    result = await model.ainvoke(messages)
    # 调用方统一处理解析失败并合计实际模型消耗。
    return result['parsed'], result['raw'].usage_metadata


async def match_job(profile, job, emit):
    """先独立匹配岗位，再检索并分析业务，由程序合并两份结果。"""
    job_data = job['data']
    await emit('status', f"正在评估 {job_data['company']} · {job_data['title']}")
    # 匹配请求只包含用户确认的简历和当前 JD，消息中没有联网资料。
    messages = [
        {'role': 'system', 'content': EVALUATE_PROMPT},
        {'role': 'user', 'content': json.dumps({'profile': profile, 'job': job_data}, ensure_ascii=False)},
    ]
    model = build_chat().with_structured_output(
        JobMatchAnalysis, method='function_calling', include_raw=True,
        extra_body={'thinking': {'type': 'disabled'}},
    )
    result = await model.ainvoke(messages)
    # 首次匹配失败时直接返回，不继续发起搜索和业务分析。
    analysis = result['parsed']
    match_usage = result['raw'].usage_metadata
    if analysis is None:
        return None, match_usage

    await emit('status', '正在根据岗位职责提取业务搜索词')
    queries, query_usage = await make_search_queries(job_data)
    # 执行记录展示实际查询，来源序号沿用工具返回的去重列表顺序。
    await emit('tool', '正在联网查询岗位相关业务', {'queries': queries})
    sources = await search_company(queries)
    await emit('status', '正在结合联网资料分析岗位业务')
    business, business_usage = await analyze_business(profile, job_data, sources)

    # 一次评估包括岗位匹配、查询规划和业务讲解，汇总三次模型的实际用量。
    usages = (match_usage, query_usage, business_usage)
    if all(value is not None for value in usages):
        usage = {key: sum(value.get(key, 0) for value in usages)
                 for key in ('input_tokens', 'output_tokens', 'total_tokens')}
    else:
        # 任一阶段未上报时保留未知，避免把局部用量显示为完整消耗。
        usage = None
    # 模型已经被调用。即使没有解析出有效结果，也把这次用量随失败原因一并返回。
    if business is None:
        return None, usage
    # 只用匹配结果计算分数，业务字段和网页来源由程序追加到最终报告。
    row = ReportJob(**analysis.model_dump(), job_id=job['id'], business_sources=sources,
                    business_analysis=business.business_analysis,
                    match_score=match_score(analysis.matches))
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
        # 业务分析已包含在 report 中，通过 state 传给通勤确认后的续接调用。
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
    # 人工选择地点只查询路线，沿用开场生成的分析与联网来源，避免重复搜索和计费。
    places = command.state['places']
    answer = command.human_input['data']
    start_place = places['start'][int(answer['start']) - 1]
    end_place = places['end'][int(answer['end']) - 1]
    # 把续接得到的通勤信息合入同一份报告，业务分析和来源一并交回保存。
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
