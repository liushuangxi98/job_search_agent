"""存取报告快照，用户可单独删除评估记录。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Report


class ReportRepository:
    """服务提供 Session（数据库会话）并负责业务校验、冲突处理及事务提交或回滚。
    flush 发送会话内所有待写变更而不提交，SQLAlchemy 异常向上传播。
    """
    def __init__(self, session: Session):
        """使用调用方当前事务的 session，不新建会话或结束事务。"""
        self.session = session

    def get(self, report_id: str):
        """按 report_id 返回报告记录对象；不存在时返回 None。"""
        return self.session.get(Report, report_id)

    def get_by_run(self, run_id: str):
        """按 run_id 返回该运行已保存的报告记录对象，无报告时返回 None。
        查询未显式加锁，不保证并发去重；重复写入由 run_id 唯一约束拒绝，异常向上传播。
        """
        return self.session.scalar(select(Report).where(Report.run_id == run_id))

    def list(self):
        """按创建时间从新到旧返回全部报告记录列表；没有报告时返回空列表。"""
        return self.session.scalars(select(Report).order_by(Report.created_at.desc())).all()

    def delete(self, row: Report):
        self.session.delete(row)

    # job_id 来自用户点击的岗位。按时间倒序只取一条，旧评估和别的岗位都不会参与本场规划。
    def latest_evaluation(self, job_id):
        # 评估入口一次只评一个岗位，只读取匹配的最新真实评估数据。
        return self.session.scalar(select(Report.data).where(
            Report.kind == 'evaluation', Report.is_demo.is_(False),
            Report.data['jobs'][0]['job_id'].as_string() == job_id,
        ).order_by(Report.created_at.desc(), Report.id.desc()).limit(1))

    def add(self, **values):
        """用 values 中的结果 data、输入快照 snapshot、运行标识 run_id 和演示标记 is_demo 新增报告。
        values 的 kind 为 evaluation（评估）或 interview（面试）；结果校验和快照复制由调用方负责。
        flush 后返回记录对象；重复 run_id 由数据库唯一约束拒绝，不在此复用已有报告。
        """
        row = Report(**values)
        self.session.add(row)
        self.session.flush()
        return row
