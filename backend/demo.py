"""提供固定界面联调样例，不根据输入执行推理、评分或真实工具调用。"""
from copy import deepcopy

from .contracts import AgentResponse
from .exceptions import AppError

PROFILE = {'name': '示例求职者',
           'summary': '四年 Python 自动化测试开发经验，正在学习 Agent 应用开发。',
           'skills': ['Python', '自动化测试', '接口测试'], 'experience': '构建接口自动化测试及持续集成流程。',
           'education': '本科', 'major': '计算机科学与技术（示例）', 'experience_years': 4,
           'projects': '开发接口自动化测试平台，支持用例执行和测试报告汇总（固定示例）。', 'evidence': [],
           'city': '杭州', 'direction': 'Python Agent 应用开发', 'salary_min': 20000,
           'salary_max': 30000, 'commute_origin': '杭州西湖区',
           'commute_mode': '公共交通'}
JOBS = [
    {'company': '星河科技（虚构）', 'title': 'Agent 应用开发工程师', 'city': '杭州',
     'address': '示例办公地址，待核实', 'salary_min': 20000, 'salary_max': 30000,
     'job_description': '【固定演示资料】使用 Python 开发 Agent 应用，了解工具调用与 RAG。',
     'job_requirement': 'Python 开发经验（固定示例）', 'experience_years': 3, 'education': '本科', 'evidence': []},
    {'company': '云岚软件（虚构）', 'title': 'Python AI 应用工程师', 'city': '杭州',
     'address': '示例地址，待核实', 'salary_min': 18000, 'salary_max': 26000,
     'job_description': '【固定演示资料】开发知识库问答应用，编写自动化评测。',
     'job_requirement': 'Python 开发经验（固定示例）', 'experience_years': 3, 'education': '本科', 'evidence': []},
]
QUESTIONS = [
    '工具调用与直接让模型回答有什么区别？', '如何判断检索结果是否足以支持回答？',
    '如何评估一个 RAG 系统的召回效果？', '工具请求超时后应如何处理？',
    '如何组织用户画像与当前对话上下文？', '如何测试 Agent 的错误恢复行为？',
    '为什么需要校验模型生成的工具参数？', '如何记录一次 Agent 执行轨迹？',
]
NOTICE = '固定联调样例；未调用模型或外部工具，与输入内容无推理关系。'


def examples():
    """返回示例画像、条件和岗位的深拷贝，避免界面编辑影响后续请求使用的固定样例。"""
    return deepcopy({'profile': PROFILE, 'jobs': JOBS, 'notice': NOTICE})


async def handle(command, services, emit):
    """按动作和演示场景返回 AgentResponse；通过 emit 展示事件，不直接写入资料。

    command 携带初始快照、续接状态和人工输入；services 只为保持真实入口签名兼容。
    可模拟等待输入、部分结果和失败，失败场景抛 AppError；分数不评价实际输入。
    """
    await emit('tool', '读取固定联调样例', {'notice': NOTICE})
    scenario = command.input.get('demo_scenario', 'success')
    human = command.human_input
    if scenario == 'fail':
        raise AppError(502, 'DEMO_FAILURE', '这是固定的失败场景，用于验证界面错误处理。')
    if scenario == 'needs_input' and human['decision'] == 'start' and command.action != 'interview':
        if command.action == 'chat':
            current = command.snapshot['profile']
            pending = {'kind': 'proposal', 'prompt': '演示修改：将期望城市设置为杭州，是否保存？',
                       'proposal': {'target': 'profile', 'revision': current['revision'],
                                    'changes': {'city': '杭州'}}}
        else:
            pending = {'kind': 'question', 'prompt': '演示地址歧义：请补充或确认办公地址。'}
        return AgentResponse(status='needs_input', kind='chat', data={'message': pending['prompt'], 'notice': NOTICE}, pending_input=pending)
    if command.action == 'parse_resume':
        profile = deepcopy(PROFILE)
        if scenario == 'partial':
            profile['projects'] = ''
        return AgentResponse(
            status='partial' if scenario == 'partial' else 'completed', kind='profile_draft',
            data=profile,
        )
    if command.action == 'extract_jobs':
        job = deepcopy(JOBS[0])
        if command.input.get('document_id'):
            job['evidence'] = [{'document_id': command.input['document_id'], 'locator': '', 'text': ''}]
        if scenario == 'partial':
            job['address'] = ''
        return AgentResponse(kind='job_drafts', data=job)
    if command.action == 'evaluate':
        job, = command.snapshot['jobs']
        data = job['data']
        partial = not data['address'] or scenario == 'partial'
        row = {
            'job_id': job['id'], 'match_score': 80 if data['job_description'] or data['job_requirement'] else None,
            'matches': [{'requirement': 'Python 开发（固定示例）',
                         'evidence': 'Python 自动化测试经历（固定示例）', 'assessment': '符合'}],
            'supporting_experiences': '1. Python 自动化测试经历符合开发要求（固定示例）。',
            'key_requirement': '', 'recommendation': '演示建议：查看技能差距，补充资料后再做真实评估。',
            'evidence': [],
            'commutes': {
                'public_transit': {'duration_minutes': None if partial else 45, 'distance_km': None if partial else 18,
                                   'route_summary': '' if partial else '步行 → 地铁 → 步行（固定路线样例）'},
                'driving': {'duration_minutes': None if partial else 30, 'distance_km': None if partial else 16,
                            'route_summary': '' if partial else '城市道路 → 园区道路（固定路线样例）'},
            },
        }
        return AgentResponse(status='partial' if partial else 'completed', kind='evaluation_report',
                             data={'title': '岗位评估报告 · 模拟', 'summary': NOTICE, 'jobs': [row]})
    if command.action == 'interview':
        # 续接状态由运行服务在下一次调用时交回，未完成的面试不会保存到报告表。
        answers = deepcopy(command.state.get('answers', []))
        if human['decision'] == 'answer':
            index = len(answers)
            answers.append({'question': QUESTIONS[index], 'answer': human['text'],
                            'question_id': f'demo-{index + 1}', 'source': '固定演示题库'})
        if len(answers) >= len(QUESTIONS) or human['decision'] == 'finish':
            return AgentResponse(kind='interview_report', data={
                'title': '模拟面试报告', 'summary': NOTICE + ' 以下评分未评价你的回答。', 'answers': answers,
                'dimensions': [{'name': 'Agent 基础（固定示例）', 'score': 75 if answers else None}],
                'weaknesses': ['完善工具异常处理（固定示例）'], 'practice': ['练习编写一个受控工具（固定示例）']})
        index = len(answers)
        question = QUESTIONS[index]
        return AgentResponse(status='needs_input', kind='interview_question',
                             data={'round': index + 1, 'question': question, 'source': '固定演示题库',
                                   'question_id': f'demo-{index + 1}', 'notice': NOTICE},
                             pending_input={'kind': 'question', 'prompt': question}, state={'answers': answers})
    message = '这是聊天入口的固定示例回复。接入你实现的 Agent 后，这里将根据上下文回答和提出资料修改建议。'
    return AgentResponse(kind='chat', data={'message': message, 'notice': NOTICE})
