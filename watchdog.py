#!/usr/bin/env python3
import argparse
import configparser
import os
import tempfile
import fcntl
from pathlib import Path
import concurrent.futures as cf
import ipaddress
import json
import logging
import subprocess
import time
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass, field

DEFAULTS = dict(interval=60, failures=3, cooldown=120, max_restarts=3,
                window=3600, http_timeout=5, command_timeout=180, workers=32,
                mode='restart', dry_run=False)
MODES = {'restart', 'down-up', 'force-recreate'}
LOG = logging.getLogger('watchdog')


def options(raw):
    result = DEFAULTS.copy()
    for key, value in raw.items():
        if key not in result:
            raise ValueError(f'unknown option: {key}')
        if key == 'mode':
            if value not in MODES:
                raise ValueError(f'invalid mode: {value}')
        elif key == 'dry_run':
            if str(value).lower() not in ('true', 'false', '1', '0', 'yes', 'no'):
                raise ValueError('invalid dry_run')
            value = str(value).lower() in ('true', '1', 'yes')
        else:
            value = int(value)
            if value < (0 if key == 'cooldown' else 1):
                raise ValueError(f'invalid {key}: {value}')
        result[key] = value
    return result


def load_config(path):
    config = configparser.ConfigParser(interpolation=None)
    if Path(path).exists():
        with open(path) as f:
            config.read_file(f)
    return options(dict(config['watchdog']) if config.has_section('watchdog') else {})


def project_options(containers, config):
    keys = ('failures', 'cooldown', 'max_restarts', 'window', 'http_timeout',
            'command_timeout', 'mode', 'interval')
    overrides = []
    for c in containers:
        labels = c['Config']['Labels']
        overrides.append({k: labels['watchdog.' + k] for k in keys if 'watchdog.' + k in labels})
    if any(v != overrides[0] for v in overrides):
        raise ValueError('conflicting watchdog labels')
    return options({**config, **overrides[0]})


def docker(*args, timeout=20):
    return subprocess.check_output(['docker', *args], text=True, timeout=timeout)


@dataclass
class State:
    failures: int = 0
    cooldown: float = 0
    restarts: deque = field(default_factory=deque)
    recovery: str = ""
    recovery_cmd: list = field(default_factory=list)
    recovery_options: dict = field(default_factory=dict)
    next_check: float = 0


def discover():
    ids = docker('ps', '-aq', '--filter', 'label=watchdog.enabled=true').split()
    groups = defaultdict(list)
    if ids:
        for c in json.loads(docker('inspect', *ids)):
            labels = c['Config']['Labels'] or {}
            project = labels.get('com.docker.compose.project')
            if project and labels.get('com.docker.compose.oneoff', '').lower() != 'true':
                groups[project].append(c)
    return groups


def target(containers):
    configs, endpoints = set(), set()
    for c in containers:
        labels = c['Config']['Labels']
        directory = labels.get('com.docker.compose.project.working_dir')
        files = labels.get('com.docker.compose.project.config_files')
        scheme = labels.get('watchdog.scheme', 'http')
        path = labels.get('watchdog.path', '/')
        host = labels.get('watchdog.host', '')
        if not directory or not files or scheme not in ('http', 'https') or not path.startswith('/') or path.startswith('//'):
            raise ValueError('missing or invalid project metadata / health configuration')
        configs.add((directory, files, scheme, path, host, labels.get('com.docker.compose.service')))
        bindings = c['HostConfig'].get('PortBindings') or {}
        for key, values in bindings.items():
            if not key.endswith('/tcp'):
                continue
            # Runtime bindings resolve dynamically allocated host ports.
            if c['State']['Running']:
                values = (c['NetworkSettings'].get('Ports') or {}).get(key) or []
            for b in values or []:
                port = int(b.get('HostPort') or 0)
                if not port:
                    continue
                ip = b.get('HostIp') or '0.0.0.0'
                ip = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(ip, ip)
                ipaddress.ip_address(ip)
                endpoints.add((ip, port))
    if len(configs) != 1 or len({p for _, p in endpoints}) > 1:
        raise ValueError('ambiguous configuration or multiple published ports')
    directory, files, scheme, path, host, _ = configs.pop()
    return directory, files.split(','), scheme, path, host, endpoints


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def healthy(containers, scheme, path, host, endpoints, timeout=5):
    if not any(c['State']['Running'] for c in containers):
        return False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for ip, port in sorted(endpoints, key=lambda x: ':' in x[0]):
        address = f'[{ip}]' if ':' in ip else ip
        req = urllib.request.Request(f'{scheme}://{address}:{port}{path}', headers={'Host': host} if host else {})
        try:
            with opener.open(req, timeout=timeout) as response:
                if 200 <= response.status < 400:
                    return True
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400:
                return True
        except (OSError, ValueError):
            pass
    return False


