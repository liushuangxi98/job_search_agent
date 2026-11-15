"""提供 MySQL 初始化命令，将数据库连接或权限错误转换成命令行失败提示。"""
import argparse
import traceback

from sqlalchemy.exc import SQLAlchemyError
from pydantic import ValidationError

from .config import Settings
from .database import initialize_database


def main():
    """读取 --create-database 后执行初始化；成功打印提示，数据库失败则退出进程。"""
    parser = argparse.ArgumentParser(description='初始化项目库，并将旧求职条件合并到 profile；请先停止应用')
    parser.add_argument('--create-database', action='store_true')
    args = parser.parse_args()
    settings = Settings()
    try:
        initialize_database(settings, create_database=args.create_database)
    except ValidationError:
        traceback.print_exc()
        raise SystemExit('旧个人资料不符合当前字段约束，合并已回滚。请检查旧画像和求职条件的数据。')
    except ValueError as exc:
        traceback.print_exc()
        raise SystemExit(str(exc)) from None
    except SQLAlchemyError:
        traceback.print_exc()
        raise SystemExit('MySQL 初始化或迁移失败。请检查连接及建表/ALTER 权限；若合并已提交可安全重跑。')
    print(f'数据库 {settings.mysql_database} 已就绪；个人资料统一保存在 profile，历史报告保留。')


if __name__ == '__main__':
    main()
