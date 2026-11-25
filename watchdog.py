#!/usr/bin/env python3
import concurrent.futures as cf
import ipaddress
import json
import logging
import subprocess
import time
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass, field

INTERVAL = 60
FAILURES = 3
COOLDOWN = 120
MAX_RESTARTS = 3
WINDOW = 3600
LOG = logging.getLogger('watchdog')


def docker(*args, timeout=20):
    return subprocess.check_output(['docker', *args], text=True, timeout=timeout)


@dataclass
class State:
    failures: int = 0
    cooldown: float = 0
    restarts: deque = field(default_factory=deque)


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
                values = (c['NetworkSettings'].get('Ports') or {}).get(key) or values
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


def healthy(containers, scheme, path, host, endpoints):
    if not any(c['State']['Running'] for c in containers):
        return False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for ip, port in sorted(endpoints, key=lambda x: ':' in x[0]):
        address = f'[{ip}]' if ':' in ip else ip
        req = urllib.request.Request(f'{scheme}://{address}:{port}{path}', headers={'Host': host} if host else {})
        try:
            with opener.open(req, timeout=5) as response:
                if 200 <= response.status < 400:
                    return True
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400:
                return True
        except (OSError, ValueError):
            pass
    return False


def check(project, containers, state):
    now = time.monotonic()
    if now < state.cooldown:
        return
    try:
        directory, files, scheme, path, host, endpoints = target(containers)
    except (ValueError, KeyError) as exc:
        state.failures = 0
        LOG.error('%s: configuration error: %s', project, exc)
        return
    if healthy(containers, scheme, path, host, endpoints):
        state.failures = 0
        LOG.info('%s: OK', project)
        return
    state.failures = min(FAILURES, state.failures + 1)
    LOG.warning('%s: FAIL %s/%s', project, state.failures, FAILURES)
    if state.failures < FAILURES:
        return
    while state.restarts and now - state.restarts[0] >= WINDOW:
        state.restarts.popleft()
    if len(state.restarts) >= MAX_RESTARTS:
        LOG.error('%s: restart limit reached', project)
        return
    cmd = ['docker', 'compose', '--project-directory', directory, '-p', project]
    for f in files:
        cmd.extend(['-f', f])
    state.restarts.append(now)
    try:
        LOG.warning('%s: restarting all services', project)
        subprocess.run(cmd + ['restart'], cwd=directory, check=True, timeout=180)
        LOG.info('%s: restart completed', project)
    except (OSError, subprocess.SubprocessError):
        LOG.exception('%s: restart failed', project)
    finally:
        state.failures = 0
        state.cooldown = time.monotonic() + COOLDOWN


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    states = defaultdict(State)
    pending = {}
    with cf.ThreadPoolExecutor(max_workers=32) as pool:
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
            except (OSError, ValueError, subprocess.SubprocessError):
                LOG.exception('Docker discovery failed; skipping cycle')
            else:
                for project, containers in projects.items():
                    if project not in pending:
                        pending[project] = pool.submit(check, project, containers, states[project])
                for project in list(states):
                    if project not in projects and project not in pending:
                        del states[project]
            time.sleep(max(0, INTERVAL - (time.monotonic() - started)))


if __name__ == '__main__':
    main()