class Store:
    # Separate files avoid concurrent writes to the same state by different projects.
    def __init__(self, directory, readonly=False):
        self.directory = Path(directory)
        self.readonly = readonly
        if not readonly:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    def path(self, project):
        import hashlib
        return self.directory / (hashlib.sha256(project.encode()).hexdigest() + '.json')

    def load(self, project):
        path = self.path(project)
        if not path.exists():
            return State()
        with path.open() as f:
            d = json.load(f)
        if d.pop('project') != project:
            raise ValueError('state project mismatch')
        d['restarts'] = deque(d['restarts'])
        return State(**d)

    def save(self, project, state):
        if self.readonly:
            return
        d = dict(vars(state), project=project, restarts=list(state.restarts))
        fd, name = tempfile.mkstemp(dir=self.directory)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(d, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.path(project))
            fd = os.open(self.directory, os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def recovering(self):
        for path in self.directory.glob('*.json'):
            with path.open() as f:
                d = json.load(f)
            if d.get('recovery'):
                yield d['project']


def compose_command(project, directory, files):
    cmd = ['docker', 'compose', '--project-directory', directory, '-p', project]
    for f in files:
        cmd.extend(['-f', f])
    return cmd


def commands(cmd, mode):
    if mode == 'down-up':
        return [cmd + ['down'], cmd + ['up', '-d']]
    if mode == 'force-recreate':
        return [cmd + ['up', '-d', '--force-recreate']]
    return [cmd + ['restart']]


def run_command(cmd, timeout):
    subprocess.run(cmd, check=True, timeout=timeout)


def recover(project, state, store, cfg):
    # A durable intent is written before down; if the daemon or host dies,
    # up is retried even if all labelled containers have disappeared.
    if cfg['dry_run']:
        LOG.warning('%s: dry-run: pending recovery %s', project, state.recovery_cmd)
        return
    now = time.time()
    if now < state.cooldown:
        return
    state.restarts = deque(t for t in state.restarts if now - t < cfg['window'])
    if len(state.restarts) >= cfg['max_restarts']:
        LOG.error('%s: pending recovery limited; retry after window', project)
        return
    state.restarts.append(now)
    store.save(project, state)
    try:
        run_command(state.recovery_cmd, cfg['command_timeout'])
        state.recovery = ''
        state.recovery_cmd = []
        LOG.info('%s: pending up completed', project)
    except (OSError, subprocess.SubprocessError):
        LOG.exception('%s: pending up failed', project)
    finally:
        state.cooldown = time.time() + cfg['cooldown']
        store.save(project, state)


def check(project, containers, store, config):
    state = store.load(project)
    cfg = project_options(containers, config) if containers else config
    now = time.time()
    if state.recovery:
        cfg = {**cfg, **state.recovery_options, 'dry_run': config['dry_run']}
        recover(project, state, store, cfg)
        return
    if not containers or now < max(state.cooldown, state.next_check):
        return
    state.next_check = now + cfg['interval']
    try:
        directory, files, scheme, path, host, endpoints = target(containers)
        if not Path(directory).is_dir() or not all(Path(directory, f).is_file() for f in files):
            raise ValueError('missing compose directory or files')
        if any(c['State']['Running'] for c in containers) and not endpoints:
            raise ValueError('running container has no published TCP port')
    except (ValueError, KeyError) as exc:
        state.failures = 0
        store.save(project, state)
        LOG.error('%s: configuration error: %s', project, exc)
        return
    if healthy(containers, scheme, path, host, endpoints, cfg['http_timeout']):
        state.failures = 0
        store.save(project, state)
        LOG.info('%s: OK', project)
        return
    state.failures = min(cfg['failures'], state.failures + 1)
    LOG.warning('%s: FAIL %s/%s', project, state.failures, cfg['failures'])
    store.save(project, state)
    if state.failures < cfg['failures']:
        return
    state.restarts = deque(t for t in state.restarts if now - t < cfg['window'])
    if len(state.restarts) >= cfg['max_restarts']:
        LOG.error('%s: restart limit reached', project)
        return
    cmds = commands(compose_command(project, directory, files), cfg['mode'])
    if cfg['dry_run']:
        LOG.warning('%s: dry-run: %s', project, cmds)
        return
    state.restarts.append(now)
    if cfg['mode'] == 'down-up':
        state.recovery = 'up'
        state.recovery_cmd = cmds[1]
        state.recovery_options = {k: cfg[k] for k in ('window', 'max_restarts', 'command_timeout', 'cooldown')}
    store.save(project, state)
    try:
        LOG.warning('%s: recovery mode=%s', project, cfg['mode'])
        if cfg['mode'] == 'down-up':
            # Attempt up even when down errors or times out (partial teardown).
            try:
                run_command(cmds[0], cfg['command_timeout'])
            except (OSError, subprocess.SubprocessError):
                LOG.exception('%s: down failed; attempting up anyway', project)
            run_command(cmds[1], cfg['command_timeout'])
            state.recovery = ''
            state.recovery_cmd = []
        else:
            run_command(cmds[0], cfg['command_timeout'])
        LOG.info('%s: recovery completed', project)
    except (OSError, subprocess.SubprocessError):
        LOG.exception('%s: recovery failed', project)
    finally:
        state.failures = 0
        state.cooldown = time.time() + cfg['cooldown']
        store.save(project, state)


def main():
    parser = argparse.ArgumentParser(description='Docker Compose watchdog')
    parser.add_argument('--config', default='/etc/docker-watchdog/watchdog.conf')
    parser.add_argument('--state-dir', default='/var/lib/docker-watchdog')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--version', action='version', version='1.1.0')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    config = load_config(args.config)
    config['dry_run'] = config['dry_run'] or args.dry_run
    if args.list:
        for project, containers in sorted(discover().items()):
            try:
                directory, files, scheme, path, host, endpoints = target(containers)
                cfg = project_options(containers, config)
                print(json.dumps(dict(project=project, directory=directory, files=files,
                    scheme=scheme, path=path, host=host, endpoints=sorted(endpoints), mode=cfg['mode'])))
            except (ValueError, KeyError) as exc:
                print(json.dumps(dict(project=project, error=str(exc))))
        return
    store = Store(args.state_dir, readonly=config['dry_run'])
    # Dry-run keeps its own temporary state, not production counters.
    temp = tempfile.TemporaryDirectory() if config['dry_run'] else None
    if temp:
        store = Store(temp.name)
    lock = open(store.directory / '.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('another watchdog is running')
    pending = {}
    with cf.ThreadPoolExecutor(max_workers=config['workers']) as pool:
        while True:
            started = time.monotonic()
            for project, future in list(pending.items()):
                if future.done():
                    try:
                        future.result()
                    except Exception:
                        LOG.exception('%s: unexpected check error', project)
                    del pending[project]
            try:
                projects = discover()
                for project in store.recovering():
                    projects.setdefault(project, [])
            except (OSError, ValueError, subprocess.SubprocessError):
                LOG.exception('Docker discovery/state failed; skipping cycle')
                if args.once:
                    raise
            else:
                for project, containers in projects.items():
                    if project not in pending:
                        pending[project] = pool.submit(check, project, containers, store, config)
            if args.once:
                for future in pending.values():
                    future.result()
                return
            # Poll discovery often enough for shorter per-project intervals.
            time.sleep(max(0, min(5, config['interval']) - (time.monotonic() - started)))


if __name__ == '__main__':
    main()
