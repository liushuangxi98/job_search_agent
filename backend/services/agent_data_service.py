"""保留 Agent 的 services.repository 接口，所有操作转交给业务服务。

这里是应用接入适配器，不是 Agent 实现，也不直接访问数据库。
"""
from .profile_service import ProfileService
from .document_service import DocumentService
from .job_service import JobService
from .memory_service import MemoryService
from .report_service import ReportService


class AgentDataService:
    """保持 services.repository 的调用约定；校验、事务和异常仍由对应业务服务负责。"""
    def __init__(self, profiles: ProfileService, documents: DocumentService,
                 jobs: JobService, memories: MemoryService, reports: ReportService):
        """复用已装配的业务服务，不创建数据库 Session 或执行资料读写。"""
        self.profiles = profiles
        self.document_service = documents
        self.job_service = jobs
        self.record_services = {'memories': memories}
        self.report_service = reports

    @staticmethod
    def _profile_snapshot(record):
        # 已保存报告和 Agent 使用 data 字段，页面接口使用 profile 字段。
        return {'id': record['id'], 'revision': record['revision'], 'data': record['profile']}

    def profile(self):
        """返回含 ID 和修订号的画像快照；尚未保存时 ID 为空。"""
        return self._profile_snapshot(self.profiles.get())

    def update_profile(self, data, revision):
        """返回含新修订号的画像或条件字典；revision 是读取时的值。"""
        return self._profile_snapshot(self.profiles.save(data, revision))

    def job(self, key):
        """按岗位 ID 返回岗位及来源字典；缺失时抛 AppError。"""
        return self.job_service.get(key)

    def jobs(self, q='', offset=0, limit=100):
        """按关键词及条数分页返回岗位 items/total；直接调用需自行提供合法分页参数。"""
        return self.job_service.list(q, offset, limit)

    def save_job(self, data, key=None, revision=None):
        """新增或更新岗位并返回字典；key 为空走新增/合并，更新须提供读取时的 revision。"""
        return self.job_service.save(data, key, revision)

    def delete_job(self, key):
        """删除岗位和来源关联，成功无返回值；不存在抛 AppError，历史报告保留。"""
        return self.job_service.delete(key)

    def document(self, key, *, internal=False):
        """返回资料元数据，internal=True 时带存储名；不存在时抛 AppError。"""
        return self.document_service.get(key, internal=internal)

    def documents(self, kind=None):
        """返回资料 items/total；kind 为空时不过滤，没有资料时 items 为空列表。"""
        return self.document_service.list(kind)

    def add_document(self, **values):
        """仅保存文件元数据并返回字典，不创建或校验磁盘文件；写入异常向上抛出。"""
        return self.document_service.add_metadata(**values)

    def delete_document(self, key):
        """只删除资料元数据及级联关联，不删除磁盘文件；缺失抛 AppError，成功无返回值。"""
        return self.document_service.delete_metadata(key)

    def records(self, kind, q='', offset=0, limit=100):
        """返回 memories 的 items/total；分页按条数，空列表表示无匹配。"""
        return self.record_services[kind].list(q, offset, limit)

    def save_record(self, kind, data, key=None):
        """保存已确认记忆；省略 key 时新增。"""
        return self.record_services[kind].save(data, key)

    def delete_record(self, kind, key):
        """删除已确认记忆；记录缺失抛 AppError。"""
        return self.record_services[kind].delete(key)

    def save_report(self, kind, data, snapshot, run_id, is_demo):
        """按 run_id 保存或读取已有报告；返回报告字典，不替运行服务校验 Agent 输出。"""
        return self.report_service.save(kind, data, snapshot, run_id, is_demo)

    def report(self, key):
        """返回指定报告及其保存的快照；缺失时抛 AppError。"""
        return self.report_service.get(key)

    def reports(self):
        """返回报告 items/total，按创建时间倒序；没有报告时 items 为空列表。"""
        return self.report_service.list()

    # 运行服务从这里取岗位评估，具体怎样查询报告由 report_service 负责。
    def latest_evaluation(self, job_id):
        return self.report_service.latest_evaluation(job_id)
