"""Isolated UI verification: does not read/write the user's project database."""
import json
from pathlib import Path
import uuid

import uvicorn

from backend.app import create_app
from backend.config import Settings
from backend.database import initialize_database

ROOT = Path(__file__).resolve().parents[1]


if __name__ == '__main__':
    values = json.loads((ROOT / '.runtime/test-mysql.json').read_text())
    values = {key: value for key, value in values.items() if key.startswith('mysql_')}
    database = 'job_search_agent_visual_' + uuid.uuid4().hex[:8]
    settings = Settings(_env_file=None, **values, mysql_database=database, agent_mode='demo',
                        data_dir=ROOT / '.runtime' / database)
    initialize_database(settings, create_database=True)
    (ROOT / '.runtime/visual-preview.json').write_text(json.dumps({'database': database, 'port': 8001}), encoding='utf-8')
    uvicorn.run(create_app(settings), host='127.0.0.1', port=8001, log_level='warning')
