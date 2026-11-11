"""
Доступ к хостингу по SSH — чтобы оркестратор и агент-DevOps могли смотреть и
работать на сервере (Откликс, siberi.online). Безопасно по умолчанию:

- Хост задаёшь ТЫ в company/state/hosts.json или env AV_SSH_HOST. Ничего не
  хардкодится и живьём не подключается без твоей настройки. Запись хоста может
  быть строкой "user@host" или объектом:
      { "otkliks": { "ssh": "root@1.2.3.4", "port": 22,
                     "key": "C:\\Users\\user\\.ssh\\orchestrator",
                     "repo": "/var/www/hhpilot", "pm2": ["otkliks-api"] } }
- Приватный ключ НЕ читаем и не копируем — системный ssh берёт его сам по пути `key`.
- Чтение (ls, cat, git status, логи) — выполняется. ИЗМЕНЯЮЩИЕ/опасные команды
  (rm, deploy, git push, systemctl, docker down, перезапуск, запись в файл) —
  распознаются как рискованные и уходят на подтверждение человека.

  hosts()                     → известные хосты (как в конфиге)
  entry(host)                 → нормализованная запись {ssh, port, key, repo, pm2}
  run(host, command)          → {'rc', 'out', 'err'}
  is_write(command)           → меняет ли что-то на сервере
"""

import json
import os
import re
import shutil
import subprocess
from typing import Dict

from . import paths

HOSTS = paths.STATE / 'hosts.json'

# Маркеры команд, меняющих состояние сервера (идут через подтверждение человека).
WRITE = re.compile(
    r'(\brm\b|\bmv\b|\bdd\b|mkfs|\bchmod\b|\bchown\b|>{1,2}\s|\btee\b|truncate|'
    r'git\s+(push|reset|checkout|clean|rebase)|npm\s+(install|i|ci|run\s+build|publish)|'
    r'pip\s+install|systemctl|service\s|\breboot\b|shutdown|docker\s+(compose\s+)?(down|rm|stop|restart|up)|'
    r'kill\b|pm2\s+(restart|stop|delete)|deploy|migrat|drop\s+table|delete\s+from|psql|mysql\s)',
    re.IGNORECASE)


def hosts() -> Dict[str, object]:
    env = os.environ.get('AV_SSH_HOST')
    data = {}
    if HOSTS.exists():
        try:
            data = json.loads(HOSTS.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
    if env:
        data.setdefault('default', env)
    return data


def entry(host: str) -> dict:
    """Нормализованная запись хоста: {ssh, port, key, repo, pm2}. Поддержаны
    строка "user@host", объект и алиасы (строка-значение, указывающая на другой ключ)."""
    known = hosts()
    cur, seen = (host or 'default'), set()
    while cur in known and isinstance(known[cur], str) and known[cur] in known and cur not in seen:
        seen.add(cur); cur = known[cur]                 # alias → настоящая запись
    e = known.get(cur, cur)                             # имя из конфига или уже готовый user@host
    if isinstance(e, str):
        e = {'ssh': e}
    elif isinstance(e, dict):
        e = dict(e)
    else:
        e = {}
    return e


def resolve(host: str) -> str:
    """Имя из конфига → user@host; если передали уже user@host — вернём как есть."""
    return entry(host).get('ssh', '') or ''


def repo(host: str) -> str:
    return entry(host).get('repo', '') or ''


def pm2(host: str) -> list:
    return list(entry(host).get('pm2', []) or [])


def enabled(host: str = '') -> bool:
    return shutil.which('ssh') is not None and bool(resolve(host))


def is_write(command: str) -> bool:
    return bool(WRITE.search(command or ''))


def run(host: str, command: str, timeout: float = 60.0) -> dict:
    """Выполнить команду на сервере по SSH. → {'rc', 'out', 'err'}."""
    e = entry(host)
    target = e.get('ssh')
    if not target:
        raise RuntimeError('SSH-хост не задан (company/state/hosts.json или AV_SSH_HOST)')
    if shutil.which('ssh') is None:
        raise RuntimeError('ssh не найден в PATH')
    # BatchMode — не зависать на интерактивном вводе пароля; ключ берёт системный ssh
    argv = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new']
    if e.get('key'):
        argv += ['-i', str(e['key'])]
    if e.get('port') and int(e['port']) != 22:
        argv += ['-p', str(e['port'])]
    argv += [target, command]
    proc = subprocess.run(argv, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout,
                          creationflags=0x08000000 if os.name == 'nt' else 0)   # без окна консоли
    return {'rc': proc.returncode, 'out': (proc.stdout or '').strip(), 'err': (proc.stderr or '').strip()}
