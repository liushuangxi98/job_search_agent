"""报告的快照保存、读取及 Markdown/JSON 导出。"""
from copy import deepcopy
import json

from sqlalchemy.orm import Session, sessionmaker

from ..exceptions import AppError
from ..repositories.report_repository import ReportRepository


class ReportService:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    @staticmethod
    def serialize(row):
        return {
            'id': row.id, 'kind': row.kind, 'run_id': row.run_id,
            'is_demo': row.is_demo, 'data': row.data, 'snapshot': row.snapshot,
            'created_at': row.created_at.isoformat() + 'Z',
        }

    def get(self, report_id: str):
        with self.session_factory() as session:
            row = ReportRepository(session).get(report_id)
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            return self.serialize(row)

    def list(self):
        with self.session_factory() as session:
            rows = ReportRepository(session).list()
            return {'items': [self.serialize(row) for row in rows], 'total': len(rows)}

    def save(self, kind: str, data: dict, snapshot: dict, run_id: str, is_demo: bool):
        with self.session_factory.begin() as session:
            repository = ReportRepository(session)
            row = repository.get_by_run(run_id)
            # 同一次运行再次保存时返回已有报告，保留最初的结果和输入快照。
            if row is None:
                row = repository.add(
                    kind=kind, data=deepcopy(data), snapshot=deepcopy(snapshot),
                    run_id=run_id, is_demo=is_demo,
                )
            return self.serialize(row)

    def export(self, report_id: str, format: str):
        result = self.get(report_id)
        if format == 'md':
            return markdown(result)
        return json.dumps(result, ensure_ascii=False, indent=2)


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
