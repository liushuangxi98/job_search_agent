"""HTTP 与业务服务共用的数据校验，保证 Agent 直接调用时也遵守相同字段约束。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Payload(BaseModel):
    """Pydantic 输入基类：拒绝未知字段并去除字符串首尾空白，失败产生校验异常。"""
    # 本文件的资料模型均继承此约定：未声明的 key 会报错，字段长度按字符/列表项数计算。
    # “可省略”表示使用默认值，不代表允许 None；只有标注 | None 的字段接受 JSON null。
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


def current_profile_fields(data: dict) -> dict:
    """读取旧画像时清理退役字段并转换出行偏好；新请求按当前契约校验。"""
    profile = {key: value for key, value in data.items() if key not in {'headline', 'commute_limit_minutes'}}
    modes = {'public_transit': '公共交通', 'driving': '驾车'}
    if profile.get('commute_mode') in modes:
        profile['commute_mode'] = modes[profile['commute_mode']]
    return profile


class ProfileData(Payload):
    # 草稿允许缺项；正式确认统一检查个人经历、学历、专业和求职条件。
    # 个人核心资料不可用“招聘方未披露”等信息盲区说明替代。
    # 姓名；可省略，默认 ""，最多 100 字符；示例："王某"。
    name: str = Field(title='姓名', default='', max_length=100, description='求职者姓名。')
    # 画像简介；可省略，默认 ""，最多 10000 字符；示例："四年 Python 自动化测试开发经验。"。
    summary: str = Field(title='个人简介', default='', max_length=10000, description='个人经历的简短概述；草稿可留空，正式确认时必填。')
    # 技能名称；可省略，默认 []，最多 100 个字符串，未限制单项长度；示例：["Python", "接口测试"]。
    skills: list[str] = Field(title='技能', default_factory=list, max_length=100, description='求职者具备的技能名称列表，缺失时使用空列表。')
    # 工作经历正文；草稿可省略，默认 ""，最多 20000 字符；正式确认时必填。
    experience: str = Field(title='工作经历', default='', max_length=20000, description='工作经历正文，包括职责及成果；不要混入项目经历。')
    # 教育背景；可省略，默认 ""，最多 2000 字符；示例："本科"。
    education: str = Field(title='学历', default='', max_length=2000, description='学历，例如本科、硕士；大学专业填写在 major。')
    major: str = Field(title='大学专业', default='', max_length=200, description='大学所学专业，与学历分开填写；资料未提及则留空，正式确认时补齐。')
    # experience_years：累计工作年限；草稿默认 None，有限数值 0—80 年，正式确认必填；示例：4.5。
    experience_years: float | None = Field(title='工作年限', default=None, ge=0, le=80, allow_inf_nan=False, description='累计工作年限，单位为年；0 表示暂无工作经验，未知为 null。')

    # projects：独立的项目经历正文；草稿默认 ""，最多 20000 字符，正式确认必填。
    #   示例："开发测试平台，负责接口执行器和结果分析。"。
    projects: str = Field(title='项目经历', default='', max_length=20000, description='求职者实际参与的项目、承担的工作及成果。')
    # 画像提取依据；可省略，默认 []，最多 100 个字典；字典内部 key 尚未固定，须保持 JSON 可序列化。
    # 示例：[{"document_id": "resume_001", "locator": "第 2 页", "text": "负责接口自动化"}]。
    evidence: list[dict] = Field(title='提取依据', default_factory=list, max_length=100, description='程序提取的只读原文依据；每项可记录 document_id、locator 和 text。')

    # 求职城市；可省略，默认 ""，最多 100 字符；示例："杭州"。
    city: str = Field(title='目标城市', default='', max_length=100, description='期望工作的目标城市。')
    # 求职方向；草稿默认 ""，最多 200 字符；正式确认必须填写，不能由默认值代替用户提供。
    direction: str = Field(title='求职方向', default='', max_length=200, description='期望的岗位或职业方向。')
    # 薪资按元/月保存，None 表示未知；不使用 0 代替未知薪资。
    # 期望月薪下限；可省略，默认 None；整数 1—10000000；示例：20000。
    salary_min: int | None = Field(title='期望月薪下限', default=None, gt=0, le=10000000, description='期望月薪下限，单位元/月；未知为 null。')
    # 期望月薪上限；可省略，默认 None；整数 1—10000000；示例：30000。
    # 上下限都已知时，下限必须不高于上限；任一为 None 时不做两端比较。
    salary_max: int | None = Field(title='期望月薪上限', default=None, gt=0, le=10000000, description='期望月薪上限，单位元/月；不得低于已知下限，未知为 null。')
    # 通勤出发地址，不是公司地址；可省略，默认 ""，最多 300 字符；示例："杭州市西湖区某小区"。
    commute_origin: str = Field(title='通勤起点', default='', max_length=300, description='求职者单程通勤的出发地址。')
    # 出行偏好未知时为 None；正式确认时须选择公共交通或驾车。
    commute_mode: Literal['公共交通', '驾车'] | None = Field(title='出行偏好', default=None, description='偏好的出行方式：公交或驾车；路线评估仍分别展示两种方式。')

    @model_validator(mode='after')
    def salary_order(self):
        """仅在薪资上下限都已知时比较；顺序有效返回自身，否则抛 ValueError。"""
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError('薪资下限不能超过上限')
        return self



class ProfileUpdate(Payload):
    """画像保存请求；修订号用于拒绝旧表单覆盖，confirmed 表示用户是否确认画像。"""
    # 保存前读取到的共用修订号；必填非负整数；示例：3。版本冲突由服务检查，不由本模型检查。
    revision: int = Field(title='画像修订号', ge=0, description='当前已保存画像的修订号，用于检测并发修改。')
    # 是否确认画像；必填布尔值，无默认值；示例：true（JSON），确认非空条件见下方校验器。
    confirmed: bool = Field(title='确认状态', description='是否确认这份画像；确认前必须补齐核心资料。')
    # 要保存的完整画像内容；必填 ProfileData；示例：{"summary": "四年 Python 开发经验"}。
    data: ProfileData = Field(title='画像内容', description='本次保存的完整画像，包含个人经历和求职条件。')

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
    revision: int = Field(title='画像修订号', ge=0, description='当前已保存画像的修订号，用于检测并发修改。')

    # profile：完整画像；必填，正式确认要求全部个人核心字段有效。
    profile: ProfileData = Field(title='完整画像', description='需要一次确认保存的完整个人画像及求职条件。')



def current_job_fields(data: dict, source_ids: list[str]) -> dict:
    """将旧来源关联转换成依据；文件删除后保留摘录，但解除失效的图片引用。"""
    if len(source_ids) > 1:
        raise ValueError('旧岗位关联了多张资料，请先整理为单张截图后迁移')
    result = {key: value for key, value in data.items() if key not in {'source_ids', 'jd_text', 'notes', 'salary_note', 'blind_spots'}}
    if 'job_description' not in result and 'jd_text' in data:
        result['job_description'] = data['jd_text']
    evidence = result.get('evidence')
    if evidence is None:
        evidence = [{'document_id': key, 'locator': '', 'text': ''} for key in source_ids]
    result['evidence'] = [
        {**item, 'document_id': item.get('document_id') if item.get('document_id') in source_ids else None}
        for item in evidence
    ]
    return result


class JobData(Payload):
    """正式保存的岗位结构；薪资为元/月或 None，evidence 保留提取依据。"""
    # 公司名称；最多 200 字符，未披露时留空。
    company: str = Field(title='公司名称', default='', max_length=200, description='招聘方的公司名称；未披露时留空。')
    # 岗位名称；最多 200 字符，未披露时留空。
    title: str = Field(title='岗位名称', default='', max_length=200, description='招聘岗位的职位名称；未披露时留空。')
    # 工作城市；可省略，默认 ""，最多 100 字符；示例："杭州"。
    city: str = Field(title='工作城市', default='', max_length=100, description='岗位实际工作城市，区别于求职者期望城市；未知时留空。')
    # 具体办公地址，用于通勤查询；可省略，默认 ""，最多 300 字符；示例："杭州市滨江区某园区"。
    address: str = Field(title='办公地址', default='', max_length=300, description='岗位的具体办公地址，用于查询通勤路线；未知时留空，不推测门牌或园区。')
    # 招聘月薪下限，元/月；可省略，默认 None；整数 1—10000000；示例：18000。
    salary_min: int | None = Field(title='招聘月薪下限', default=None, gt=0, le=10000000, description='招聘月薪下限，单位元/月；未知或无法可靠换算时为 null，不用 0 代替。')
    # 招聘月薪上限，元/月；可省略，默认 None；整数 1—10000000；示例：26000。
    # 两端都已知时仍要求下限不高于上限；薪资不能确定时用 None，不用 0。
    salary_max: int | None = Field(title='招聘月薪上限', default=None, gt=0, le=10000000, description='招聘月薪上限，单位元/月；未知为 null，已知时不能低于下限。')
    job_description: str = Field(title='岗位职责', default='', max_length=50000, description='岗位工作内容和岗位职责；未披露时留空，不混入候选人经历。')
    job_requirement: str = Field(title='任职要求', default='', max_length=50000, description='招聘方对候选人的技能、能力及其他任职要求；未披露时留空。')
    experience_years: float | None = Field(title='最低工作年限', default=None, ge=0, le=80, allow_inf_nan=False, description='招聘方明确要求的最低工作年限，单位年；3—5年填3，不限经验或接受应届生填0，未说明填null。')
    education: str = Field(title='学历要求', default='', max_length=2000, description='招聘方要求的学历，例如本科及以上；未披露时留空。')
    complete: Literal[None, '完整'] = Field(title='岗位信息完整性', default='完整', description='如果岗位正文存在被隐藏的内容，如存在“...查看更多”此类代表文字被折叠隐藏的信息，本字段应该设置为None')
    # 多段依据可来自同一截图；粘贴原文可不关联文件，截图存在性由保存服务检查。
    evidence: list[dict[Literal['document_id', 'locator', 'text'], str | None]] = Field(
        title='提取依据', default_factory=list, max_length=100,
        description=(
            '岗位提取依据列表，每条是一个字典。'
            'document_id：已上传的招聘截图 ID，同一岗位的多段摘录共用一个 ID；粘贴原文时填 null。'
            'locator：摘录在截图或原文中的位置，如“薪资区域”或“第 2 段”，未知填空字符串。'
            'text：支持岗位字段的原文片段，照录原文；旧资料没有保留摘录时填空字符串。'
        ),
    )

    @model_validator(mode='after')
    def check_evidence(self):
        # 补齐省略的键，使存储和接口始终得到相同的三个字段。
        for item in self.evidence:
            if item.setdefault('document_id', None) == '':
                raise ValueError('截图 ID 须为非空字符串；粘贴原文请使用 null')
            for key, limit in (('locator', 500), ('text', 50000)):
                value = item.setdefault(key, '')
                if value is None or len(value) > limit:
                    raise ValueError(f'依据中的 {key} 须为不超过 {limit} 字的字符串')
        if len({item['document_id'] for item in self.evidence if item['document_id']}) > 1:
            raise ValueError('同一岗位的提取依据只能来自一张截图')
        return self

    @model_validator(mode='after')
    def salary_order(self):
        """拒绝已知薪资下限高于上限；未知的一端不参与比较，校验通过返回自身。"""
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError('薪资下限不能超过上限')
        return self


class JobUpdate(Payload):
    # 保存前读取的岗位修订号；必填整数，至少为 1；示例：2，服务在写入时检查是否仍有效。
    revision: int = Field(title='岗位修订号', ge=1, description='读取岗位时的当前修订号，用于防止覆盖他人的更新；不得自行递增。')
    # 更新后的完整岗位及来源；必填 JobData，省略可选子字段会采用默认值，而非保留旧值。
    # 示例：{"company": "星河科技", "title": "Agent 开发", "evidence": []}。
    data: JobData = Field(title='岗位内容', description='待保存的完整岗位资料及来源；提交后整体替换该岗位内容。')


class MemoryData(Payload):
    """已确认记忆的普通存取格式；本模型不推断内容，也不执行用户确认流程。"""
    # 要保存的记忆事实；必填，去首尾空白后长度 1—10000 字符；示例："用户优先考虑杭州岗位。"。
    content: str = Field(title='记忆内容', min_length=1, max_length=10000, description='经过用户确认、需要长期保留的事实内容。')
    # 记忆类别；可省略，默认 preference；限 preference（偏好）、profile（画像）、weakness（薄弱项）。
    # 示例："weakness"，具体内容仍填 content，不由 category 自动生成。
    category: Literal['preference', 'profile', 'weakness'] = Field(title='记忆类别', default='preference', description='记忆类别：preference 表示求职偏好，profile 表示个人事实，weakness 表示待练习的薄弱项。')
    # 这条记忆的确认依据；可省略，默认 ""，最多 10000 字符；示例："用户在聊天中明确确认。"。
    evidence: str = Field(title='确认依据', default='', max_length=10000, description='这条记忆的确认依据或来源说明；没有可记录依据时留空。')
