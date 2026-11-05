"""完整的固定测试资料；调用处显式覆盖字段，不自动补齐待校验的输入。"""


def complete_profile(**changes):
    return {
        'name': '测试求职者', 'education': '本科', 'major': '计算机科学与技术',
        'experience_years': 4, 'skills': ['Python', '自动化测试'],
        'experience': '维护接口自动化平台及持续集成流程。',
        'projects': '开发 RAG 题库检索项目，负责召回评测和接口测试。',
        'summary': '四年 Python 测试开发经验',
        'direction': 'Python Agent 应用开发', 'city': '杭州',
        'commute_origin': '杭州市西湖区文三路',
        'salary_min': 20000, 'salary_max': 30000,
        **changes,
    }


def complete_job(**changes):
    return {
        'company': '测试科技', 'title': 'Agent 应用开发', 'city': '杭州',
        'address': '杭州市滨江区测试园区', 'jd_text': '使用 Python 开发 RAG 应用并编写自动化评测。',
        'salary_min': 20000, 'salary_max': 30000, 'source_ids': [], 'blind_spots': {},
        **changes,
    }


def candidate_payload(revision, *, profile=None):
    return {
        'revision': revision,
        'profile': complete_profile() if profile is None else profile,
    }
