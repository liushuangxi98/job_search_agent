"""工作台升级后应重新校验页面和脚本，避免新 HTML 搭配旧 JavaScript。"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings


def test_workbench_assets_require_revalidation_without_changing_api_cache(tmp_path):
    app = create_app(Settings(_env_file=None, agent_mode='demo', data_dir=tmp_path))
    with TestClient(app) as client:
        for path in ('/', '/static/app.js', '/static/api.js', '/static/styles.css'):
            first = client.get(path)
            assert first.status_code == 200
            assert first.headers.get('cache-control') == 'no-cache'
        assert 'cache-control' not in client.get('/api/demo/examples').headers


def test_static_asset_revalidation_keeps_cache_until_content_changes(tmp_path):
    from backend.api.middleware import revalidate_static_assets

    asset = tmp_path / 'app.js'
    asset.write_text('window.version = 1;', encoding='utf-8')
    app = FastAPI()
    app.middleware('http')(revalidate_static_assets)
    app.mount('/static', StaticFiles(directory=tmp_path))
    with TestClient(app) as client:
        first = client.get('/static/app.js')
        etag = first.headers['etag']
        unchanged = client.get('/static/app.js', headers={'If-None-Match': etag})
        assert unchanged.status_code == 304
        assert unchanged.headers['cache-control'] == 'no-cache'
        asset.write_text('window.version = 2; // new release', encoding='utf-8')
        updated = client.get('/static/app.js', headers={'If-None-Match': etag})
        assert updated.status_code == 200
        assert updated.text == 'window.version = 2; // new release'
        assert updated.headers['etag'] != etag
        assert updated.headers['cache-control'] == 'no-cache'
