"""HTTP 与业务服务共用的数据校验，保证 Agent 直接调用时也遵守相同字段约束。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Payload(BaseModel):
    """Pydantic 输入基类：拒绝未知字段并去除字符串首尾空白，失败产生校验异常。"""
    # 本文件的资料模型均继承此约定：未声明的 key 会报错，字段长度按字符/列表项数计算。
    # “可省略”表示使用默认值，不代表允许 None；只有标注 | None 的字段接受 JSON null。
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ProfileData(Payload):
    # 所有画像字段均可省略，常用于逐步补齐草稿；ProfileDraftData 的 profile 本身仍是必填 key。
    # 用户确认画像时，ProfileUpdate 另要求 summary、experience、skills 至少有一项非空。
    # 姓名；可省略，默认 ""，最多 100 字符；示例："王某"。
    name: str = Field(default='', max_length=100)
    # 职业概述或求职标题；可省略，默认 ""，最多 200 字符；示例："Python 测试开发转 Agent 应用"。
    headline: str = Field(default='', max_length=200)
    # 画像简介；可省略，默认 ""，最多 10000 字符；示例："四年 Python 自动化测试开发经验。"。
    summary: str = Field(default='', max_length=10000)
    # 技能名称；可省略，默认 []，最多 100 个字符串，未限制单项长度；示例：["Python", "接口测试"]。
    skills: list[str] = Field(default_factory=list, max_length=100)
    # 工作/项目经历正文；可省略，默认 ""，最多 20000 字符；示例："维护接口自动化平台及 CI 流程。"。
    experience: str = Field(default='', max_length=20000)
    # 教育背景；可省略，默认 ""，最多 2000 字符；示例："本科，计算机相关专业"。
    education: str = Field(default='', max_length=2000)
    # 画像提取依据；可省略，默认 []，最多 100 个字典；字典内部 key 尚未固定，须保持 JSON 可序列化。
    # 示例：[{"document_id": "resume_001", "locator": "第 2 页", "text": "负责接口自动化"}]。
    evidence: list[dict] = Field(default_factory=list, max_length=100)


class ProfileUpdate(Payload):
    """画像保存请求；修订号用于拒绝旧表单覆盖，confirmed 表示用户是否确认画像。"""
    # 保存前读取到的共用修订号；必填非负整数；示例：3。版本冲突由服务检查，不由本模型检查。
    revision: int = Field(ge=0)
    # 是否确认画像；必填布尔值，无默认值；示例：true（JSON），确认非空条件见下方校验器。
    confirmed: bool
    # 要保存的完整画像内容；必填 ProfileData；示例：{"summary": "四年 Python 开发经验"}。
    data: ProfileData

    # 字段完成解析后再校验字段之间的业务关系，FastAPI 会把校验失败转换为 422。
    @model_validator(mode='after')
    def nonempty_confirmation(self):
        """拒绝确认没有简介、经历和技能的画像；通过时返回当前对象，否则抛 ValueError。"""
        if self.confirmed and not (self.data.summary or self.data.experience or self.data.skills):
            raise ValueError('请至少填写简介、经历或技能后再确认画像')
        return self


class Preferences(Payload):
    # 求职城市；可省略，默认 ""，最多 100 字符；示例："杭州"。
    city: str = Field(default='', max_length=100)
    # 求职方向；可省略，默认 "Python Agent 应用开发"，最多 200 字符；示例："Agent 应用开发"。
    direction: str = Field(default='Python Agent 应用开发', max_length=200)
    # 薪资按元/月保存，None 表示未知；不使用 0 代替未知薪资。
    # 期望月薪下限；可省略，默认 None；整数 1—10000000；示例：20000。
    salary_min: int | None = Field(default=None, gt=0, le=10000000)
    # 期望月薪上限；可省略，默认 None；整数 1—10000000；示例：30000。
    # 上下限都已知时，下限必须不高于上限；任一为 None 时不做两端比较。
    salary_max: int | None = Field(default=None, gt=0, le=10000000)
    # 通勤出发地址，不是公司地址；可省略，默认 ""，最多 300 字符；示例："杭州市西湖区某小区"。
    commute_origin: str = Field(default='', max_length=300)
    # 通勤是单程分钟数，commute_mode 指定用于评分的出行方式。
    # 可接受的单程上限；可省略，默认 60；整数 1—1440；示例：45。
    commute_limit_minutes: int = Field(default=60, gt=0, le=1440)
    # 常用评分路线；可省略，默认 public_transit（公交），也允许 driving（驾车）；示例："driving"。
    commute_mode: Literal['public_transit', 'driving'] = 'public_transit'

    @model_validator(mode='after')
    def salary_order(self):
        """仅在薪资上下限都已知时比较；顺序有效返回自身，否则抛 ValueError。"""
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError('薪资下限不能超过上限')
        return self


class PreferencesUpdate(Payload):
    # 保存前读取的修订号；必填非负整数，与画像共用版本；示例：3。
    revision: int = Field(ge=0)
    # 要保存的条件；必填 Preferences，省略其子字段会采用默认值；示例：{"city": "杭州"}。
    data: Preferences


class JobData(Payload):
    """正式保存的岗位结构；薪资为元/月或 None，source_ids 引用已登记资料。"""
    # 公司名称；必填，去首尾空白后长度 1—200 字符；示例："星河科技"。
    company: str = Field(min_length=1, max_length=200)
    # 岗位名称；必填，去首尾空白后长度 1—200 字符；示例："Agent 应用开发工程师"。
    title: str = Field(min_length=1, max_length=200)
    # 工作城市；可省略，默认 ""，最多 100 字符；示例："杭州"。
    city: str = Field(default='', max_length=100)
    # 具体办公地址，用于通勤查询；可省略，默认 ""，最多 300 字符；示例："杭州市滨江区某园区"。
    address: str = Field(default='', max_length=300)
    # 招聘月薪下限，元/月；可省略，默认 None；整数 1—10000000；示例：18000。
    salary_min: int | None = Field(default=None, gt=0, le=10000000)
    # 招聘月薪上限，元/月；可省略，默认 None；整数 1—10000000；示例：26000。
    # 两端都已知时仍要求下限不高于上限；薪资不能确定时用 None，不用 0。
    salary_max: int | None = Field(default=None, gt=0, le=10000000)
    # 薪数、奖金等补充，不参与自动金额换算；可省略，默认 ""，最多 500 字符；示例："13 薪"。
    salary_note: str = Field(default='', max_length=500)
    # 招聘描述原文；可省略，默认 ""，最多 50000 字符；示例："负责 Agent 工具调用与 RAG 开发。"。
    jd_text: str = Field(default='', max_length=50000)
    # 用户备注；可省略，默认 ""，最多 5000 字符；示例："需要确认实际办公地点"。
    notes: str = Field(default='', max_length=5000)
    # 已登记的来源资料 ID；可省略，默认 []，最多 30 个字符串，未限制单个 ID 的长度。
    # 示例：["image_001"]；为空可表示手动录入，资料是否存在由保存服务另外检查。
    source_ids: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def salary_order(self):
        """拒绝已知薪资下限高于上限；未知的一端不参与比较，校验通过返回自身。"""
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError('薪资下限不能超过上限')
        return self


class JobUpdate(Payload):
    # 保存前读取的岗位修订号；必填整数，至少为 1；示例：2，服务在写入时检查是否仍有效。
    revision: int = Field(ge=1)
    # 更新后的完整岗位及来源；必填 JobData，省略可选子字段会采用默认值，而非保留旧值。
    # 示例：{"company": "星河科技", "title": "Agent 开发", "source_ids": ["image_001"]}。
    data: JobData


class QuestionData(Payload):
    """题目元数据；document_id 可为空，locator 记录来源位置，不代表已经建立向量索引。"""
    # 来源 MD 等资料的 ID；可省略，默认 None，无长度/格式约束；示例："knowledge_001"。
    # None 表示没有关联资料；非空 ID 的存在性由保存服务检查，不是这里的字段校验。
    document_id: str | None = None
    # 题干；必填，去首尾空白后长度 1—10000 字符；示例："如何评估 RAG 的召回效果？"。
    question: str = Field(min_length=1, max_length=10000)
    # 参考答案；可省略，默认 ""，最多 30000 字符；示例："准备标注题集并计算 Recall@K。"。
    answer: str = Field(default='', max_length=30000)
    # 知识点标签；可省略，默认 []，最多 30 个字符串，单项未设长度上限；示例：["RAG", "评测"]。
    tags: list[str] = Field(default_factory=list, max_length=30)
    # 资料内的原文定位，不是独立的资料 ID；可省略，默认 ""，最多 500 字符；示例："第 3 节/题 12"。
    locator: str = Field(default='', max_length=500)


class MemoryData(Payload):
    """已确认记忆的普通存取格式；本模型不推断内容，也不执行用户确认流程。"""
    # 要保存的记忆事实；必填，去首尾空白后长度 1—10000 字符；示例："用户优先考虑杭州岗位。"。
    content: str = Field(min_length=1, max_length=10000)
    # 记忆类别；可省略，默认 preference；限 preference（偏好）、profile（画像）、weakness（薄弱项）。
    # 示例："weakness"，具体内容仍填 content，不由 category 自动生成。
    category: Literal['preference', 'profile', 'weakness'] = 'preference'
    # 这条记忆的确认依据；可省略，默认 ""，最多 10000 字符；示例："用户在聊天中明确确认。"。
    evidence: str = Field(default='', max_length=10000)
