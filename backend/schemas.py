from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Payload(BaseModel):
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
    revision: int = Field(ge=0)
    confirmed: bool
    data: ProfileData

    @model_validator(mode='after')
    def nonempty_confirmation(self):
        if self.confirmed and not (self.data.summary or self.data.experience or self.data.skills):
            raise ValueError('请至少填写简介、经历或技能后再确认画像')
        return self


class Preferences(Payload):
    city: str = Field(default='', max_length=100)
    direction: str = Field(default='Python Agent 应用开发', max_length=200)
    salary_min: int | None = Field(default=None, gt=0, le=10000000)
    salary_max: int | None = Field(default=None, gt=0, le=10000000)
    commute_origin: str = Field(default='', max_length=300)
    commute_limit_minutes: int = Field(default=60, gt=0, le=1440)
    commute_mode: Literal['public_transit', 'driving'] = 'public_transit'

    @model_validator(mode='after')
    def salary_order(self):
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError('薪资下限不能超过上限')
        return self


class PreferencesUpdate(Payload):
    revision: int = Field(ge=0)
    data: Preferences


class JobData(Payload):
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
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError('薪资下限不能超过上限')
        return self


class JobUpdate(Payload):
    revision: int = Field(ge=1)
    data: JobData


class QuestionData(Payload):
    document_id: str | None = None
    question: str = Field(min_length=1, max_length=10000)
    answer: str = Field(default='', max_length=30000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    locator: str = Field(default='', max_length=500)


class MemoryData(Payload):
    content: str = Field(min_length=1, max_length=10000)
    category: Literal['preference', 'profile', 'weakness'] = 'preference'
    evidence: str = Field(default='', max_length=10000)
