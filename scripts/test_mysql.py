"""Start/stop an isolated local MySQL for application tests; never uses the user's DB."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import time

import pymysql

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / '.runtime' / 'test-mysql.json'


def connect(config):
    return pymysql.connect(host='127.0.0.1', port=config['mysql_port'],
                           user='root', password=config['mysql_password'], connect_timeout=2)


def start():
    CONFIG.parent.mkdir(exist_ok=True)
    if CONFIG.exists():
        config = json.loads(CONFIG.read_text())
        try:
            with connect(config):
                print(f"Isolated MySQL already available on port {config['mysql_port']}")
                return
        except pymysql.MySQLError:
            pass
    binary = shutil.which('mysqld') or str(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'MySQL/MySQL Server 8.0/bin/mysqld.exe')
    if not Path(binary).is_file():
        raise SystemExit('mysqld not found; install MySQL or add its bin directory to PATH.')
    run_dir = CONFIG.parent / ('mysql-' + secrets.token_hex(4))
    run_dir.mkdir()
    datadir = run_dir / 'data'
    hidden = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with (run_dir / 'initialize.log').open('wb') as log:
        subprocess.run([binary, '--no-defaults', '--initialize-insecure', f'--datadir={datadir}'],
                       stdout=log, stderr=subprocess.STDOUT, check=True, creationflags=hidden)
    password = secrets.token_hex(24)
    init = run_dir / 'initialize-user.sql'
    init.write_text(f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{password}';\n", encoding='utf-8')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    args = [binary, '--no-defaults', f'--datadir={datadir}', '--bind-address=127.0.0.1',
            f'--port={port}', '--mysqlx=0', '--skip-log-bin', '--innodb-buffer-pool-size=64M',
            f'--init-file={init}', f'--log-error={run_dir / "server.log"}']
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=hidden)
    config = {'mysql_host': '127.0.0.1', 'mysql_port': port, 'mysql_user': 'root',
              'mysql_password': password, 'datadir': str(datadir), 'pid': process.pid}
    for _ in range(60):
        if process.poll() is not None:
            raise SystemExit(f'MySQL failed to start; inspect {run_dir / "server.log"}')
        try:
            with connect(config):
                CONFIG.write_text(json.dumps(config), encoding='utf-8')
                init.unlink()
                print(f'Isolated MySQL ready on 127.0.0.1:{port}; credentials saved locally, not printed.')
                return
        except pymysql.MySQLError:
            time.sleep(0.5)
    process.terminate()
    raise SystemExit('Isolated MySQL startup timed out.')


def stop():
    if not CONFIG.exists():
        return
    config = json.loads(CONFIG.read_text())
    expected = Path(config['datadir']).resolve()
    if not expected.is_relative_to(CONFIG.parent.resolve()):
        raise SystemExit('Refusing to stop a MySQL outside the test workspace.')
    with connect(config) as conn:
        with conn.cursor() as cursor:
            cursor.execute('SELECT @@datadir')
            if Path(cursor.fetchone()[0]).resolve() != expected:
                raise SystemExit('Server data directory differs; refusing shutdown.')
            cursor.execute('SHUTDOWN')
    CONFIG.unlink()
    print('Isolated MySQL stopped. Test data and logs retained under .runtime/.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'stop'])
    args = parser.parse_args()
    start() if args.action == 'start' else stop()
