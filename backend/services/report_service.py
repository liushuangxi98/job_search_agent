"""报告的快照保存、读取及 Markdown/JSON 导出。"""
from copy import deepcopy
import json

from sqlalchemy.orm import Session, sessionmaker

from ..exceptions import AppError
from ..repositories.report_repository import ReportRepository


class ReportService:
    def __init__(self, session_factory: sessionmaker[Session]):
        """接收 Session 工厂，为每次报告存取单独创建 Session。"""
        self.session_factory = session_factory

    @staticmethod
    def serialize(row):
        """将报告行整理为含 data、snapshot 和模拟标识的字典；创建时间按 UTC 展示。"""
        return {
            'id': row.id, 'kind': row.kind, 'run_id': row.run_id,
            'is_demo': row.is_demo, 'data': row.data, 'snapshot': row.snapshot,
            'created_at': row.created_at.isoformat() + 'Z',
        }

    def get(self, report_id: str):
        """读取报告自身保存的结果和快照；不存在时抛 AppError，不拼入当前画像或岗位。"""
        with self.session_factory() as session:
            row = ReportRepository(session).get(report_id)
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            return self.serialize(row)

    def list(self):
        """按创建时间倒序返回报告 items 与 total；没有报告时 items 为空列表。"""
        with self.session_factory() as session:
            rows = ReportRepository(session).list()
            return {'items': [self.serialize(row) for row in rows], 'total': len(rows)}

    def save(self, kind: str, data: dict, snapshot: dict, run_id: str, is_demo: bool):
        """按运行 ID 保存并返回报告字典，已存在时返回原报告而不覆盖。

        kind 区分评估和面试，is_demo 标记模拟结果；data 和 snapshot 在写入前复制。
        这里只做普通存取，结果结构须由调用方先校验；数据库异常会回滚当前事务。
        """
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
        """返回报告文本，不写导出文件；format='md' 为 Markdown，否则为完整报告 JSON。

        HTTP 层将 format 限定为 md/json；报告不存在时沿用 get 的 AppError。
        """
        result = self.get(report_id)
        if format == 'md':
            return markdown(result)
        return json.dumps(result, ensure_ascii=False, indent=2)


def markdown(report):
    """将报告字典转换为 Markdown 正文及 data 附录，不写导出文件。
    岗位评估表中的 None 显示为“待补充”，JSON 附录保留 null。
    """
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
