import json
from pathlib import Path
import uuid

import pymysql
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.database import initialize_database


@pytest.fixture
def settings(tmp_path):
    config_file = Path('.runtime/test-mysql.json')
    if not config_file.exists():
        pytest.skip('Start isolated MySQL: python scripts/test_mysql.py start')
    config = json.loads(config_file.read_text())
    database = 'job_search_agent_test_' + uuid.uuid4().hex[:12]
    kwargs = {k: v for k, v in config.items() if k.startswith('mysql_')}
    value = Settings(_env_file=None, **kwargs, mysql_database=database,
                     data_dir=tmp_path / 'data', agent_mode='demo')
    initialize_database(value, create_database=True)
    yield value
    assert database.startswith('job_search_agent_test_')
    with pymysql.connect(host=kwargs['mysql_host'], port=kwargs['mysql_port'],
                         user=kwargs['mysql_user'], password=kwargs['mysql_password']) as conn:
        with conn.cursor() as cursor:
            cursor.execute(f'DROP DATABASE `{database}`')


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as value:
        yield value
