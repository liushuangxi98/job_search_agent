"""检查确认入库所需的基础资料，不代替 Agent 的解析、材料诊断或业务推理。"""

CANDIDATE_FIELDS = {
    'name': '姓名', 'education': '学历', 'major': '大学专业',
    'experience_years': '工作年限', 'skills': '技能',
    'experience': '工作经历', 'projects': '项目经历',
    'direction': '求职方向', 'city': '目标城市', 'commute_origin': '通勤起点',
    'salary_min': '期望月薪下限', 'salary_max': '期望月薪上限', 'summary': '个人简介',
    'commute_mode': '出行偏好'
}
JOB_FIELDS = {
    'company': '公司名称', 'title': '岗位名称', 'city': '工作城市',
    'address': '办公地址', 'salary': '月薪区间', 'jd_text': '岗位描述',
}


def candidate_missing(profile: dict) -> list[dict]:
    """个人资料缺项列表；0 年经验有效，个人资料不能用信息盲区代替。"""
    missing = []
    for field, label in CANDIDATE_FIELDS.items():
        value = profile.get(field)
        absent = not any(value or []) if field == 'skills' else value is None or value == ''
        if absent:
            missing.append({'field': f'profile.{field}', 'label': label})
    return missing


def job_missing(data: dict) -> list[dict]:
    """每个关键字段有值或有已确认盲区说明，才可进入正式岗位池。"""
    missing = []
    blind_spots = data.get('blind_spots', {})
    for field, label in JOB_FIELDS.items():
        if field == 'salary':
            present = data.get('salary_min') is not None and data.get('salary_max') is not None
        else:
            present = bool(data.get(field))
        if not present and not blind_spots.get(field):
            missing.append({'field': field, 'label': label})
    return missing
