import json


def markdown(report):
    data = report['data']
    lines = ['# ' + str(data.get('title', '报告')), '',
             '> 模拟数据：没有实际评估输入内容。' if report['is_demo'] else '> 基于本次运行保存的输入快照。',
             '', str(data.get('summary', '')), '']
    if report['kind'] == 'evaluation':
        lines += ['| 公司 | 岗位 | 总分 | 匹配 | 薪资 | 通勤 |', '| --- | --- | ---: | ---: | ---: | ---: |']
        def cell(value):
            return '待补充' if value is None else str(value).replace('|', '\\|').replace('\n', ' ')
        for row in data.get('jobs', []):
            lines.append('| ' + ' | '.join(cell(row.get(key)) for key in ('company', 'title', 'total_score', 'match_score', 'salary_score', 'commute_score')) + ' |')
        for row in data.get('jobs', []):
            lines += ['', '## ' + row.get('company', ''), row.get('recommendation', '')]
            lines += ['- ' + text for text in row.get('gaps', [])]
    else:
        for item in data.get('answers', []):
            lines += ['## ' + item.get('question', ''), '', item.get('answer', ''), '']
    lines += ['', '## 保存的数据', '', '```json', json.dumps(data, ensure_ascii=False, indent=2), '```']
    return '\n'.join(lines)
