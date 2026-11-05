"""HTTP 与业务服务共用的数据校验，保证 Agent 直接调用时也遵守相同字段约束。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Payload(BaseModel):
    """Pydantic 输入基类：拒绝未知字段并去除字符串首尾空白，失败产生校验异常。"""
    # 本文件的资料模型均继承此约定：未声明的 key 会报错，字段长度按字符/列表项数计算。
    # “可省略”表示使用默认值，不代表允许 None；只有标注 | None 的字段接受 JSON null。
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


def current_profile_fields(data: dict) -> dict:
    """读取旧画像时只剔除已退役的键；新请求仍由 ProfileData 拒绝未知字段。"""
    return {key: value for key, value in data.items() if key not in {'headline', 'commute_limit_minutes'}}


class ProfileData(Payload):
    # 草稿允许缺项；正式确认统一检查个人经历、学历、专业和求职条件。
    # 个人核心资料不可用“招聘方未披露”等信息盲区说明替代。
    # 姓名；可省略，默认 ""，最多 100 字符；示例："王某"。
    name: str = Field(default='', max_length=100, description='求职者姓名。')
    # 画像简介；可省略，默认 ""，最多 10000 字符；示例："四年 Python 自动化测试开发经验。"。
    summary: str = Field(default='', max_length=10000, description='个人经历的简短概述，属于可选补充信息。')
    # 技能名称；可省略，默认 []，最多 100 个字符串，未限制单项长度；示例：["Python", "接口测试"]。
    skills: list[str] = Field(default_factory=list, max_length=100, description='求职者具备的技能名称列表，缺失时使用空列表。')
    # 工作经历正文；草稿可省略，默认 ""，最多 20000 字符；正式确认时必填。
    experience: str = Field(default='', max_length=20000, description='工作经历正文，包括职责及成果；不要混入项目经历。')
    # 教育背景；可省略，默认 ""，最多 2000 字符；示例："本科"。
    education: str = Field(default='', max_length=2000, description='学历，例如本科、硕士；大学专业填写在 major。')
    major: str = Field(default='', max_length=200, description='大学所学专业，与学历分开填写；资料未提及则留空，正式确认时补齐。')
    # experience_years：累计工作年限；草稿默认 None，有限数值 0—80 年，正式确认必填；示例：4.5。
    experience_years: float | None = Field(default=None, ge=0, le=80, allow_inf_nan=False, description='累计工作年限，单位为年；0 表示暂无工作经验，未知为 null。')

    # projects：独立的项目经历正文；草稿默认 ""，最多 20000 字符，正式确认必填。
    #   示例："开发测试平台，负责接口执行器和结果分析。"。
    projects: str = Field(default='', max_length=20000, description='求职者实际参与的项目、承担的工作及成果。')
    # 画像提取依据；可省略，默认 []，最多 100 个字典；字典内部 key 尚未固定，须保持 JSON 可序列化。
    # 示例：[{"document_id": "resume_001", "locator": "第 2 页", "text": "负责接口自动化"}]。
    evidence: list[dict] = Field(default_factory=list, max_length=100, description='程序提取的只读原文依据；每项可记录 document_id、locator 和 text。')

    # 求职城市；可省略，默认 ""，最多 100 字符；示例："杭州"。
    city: str = Field(default='', max_length=100, description='期望工作的目标城市。')
    # 求职方向；草稿默认 ""，最多 200 字符；正式确认必须填写，不能由默认值代替用户提供。
    direction: str = Field(default='', max_length=200, description='期望的岗位或职业方向。')
    # 薪资按元/月保存，None 表示未知；不使用 0 代替未知薪资。
    # 期望月薪下限；可省略，默认 None；整数 1—10000000；示例：20000。
    salary_min: int | None = Field(default=None, gt=0, le=10000000, description='期望月薪下限，单位元/月；未知为 null。')
    # 期望月薪上限；可省略，默认 None；整数 1—10000000；示例：30000。
    # 上下限都已知时，下限必须不高于上限；任一为 None 时不做两端比较。
    salary_max: int | None = Field(default=None, gt=0, le=10000000, description='期望月薪上限，单位元/月；不得低于已知下限，未知为 null。')
    # 通勤出发地址，不是公司地址；可省略，默认 ""，最多 300 字符；示例："杭州市西湖区某小区"。
    commute_origin: str = Field(default='', max_length=300, description='求职者单程通勤的出发地址。')
    # 出行偏好默认公共交通，也可选驾车；评估时两种路线分别展示。
    commute_mode: Literal['public_transit', 'driving'] = Field(default='public_transit', description='偏好的出行方式：公交或驾车；路线评估仍分别展示两种方式。')

    @model_validator(mode='after')
    def salary_order(self):
        """仅在薪资上下限都已知时比较；顺序有效返回自身，否则抛 ValueError。"""
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError('薪资下限不能超过上限')
        return self



class ProfileUpdate(Payload):
    """画像保存请求；修订号用于拒绝旧表单覆盖，confirmed 表示用户是否确认画像。"""
    # 保存前读取到的共用修订号；必填非负整数；示例：3。版本冲突由服务检查，不由本模型检查。
    revision: int = Field(ge=0, description='当前已保存画像的修订号，用于检测并发修改。')
    # 是否确认画像；必填布尔值，无默认值；示例：true（JSON），确认非空条件见下方校验器。
    confirmed: bool = Field(description='是否确认这份画像；确认前必须补齐核心资料。')
    # 要保存的完整画像内容；必填 ProfileData；示例：{"summary": "四年 Python 开发经验"}。
    data: ProfileData = Field(description='本次保存的完整画像，包含个人经历和求职条件。')

    # 字段完成解析后再校验字段之间的业务关系，FastAPI 会把校验失败转换为 422。
    @model_validator(mode='after')
    def nonempty_confirmation(self):
        """拒绝确认没有简介、经历和技能的画像；通过时返回当前对象，否则抛 ValueError。"""
        if self.confirmed and not (self.data.summary or self.data.experience or self.data.skills):
            raise ValueError('请至少填写简介、经历或技能后再确认画像')
        return self


class CandidateUpdate(Payload):
    """一并确认画像和求职条件，避免两次保存导致部分成功或修订冲突。"""
    # revision：读取个人资料时的修订号；必填非负整数，例：3；服务会拒绝旧版本。
    revision: int = Field(ge=0, description='当前已保存画像的修订号，用于检测并发修改。')

    # profile：完整画像；必填，正式确认要求全部个人核心字段有效。
    profile: ProfileData = Field(description='需要一次确认保存的完整个人画像及求职条件。')



class JobData(Payload):
    """正式保存的岗位结构；薪资为元/月或 None，source_ids 引用已登记资料。"""
    # 公司名称；最多 200 字符，未披露时留空并在 blind_spots.company 明确说明。
    company: str = Field(default='', max_length=200)
    # 岗位名称；最多 200 字符，未披露时留空并在 blind_spots.title 明确说明。
    title: str = Field(default='', max_length=200)
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
    # blind_spots：招聘方未披露的字段及原因；默认 {}，最多 6 项。
    #   key 仅限 company/title/city/address/salary/jd_text，原因必须非空。
    #   示例：{"address": "招聘方尚未披露具体办公地点"}；正式保存前由用户确认。
    blind_spots: dict[str, str] = Field(default_factory=dict, max_length=6)

    @model_validator(mode='after')
    def valid_blind_spots(self):
        allowed = {'company', 'title', 'city', 'address', 'salary', 'jd_text'}
        if set(self.blind_spots) - allowed or any(not reason.strip() for reason in self.blind_spots.values()):
            raise ValueError('信息盲区须使用支持的字段名，并填写招聘方未披露的具体说明')
        return self

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
