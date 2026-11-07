"""检查画像和岗位正式入库所需的业务字段。"""
from pydantic import BaseModel
from ..schemas import JobData, ProfileData

FOLDED_JOB_MESSAGE = '岗位正文被折叠，请展开“查看更多”后重新截图上传。'


def check_dict_missing(wait_check: dict, expect: type[BaseModel]) -> list[dict]:
    missing = []
    for field, info in expect.model_fields.items():
        if field == 'evidence':  # 提取依据由程序生成，不要求用户补充。
            continue
        value = wait_check.get(field)
        if (value is None or value == '' or value == {} or
                (isinstance(value, list) and not any(value)) or
                (expect is JobData and value == '未披露')):
            path = f'profile.{field}' if expect is ProfileData else field
            missing.append({'field': path, 'label': info.title})
    return missing
