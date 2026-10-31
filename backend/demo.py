"""Fixed UI fixtures, NOT an Agent. Inputs never drive inference or scoring."""
from copy import deepcopy

from .contracts import AgentResponse
from .errors import AppError

PROFILE = {'name': '示例求职者', 'headline': 'Python 自动化测试开发 · 转向 Agent 应用',
           'summary': '四年 Python 自动化测试开发经验，正在学习 Agent 应用开发。',
           'skills': ['Python', '自动化测试', '接口测试'], 'experience': '构建接口自动化测试及持续集成流程。',
           'education': '', 'evidence': []}
PREFERENCES = {'city': '杭州', 'direction': 'Python Agent 应用开发', 'salary_min': 20000,
               'salary_max': 30000, 'commute_origin': '杭州西湖区', 'commute_limit_minutes': 60,
               'commute_mode': 'public_transit'}
JOBS = [
    {'company': '星河科技（虚构）', 'title': 'Agent 应用开发工程师', 'city': '杭州',
     'address': '示例办公地址，待核实', 'salary_min': 20000, 'salary_max': 30000,
     'jd_text': '【固定演示资料】使用 Python 开发 Agent 应用，了解工具调用与 RAG。',
     'notes': '模拟资料，不代表真实招聘信息。', 'source_ids': []},
    {'company': '云岚软件（虚构）', 'title': 'Python AI 应用工程师', 'city': '杭州',
     'address': '示例地址，待核实', 'salary_min': 18000, 'salary_max': 26000,
     'jd_text': '【固定演示资料】开发知识库问答应用，编写自动化评测。',
     'notes': '模拟资料，不代表真实招聘信息。', 'source_ids': []},
]
QUESTIONS = [
    '工具调用与直接让模型回答有什么区别？', '如何判断检索结果是否足以支持回答？',
    '如何评估一个 RAG 系统的召回效果？', '工具请求超时后应如何处理？',
    '如何组织用户画像与当前对话上下文？', '如何测试 Agent 的错误恢复行为？',
    '为什么需要校验模型生成的工具参数？', '如何记录一次 Agent 执行轨迹？',
]
NOTICE = '固定联调样例；未调用模型或外部工具，与输入内容无推理关系。'


def examples():
    return deepcopy({'profile': PROFILE, 'preferences': PREFERENCES, 'jobs': JOBS, 'notice': NOTICE})


async def handle(command, services, emit):
    await emit('tool', '读取固定联调样例', {'notice': NOTICE})
    scenario = command.input.get('demo_scenario', 'success')
    human = command.human_input
    if scenario == 'fail':
        raise AppError(502, 'DEMO_FAILURE', '这是固定的失败场景，用于验证界面错误处理。')
    if scenario == 'needs_input' and human is None and command.action != 'interview':
        if command.action == 'chat':
            current = command.snapshot['preferences']
            pending = {'kind': 'proposal', 'prompt': '演示修改：将期望城市设置为杭州，是否保存？',
                       'proposal': {'target': 'preferences', 'revision': current['revision'],
                                    'changes': {'city': '杭州'}}}
        else:
            pending = {'kind': 'question', 'prompt': '演示地址歧义：请补充或确认办公地址。'}
        return AgentResponse(status='needs_input', kind='chat', data={'message': pending['prompt'], 'notice': NOTICE}, pending_input=pending)
    if command.action == 'parse_resume':
        return AgentResponse(kind='profile_draft', data={'profile': deepcopy(PROFILE), 'notice': NOTICE})
    if command.action == 'extract_jobs':
        return AgentResponse(kind='job_drafts', data={'jobs': deepcopy(JOBS), 'notice': NOTICE})
    if command.action == 'index_knowledge':
        return AgentResponse(kind='knowledge_result', data={'questions': [
            {'question': QUESTIONS[0], 'answer': '固定演示答案：工具调用由程序执行并返回观察结果。',
             'tags': ['工具调用'], 'document_id': None, 'locator': '固定演示题，不是上传 MD 的解析结果'}
        ], 'coverage': ['工具调用（演示）'], 'notice': NOTICE + ' 尚未创建向量索引。'})
    if command.action == 'evaluate':
        items = []
        # Scores are literal fixtures. No matching, salary or commute algorithm is implemented.
        scores = [(80, 100, 80, 85), (70, 80, 60, 71)]
        for index, job in enumerate(command.snapshot['jobs']):
            match, salary, commute, total = scores[index % len(scores)]
            partial = scenario == 'partial' and index == len(command.snapshot['jobs']) - 1
            items.append({'job_id': job['id'], 'company': job['data']['company'], 'title': job['data']['title'],
                          'match_score': match, 'salary_score': salary, 'commute_score': None if partial else commute,
                          'total_score': None if partial else total,
                          'gaps': ['需要补充可展示的 Agent 项目经历（固定示例）'],
                          'recommendation': '演示建议：查看技能差距，补充资料后再做真实评估。',
                          'missing': ['通勤信息待补充'] if partial else [], 'evidence': [],
                          'commutes': {'public_transit': {'duration_minutes': None if partial else 45, 'distance_km': 18},
                                       'driving': {'duration_minutes': 30, 'distance_km': 16}}})
        return AgentResponse(status='partial' if scenario == 'partial' else 'completed', kind='evaluation_report',
                             data={'title': '岗位对比报告 · 模拟', 'summary': NOTICE, 'jobs': items})
    if command.action == 'interview':
        answers = deepcopy(command.state.get('answers', []))
        if human and human['decision'] == 'answer':
            index = len(answers)
            answers.append({'question': QUESTIONS[index], 'answer': human['text'],
                            'question_id': f'demo-{index + 1}', 'source': '固定演示题库'})
        if len(answers) >= 8 or (human and human['decision'] == 'finish'):
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
    if human:
        message = '修改已确认并保存。可重新评估查看新结果。' if human['decision'] == 'confirm' else '本次补充已处理；拒绝的修改没有保存。'
    return AgentResponse(kind='chat', data={'message': message, 'notice': NOTICE})
