import argparse

from sqlalchemy.exc import SQLAlchemyError

from .config import Settings
from .database import initialize_database


def main():
    parser = argparse.ArgumentParser(description='只创建项目库和缺失的表，不清空已有数据')
    parser.add_argument('--create-database', action='store_true')
    args = parser.parse_args()
    settings = Settings()
    try:
        initialize_database(settings, create_database=args.create_database)
    except SQLAlchemyError:
        raise SystemExit('MySQL 初始化失败。请检查 .env、服务状态以及账号建库/建表权限；未修改已有数据。')
    print(f'数据库 {settings.mysql_database} 已就绪；已有表和记录保留。')


if __name__ == '__main__':
    main()
