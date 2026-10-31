"""HTTP 与业务服务共用的数据校验，保证 Agent 直接调用时也遵守相同字段约束。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Payload(BaseModel):
    """Pydantic 输入基类：拒绝未知字段并去除字符串首尾空白，失败产生校验异常。"""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ProfileData(Payload):
    name: str = Field(default='', max_length=100)
    headline: str = Field(default='', max_length=200)
    summary: str = Field(default='', max_length=10000)
    skills: list[str] = Field(default_factory=list, max_length=100)
    experience: str = Field(default='', max_length=20000)
    education: str = Field(default='', max_length=2000)
    evidence: list[dict] = Field(default_factory=list, max_length=100)


class ProfileUpdate(Payload):
    """画像保存请求；修订号用于拒绝旧表单覆盖，confirmed 表示用户是否确认画像。"""
    revision: int = Field(ge=0)
    confirmed: bool
    data: ProfileData

    # 字段完成解析后再校验字段之间的业务关系，FastAPI 会把校验失败转换为 422。
    @model_validator(mode='after')
    def nonempty_confirmation(self):
        """拒绝确认没有简介、经历和技能的画像；通过时返回当前对象，否则抛 ValueError。"""
        if self.confirmed and not (self.data.summary or self.data.experience or self.data.skills):
            raise ValueError('请至少填写简介、经历或技能后再确认画像')
        return self


class Preferences(Payload):
    city: str = Field(default='', max_length=100)
    direction: str = Field(default='Python Agent 应用开发', max_length=200)
    # 薪资按元/月保存，None 表示未知；不使用 0 代替未知薪资。
    salary_min: int | None = Field(default=None, gt=0, le=10000000)
    salary_max: int | None = Field(default=None, gt=0, le=10000000)
    commute_origin: str = Field(default='', max_length=300)
    # 通勤是单程分钟数，commute_mode 指定用于评分的出行方式。
    commute_limit_minutes: int = Field(default=60, gt=0, le=1440)
    commute_mode: Literal['public_transit', 'driving'] = 'public_transit'

    @model_validator(mode='after')
    def salary_order(self):
        """仅在薪资上下限都已知时比较；顺序有效返回自身，否则抛 ValueError。"""
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError('薪资下限不能超过上限')
        return self


class PreferencesUpdate(Payload):
    revision: int = Field(ge=0)
    data: Preferences


class JobData(Payload):
    """正式保存的岗位结构；薪资为元/月或 None，source_ids 引用已登记资料。"""
    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    city: str = Field(default='', max_length=100)
    address: str = Field(default='', max_length=300)
    salary_min: int | None = Field(default=None, gt=0, le=10000000)
    salary_max: int | None = Field(default=None, gt=0, le=10000000)
    salary_note: str = Field(default='', max_length=500)
    jd_text: str = Field(default='', max_length=50000)
    notes: str = Field(default='', max_length=5000)
    source_ids: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def salary_order(self):
        """拒绝已知薪资下限高于上限；未知的一端不参与比较，校验通过返回自身。"""
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError('薪资下限不能超过上限')
        return self


class JobUpdate(Payload):
    revision: int = Field(ge=1)
    data: JobData


class QuestionData(Payload):
    """题目元数据；document_id 可为空，locator 记录来源位置，不代表已经建立向量索引。"""
    document_id: str | None = None
    question: str = Field(min_length=1, max_length=10000)
    answer: str = Field(default='', max_length=30000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    locator: str = Field(default='', max_length=500)


class MemoryData(Payload):
    """已确认记忆的普通存取格式；本模型不推断内容，也不执行用户确认流程。"""
    content: str = Field(min_length=1, max_length=10000)
    category: Literal['preference', 'profile', 'weakness'] = 'preference'
    evidence: str = Field(default='', max_length=10000)
