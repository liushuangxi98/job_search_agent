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

    def latest_evaluation(self, job_id):
        with self.session_factory() as session:
            data = ReportRepository(session).latest_evaluation(job_id)
            if data is not None:
                # row 是报告里这个岗位的评估明细。挑出匹配依据、关键要求和有利经历，交给面试负责人选考点。
                row = data['jobs'][0]
                return {key: row[key] for key in ('matches', 'key_requirement', 'supporting_experiences') if key in row}

    def delete_evaluation(self, report_id: str):
        """删除选中的一份岗位评估，岗位资料和其他报告各自保留。"""
        with self.session_factory.begin() as session:
            repository = ReportRepository(session)
            row = repository.get(report_id)
            if row is None:
                raise AppError(404, 'NOT_FOUND', '评估记录不存在或已删除')
            if row.kind != 'evaluation':
                raise AppError(409, 'REPORT_KIND', '只能删除岗位评估记录')
            repository.delete(row)

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
                # 在副本中拆出正文，来源的标题、链接、时间及顺序留在展示数据里。
                stored_data = deepcopy(data)
                source_contents = {}
                for job in stored_data.get('jobs', []):
                    for source in job.get('business_sources', []):
                        if 'content' in source:
                            source_contents.setdefault(job['job_id'], {})[source['url']] = source.pop('content')
                # 分开的两份数据属于同一行，由同一事务保存。
                row = repository.add(
                    kind=kind, data=stored_data, source_contents=source_contents, snapshot=deepcopy(snapshot),
                    run_id=run_id, is_demo=is_demo,
                )
            return self.serialize(row)

    def export(self, report_id: str, format: str):
        """返回报告文本，不写导出文件；format='md' 为 Markdown，否则为完整报告 JSON。

        HTTP 层将 format 限定为 md/json；报告不存在时沿用 get 的 AppError。
        """
        if format == 'md':
            return markdown(self.get(report_id))
        with self.session_factory() as session:
            row = ReportRepository(session).get(report_id)
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            result = deepcopy(self.serialize(row))
            # 显式导出完整 JSON 时读取正文列，恢复原来的完整报告格式。
            source_contents = row.source_contents or {}
            for job in result['data'].get('jobs', []):
                contents = source_contents.get(job['job_id'], {})
                for source in job.get('business_sources', []):
                    if source['url'] in contents:
                        source['content'] = contents[source['url']]
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
        def cell(value):
            return '未知' if value is None or value == '' else str(value).replace('|', '\\|').replace('\n', ' ')
        snapshot_jobs = {job['id']: job['data'] for job in report['snapshot'].get('jobs', [])}
        for row in data.get('jobs', []):
            job = snapshot_jobs.get(row.get('job_id'), row)
            lines += ['', '## ' + cell(job.get('company')) + ' · ' + cell(job.get('title')),
                      '', '匹配分：' + cell(row.get('match_score'))]
            # 导出与页面保持相同顺序，保留当时联网检索的来源链接。
            if row.get('business_analysis'):
                lines += ['', '### 业务分析', '', row['business_analysis']]
                for number, source in enumerate(row.get('business_sources', []), 1):
                    lines += [f"- [{number}] {source['title']}：{source['url']}"]
            lines += ['', '### 求职建议', '', row.get('recommendation', '')]
            lines += ['', '### 匹配依据']
            for item in row.get('matches', []):
                lines += ['- 要求：' + item.get('requirement', ''), '  依据：' + item.get('evidence', '')]
            for name, field in [('最有利的经历', 'supporting_experiences'), ('最不利的关键要求', 'key_requirement'), ('优势', 'strengths'),
                                ('差距', 'gaps'), ('信息盲区', 'missing')]:
                value = row.get(field)
                if value:
                    body = '\n'.join('- ' + text for text in value) if isinstance(value, list) else value
                    lines += ['', '### ' + name, '', body]
            lines += ['', '### 单程通勤', '| 方式 | 分钟 | 公里 | 主要路线 |', '| --- | ---: | ---: | --- |']
            for mode, label in [('public_transit', '公共交通'), ('driving', '驾车')]:
                route = row.get('commutes', {}).get(mode, {})
                lines.append('| ' + label + ' | ' + ' | '.join(
                    cell(route.get(key)) for key in ('duration_minutes', 'distance_km', 'route_summary')
                ) + ' |')
    else:
        for item in data.get('answers', []):
            lines += ['## ' + item.get('question', ''), '', item.get('answer', ''), '']
    lines += ['', '## 保存的数据', '', '```json', json.dumps(data, ensure_ascii=False, indent=2), '```']
    return '\n'.join(lines)
