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
import threading
import http.server
import contextlib
import datetime
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field

DEFAULTS = dict(interval=60, failures=3, cooldown=120, max_restarts=3,
                window=3600, http_timeout=5, command_timeout=180, workers=32,
                mode='restart', dry_run=False, startup_grace=60, recovery='',
                log_success=True, log_level='INFO', summary_interval=600,
                docker_events=True, webhook_url='', metrics_port=0,
                pre_hook='', post_hook='', hook_timeout=30, check='http',
                healthcheck_failures=0, project_failure_threshold=2, project_recovery_mode='restart')
VERSION = '1.3.0'
PROJECT_MODES = {'restart', 'down-up', 'force-recreate'}
CONTAINER_MODES = {'container-restart', 'container-recreate'}
MODES = PROJECT_MODES | CONTAINER_MODES
CHECKS = {'http', 'healthcheck', 'both', 'auto'}
LOG = logging.getLogger('watchdog')


def options(raw):
    result = DEFAULTS.copy()
    for key, value in raw.items():
        if key not in result:
            raise ValueError(f'unknown option: {key}')
        if key == 'mode':
            if value not in MODES:
                raise ValueError(f'invalid mode: {value}')
        elif key == 'project_recovery_mode':
            if value not in PROJECT_MODES: raise ValueError('invalid project_recovery_mode')
        elif key == 'check':
            if value not in CHECKS: raise ValueError('invalid check method')
        elif key == 'recovery':
            if value and any(m not in MODES for m in value.split(',')):
                raise ValueError('invalid recovery sequence')
        elif key == 'log_level':
            value = value.upper()
            if value not in ('DEBUG', 'INFO', 'WARNING', 'ERROR'): raise ValueError('invalid log_level')
        elif key in ('webhook_url', 'pre_hook', 'post_hook'):
            if key == 'webhook_url' and value and not value.startswith('https://'): raise ValueError('webhook requires HTTPS')
            if key.endswith('_hook') and value and not os.path.isabs(value): raise ValueError('hook must be absolute path')
        elif key in ('dry_run', 'log_success', 'docker_events'):
            if str(value).lower() not in ('true', 'false', '1', '0', 'yes', 'no'):
                raise ValueError('invalid dry_run')
            value = str(value).lower() in ('true', '1', 'yes')
        else:
            value = int(value)
            if key == 'metrics_port' and value > 65535: raise ValueError('invalid metrics port')
            if value < (0 if key in ('cooldown', 'max_restarts', 'startup_grace', 'summary_interval', 'metrics_port', 'healthcheck_failures', 'project_failure_threshold') else 1):
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
            'command_timeout', 'mode', 'interval', 'startup_grace', 'recovery', 'check',
            'healthcheck_failures', 'project_failure_threshold', 'project_recovery_mode')
    overrides = []
    for c in containers:
        labels = c['Config']['Labels']
        overrides.append({k: labels['watchdog.' + k] for k in keys if 'watchdog.' + k in labels})
    if any(v != overrides[0] for v in overrides):
        raise ValueError('conflicting watchdog labels')
    if 'mode' in overrides[0] and 'recovery' not in overrides[0]:
        overrides[0]['recovery'] = ''
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
    history: list = field(default_factory=list)
    escalation: int = 0
    blocked: bool = False
    status: str = "unknown"
    last_check: float = 0
    last_detail: str = ""
    generation: str = ""
    grace_until: float = 0
    targets: dict = field(default_factory=dict)


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


def target(containers, include_ports=True):
    configs, endpoints = set(), set()
    for c in containers:
        labels = c['Config']['Labels']
        directory = labels.get('com.docker.compose.project.working_dir')
        files = labels.get('com.docker.compose.project.config_files')
        scheme = labels.get('watchdog.scheme', 'http') if include_ports else 'http'
        path = labels.get('watchdog.path', '/') if include_ports else '/'
        host = labels.get('watchdog.host', '') if include_ports else ''
        if not directory or not files or scheme not in ('http', 'https') or not path.startswith('/') or path.startswith('//'):
            raise ValueError('missing or invalid project metadata / health configuration')
        configs.add((directory, files, scheme, path, host, labels.get('com.docker.compose.service')))
        bindings = (c['HostConfig'].get('PortBindings') or {}) if include_ports else {}
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


PROBE = threading.local()

def healthy(containers, scheme, path, host, endpoints, timeout=5):
    PROBE.detail = "container stopped or no endpoint"
    started = time.monotonic()
    if not any(c['State']['Running'] for c in containers):
        return False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for ip, port in sorted(endpoints, key=lambda x: ':' in x[0]):
        address = f'[{ip}]' if ':' in ip else ip
        req = urllib.request.Request(f'{scheme}://{address}:{port}{path}', headers={'Host': host} if host else {})
        try:
            with opener.open(req, timeout=timeout) as response:
                PROBE.detail = f"HTTP {response.status} endpoint={address}:{port} duration={time.monotonic()-started:.3f}s"
                if 200 <= response.status < 400:
                    return True
        except urllib.error.HTTPError as e:
            PROBE.detail = f"HTTP {e.code} endpoint={address}:{port} duration={time.monotonic()-started:.3f}s"
            if 300 <= e.code < 400:
                return True
        except (OSError, ValueError) as e:
            PROBE.detail = f"{type(e).__name__}: {e} endpoint={address}:{port} duration={time.monotonic()-started:.3f}s"
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

    @contextlib.contextmanager
    def guard(self, project, blocking=True):
        with open(str(self.path(project)) + '.lock', 'a') as f:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            yield

    def pause_path(self, project):
        return self.path(project).with_suffix('.pause')

    def paused(self, project):
        try:
            until = float(self.pause_path(project).read_text())
            return until == 0 or until > time.time()
        except FileNotFoundError:
            return False

    def all_states(self):
        for path in self.directory.glob('*.json'):
            try:
                d = json.loads(path.read_text())
                yield d['project'], self.load(d['project'])
            except (OSError, ValueError, TypeError, KeyError):
                LOG.exception('Invalid state file %s', path)

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


def commands(cmd, mode, container=None, service=None):
    if mode == 'container-restart':
        if not container: raise ValueError('container ID required')
        return [['docker', 'restart', container]]
    if mode == 'container-recreate':
        if not service: raise ValueError('Compose service required')
        return [cmd + ['up', '-d', '--no-deps', '--force-recreate', '--', service]]
    if mode not in PROJECT_MODES: raise ValueError('invalid recovery mode')
    if mode == 'down-up':
        return [cmd + ['down'], cmd + ['up', '-d']]
    if mode == 'force-recreate':
        return [cmd + ['up', '-d', '--force-recreate']]
    return [cmd + ['restart']]


def run_command(cmd, timeout):
    subprocess.run(cmd, check=True, timeout=timeout)


def limited(project, state, store, cfg):
    limit = cfg['max_restarts']
    blocked = bool(limit and len(state.restarts) >= limit)
    if blocked and not state.blocked:
        LOG.error('%s: restart limit reached (%s/%s); next recovery allowed at %s', project,
                  len(state.restarts), limit, datetime.datetime.fromtimestamp(
                      state.restarts[len(state.restarts)-limit] + cfg['window']).isoformat())
    if state.blocked and not blocked:
        LOG.info('%s: recovery limit released', project)
    state.blocked = blocked
    store.save(project, state)
    return blocked


def record(state, mode, result):
    state.history.append(dict(time=time.time(), mode=mode, result=result))
    state.history = state.history[-500:]


def notify(project, cfg, event, mode, container=None):
    if not cfg['webhook_url'] or cfg['dry_run']:
        return
    try:
        payload = dict(project=project, event=event, mode=mode)
        if container: payload['container']=container
        body = json.dumps(payload).encode()
        req = urllib.request.Request(cfg['webhook_url'], data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=5): pass
    except Exception as e:
        LOG.warning('%s: webhook failed (%s)', project, type(e).__name__)


def hook(project, cfg, name, mode, result=''):
    if cfg[name] and not cfg['dry_run']:
        subprocess.run([cfg[name], project, mode, result], check=True, timeout=cfg['hook_timeout'])


def fresh(project, containers):
    current = discover().get(project, [])
    def snapshot(cs):
        return {(c.get('Id'), c['State'].get('StartedAt'), c['State'].get('Running'), (c['State'].get('Health') or {}).get('Status')) for c in cs}
    return bool(current) and snapshot(current) == snapshot(containers)


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
    if limited(project, state, store, cfg):
        return
    state.restarts.append(now)
    store.save(project, state)
    try:
        run_command(state.recovery_cmd, cfg['command_timeout'])
        state.recovery = ''
        state.recovery_cmd = []
        record(state, 'pending-up', 'completed')
        LOG.info('%s: pending up completed', project)
    except (OSError, subprocess.SubprocessError):
        record(state, 'pending-up', 'failed')
        LOG.exception('%s: pending up failed', project)
    finally:
        state.cooldown = time.time() + cfg['cooldown']
        store.save(project, state)


def target_key(c):
    labels = c['Config']['Labels']
    return labels.get('com.docker.compose.service', '') + ':' + labels.get('com.docker.compose.container-number', '1')


def target_name(c):
    return c.get('Name', '').lstrip('/') or target_key(c)


def has_healthcheck(c):
    test = (c['Config'].get('Healthcheck') or {}).get('Test') or []
    return test[0] != 'NONE' if test else 'Health' in c['State']


def check_method(c, cfg):
    if cfg['check'] == 'auto':
        return 'healthcheck' if has_healthcheck(c) else 'http'
    return cfg['check']


def probe(c, cfg, endpoints, scheme, path, host):
    """Return status, detail, and whether the Docker healthcheck failed."""
    method = check_method(c, cfg)
    if cfg.get('_error'): return 'config-error', cfg['_error'], False
    if method in ('healthcheck', 'both') and not has_healthcheck(c):
        return 'config-error', 'Docker Healthcheck is not configured', False
    if method in ('http', 'both') and c['State']['Running'] and not endpoints:
        return 'config-error', 'running container has no published TCP port', False
    if not c['State']['Running']:
        return 'failed', 'container not running', method in ('healthcheck', 'both')
    detail = ''
    if method in ('healthcheck', 'both'):
        health = (c['State'].get('Health') or {}).get('Status', 'starting')
        detail = 'Docker Healthcheck ' + health
        if health == 'starting': return 'starting', detail, False
        if health != 'healthy': return 'failed', detail, True
    if method in ('http', 'both'):
        ok = healthy([c], scheme, path, host, endpoints, cfg['http_timeout'])
        return ('healthy' if ok else 'failed'), (detail + ' ' + getattr(PROBE, 'detail', '')).strip(), False
    return 'healthy', detail, False


def project_policy(config, configs, containers):
    # Project-level coordination must not depend on discovery order. Shared
    # budget conservatively uses the strictest nonzero limit and longest window.
    result = dict(config)
    for k in ('project_failure_threshold', 'project_recovery_mode'):
        values = {options({k:c['Config']['Labels']['watchdog.'+k]})[k] for c in containers if 'watchdog.'+k in c['Config']['Labels']}
        if len(values) > 1: raise ValueError('conflicting project-wide option: ' + k)
        if values: result[k] = values.pop()
    if configs:
        limits = [c['max_restarts'] for c in configs if c['max_restarts']]
        result['max_restarts'] = min(limits) if limits else 0
        for k in ('window', 'cooldown', 'command_timeout'):
            result[k] = max(c[k] for c in configs)
    return result


def prepare_targets(containers, config):
    rows = []
    keys = set()
    projects = set()
    for c in sorted(containers, key=target_key):
        key = target_key(c)
        labels = c['Config']['Labels']
        if not labels.get('com.docker.compose.service'):
            raise ValueError('missing Compose service identity')
        if key in keys: raise ValueError('duplicate Compose container identity: ' + key)
        keys.add(key)
        directory = labels.get('com.docker.compose.project.working_dir')
        raw_files = labels.get('com.docker.compose.project.config_files')
        if not directory or not raw_files:
            raise ValueError('missing Compose project metadata')
        files = raw_files.split(',')
        if not Path(directory).is_dir() or not all(Path(directory, f).is_file() for f in files):
            raise ValueError('missing compose directory or files')
        projects.add((directory, tuple(files)))
        try:
            cfg = project_options([c], config)
            _, _, scheme, path, host, endpoints = target([c], include_ports=check_method(c,cfg)!='healthcheck')
        except (ValueError, KeyError, TypeError) as exc:
            # Bad endpoint/check labels must not silence a different container.
            try: cfg = project_options([c], config)
            except (ValueError, KeyError, TypeError): cfg = dict(config)
            cfg['_error'] = str(exc)
            scheme, path, host, endpoints = 'http', '/', '', set()
        rows.append((key, c, cfg, directory, files, scheme, path, host, endpoints))
    if len(projects) != 1: raise ValueError('conflicting Compose metadata')
    policy=project_policy(config, [row[2] for row in rows], containers)
    for row in rows:
        row[2].update({k:policy[k] for k in ('project_failure_threshold','project_recovery_mode')})
    return rows, policy


def validate_container_recreate(project, directory, files, service, container_id):
    if not service or not container_id: raise ValueError('missing container or service identity')
    ids = docker('ps','-aq','--filter','label=com.docker.compose.project='+project,
                 '--filter','label=com.docker.compose.service='+service).split()
    if ids != [container_id] and not (len(ids)==1 and container_id.startswith(ids[0])):
        raise ValueError('container-recreate requires exactly one existing service container')
    cfg=json.loads(subprocess.check_output(compose_command(project,directory,files)+['config','--format','json'],text=True,timeout=20))
    svc=cfg['services'][service]
    if svc.get('scale',1)!=1 or (svc.get('deploy') or {}).get('replicas',1)!=1:
        raise ValueError('container-recreate requires a service configured for one replica')


def aggregate_targets(state):
    statuses = {t.get('status','unknown') for t in state.targets.values()}
    state.status = next((v for v in ('failed','config-error','starting','unknown','healthy') if v in statuses),'unknown')
    if not state.targets: return
    latest = max(state.targets.values(),key=lambda t:t.get('last_check',0))
    state.last_check=latest.get('last_check',0); state.last_detail=latest.get('detail','')
    state.failures=max(t.get('failures',0) for t in state.targets.values())
    state.next_check=min(max(t.get('next_check',0),t.get('cooldown',0)) for t in state.targets.values())
    state.grace_until=min(t.get('grace_until',0) for t in state.targets.values()) if statuses=={'starting'} else 0


def check(project, containers, store, config, manual_mode=None, manual_target=None):
    with store.guard(project):
        return check_locked(project, containers, store, config, manual_mode, manual_target)


def check_locked(project, containers, store, config, manual_mode=None, manual_target=None):
    state = store.load(project)
    now = time.time()
    if store.paused(project): return
    if state.recovery:
        cfg = {**config, **state.recovery_options, 'dry_run': config['dry_run']}
        if containers:
            try: _, cfg = prepare_targets(containers, config)
            except (ValueError, KeyError): pass  # durable pending up takes priority
        recover(project, state, store, cfg)
        return
    if not containers: return
    try:
        rows, policy = prepare_targets(containers, config)
    except (ValueError, KeyError) as exc:
        state.status = 'config-error'; state.last_detail = str(exc); state.failures = 0
        store.save(project, state)
        LOG.error('%s: configuration error: %s', project, exc)
        return
    if manual_target and manual_mode not in CONTAINER_MODES:
        raise ValueError('--container is only for container recovery modes')
    if manual_mode and manual_target:
        matching = [r for r in rows if manual_target in (r[0], target_name(r[1]), r[1].get('Id'), r[1]['Config']['Labels'].get('com.docker.compose.service'))]
        if len(matching) != 1: raise ValueError('container selector must identify exactly one monitored container')
    else: matching = rows
    if manual_mode in CONTAINER_MODES and len(matching) != 1:
        raise ValueError('container recovery requires --container when multiple targets exist')
    if now < state.cooldown and not manual_mode: return
    keys = {r[0] for r in rows}
    dirty = bool(set(state.targets)-keys)
    state.targets = {k:v for k,v in state.targets.items() if k in keys}
    eligible = []
    probed = set()
    for key, c, cfg, directory, files, scheme, path, host, endpoints in rows:
        t = state.targets.setdefault(key, {})
        generation = c.get('Id', '') + ':' + c['State'].get('StartedAt', '')
        if t.get('generation') != generation:
            dirty=True
            escalation = t.get('escalation', 0)
            cooldown = t.get('cooldown', 0)
            t.clear(); t.update(generation=generation, failures=0, threshold=cfg['failures'], status='starting',
                                escalation=escalation, cooldown=cooldown, next_check=0, grace_until=now+cfg['startup_grace'])
        t.update(name=target_name(c), service=c['Config']['Labels'].get('com.docker.compose.service',''),
                 method=check_method(c,cfg), mode=cfg['mode'])
        if manual_mode:
            if key in {r[0] for r in matching}: eligible.append((key,c,cfg,directory,files,scheme,path,host,endpoints))
            continue
        if now < max(t.get('cooldown',0),t.get('next_check',0)): continue
        if now < t.get('grace_until',0) and c['State']['Running']:
            t['status']='starting'; t['next_check']=t['grace_until']; dirty=True; continue
        dirty=True; probed.add(key)
        t['next_check'] = time.time() + cfg['interval']
        status, detail, docker_failed = probe(c,cfg,endpoints,scheme,path,host)
        threshold = (cfg['healthcheck_failures'] or cfg['failures']) if docker_failed else cfg['failures']
        changed = t.get('status') != status
        t.update(status=status, detail=detail, last_check=time.time(), threshold=threshold)
        if status == 'failed':
            t['failures'] = min(threshold,t.get('failures',0)+1)
            if not state.blocked: LOG.warning('%s/%s: FAIL %s/%s %s', project,key,t['failures'],threshold,detail)
        else:
            t['failures'] = 0
            if status == 'healthy':
                t['escalation'] = 0
                if cfg['log_success'] or changed: LOG.info('%s/%s: OK %s',project,key,detail)
            elif status == 'config-error' and changed: LOG.error('%s/%s: %s',project,key,detail)
    if not dirty and not manual_mode: return
    aggregate_targets(state)
    if not manual_mode:
        eligible = [r for r in rows if state.targets[r[0]].get('status')=='failed' and
                    state.targets[r[0]].get('failures',0)>=state.targets[r[0]].get('threshold',1) and
                    now>=state.targets[r[0]].get('cooldown',0)]
    if not eligible or (not manual_mode and not probed):
        if state.status=='healthy': state.blocked=False
        store.save(project,state); return
    # A threshold-ready target may promote to one project-wide action when
    # several targets are currently failed. Reconfirm older observations
    # without incrementing their failure counters or changing their cadence.
    threshold=policy['project_failure_threshold']
    failed = []
    if not manual_mode:
        failed = [r for r in rows if state.targets[r[0]].get('status')=='failed' and now>=state.targets[r[0]].get('cooldown',0)]
        to_confirm = failed if threshold and len(failed)>=threshold else eligible
        for r in to_confirm:
            key,c,cfg,_,_,scheme,path,host,endpoints=r
            if key in probed: continue
            t=state.targets[key]
            status,detail,docker_failed=probe(c,cfg,endpoints,scheme,path,host)
            t.update(status=status,detail=detail,last_check=time.time(),
                     threshold=(cfg['healthcheck_failures'] or cfg['failures']) if docker_failed else cfg['failures'])
            if status!='failed':
                t['failures']=0
                if status=='healthy': t['escalation']=0
        eligible=[r for r in eligible if state.targets[r[0]].get('status')=='failed' and state.targets[r[0]].get('failures',0)>=state.targets[r[0]].get('threshold',1)]
        failed=[r for r in failed if state.targets[r[0]].get('status')=='failed']
        aggregate_targets(state)
        if not eligible:
            store.save(project,state); return
    def selected_mode(r):
        cfg=r[2]; seq=cfg['recovery'].split(',') if cfg['recovery'] else [cfg['mode']]
        return seq[min(state.targets[r[0]].get('escalation',0),len(seq)-1)]
    chosen=eligible[0]
    if manual_mode: mode=manual_mode
    elif threshold and len(failed)>=threshold:
        mode=policy['project_recovery_mode']
        LOG.warning('%s: %s failed targets; promoting recovery to %s',project,len(failed),mode)
    else:
        chosen=next((r for r in eligible if selected_mode(r) in PROJECT_MODES),eligible[0])
        mode=selected_mode(chosen)
    key,c,cfg,directory,files,*_=chosen
    service=c['Config']['Labels'].get('com.docker.compose.service')
    if mode=='container-recreate' and sum(r[1]['Config']['Labels'].get('com.docker.compose.service')==service for r in rows)!=1:
        LOG.error('%s/%s: container-recreate is unsupported for scaled services',project,key)
        state.targets[key]['status']='config-error'; state.targets[key]['detail']='container-recreate requires one replica'; aggregate_targets(state); store.save(project,state); return
    # Keep shared project rate limiting for all automatic/manual operations.
    state.restarts=deque(t for t in state.restarts if now-t<policy['window'])
    if limited(project,state,store,policy): return
    cmds=commands(compose_command(project,directory,files),mode,c.get('Id'),service)
    if cfg['dry_run']:
        LOG.warning('%s: dry-run: %s',project,cmds); return
    if not fresh(project,containers):
        LOG.warning('%s: recovery skipped: project changed or removed',project); return
    try: hook(project,cfg,'pre_hook',mode)
    except (OSError,subprocess.SubprocessError):
        LOG.exception('%s: pre-hook failed; recovery skipped',project); return
    if not fresh(project,containers): return
    if mode=='container-recreate':
        try: validate_container_recreate(project,directory,files,service,c.get('Id'))
        except (OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
            LOG.error('%s/%s: recreate validation failed: %s',project,key,exc)
            state.targets[key].update(status='config-error',detail=str(exc)); aggregate_targets(state); store.save(project,state); return
        if not fresh(project,containers): return
    state.restarts.append(time.time())
    affected = list(state.targets) if mode in PROJECT_MODES else [key]
    for k in affected:
        if k in {r[0] for r in eligible}: state.targets[k]['escalation']=state.targets[k].get('escalation',0)+1
    record(state,mode,'started'); state.history[-1]['container']=target_name(c) if mode in CONTAINER_MODES else ''
    if mode=='down-up':
        state.recovery='up'; state.recovery_cmd=cmds[1]
        state.recovery_options={k:policy[k] for k in ('window','max_restarts','command_timeout','cooldown')}
    store.save(project,state)
    try:
        LOG.warning('%s: recovery mode=%s target=%s',project,mode,target_name(c) if mode in CONTAINER_MODES else 'project')
        if mode=='down-up':
            try: run_command(cmds[0],policy['command_timeout'])
            except (OSError,subprocess.SubprocessError): LOG.exception('%s: down failed; attempting up anyway',project)
            run_command(cmds[1],policy['command_timeout']); state.recovery=''; state.recovery_cmd=[]
        else: run_command(cmds[0],policy['command_timeout'] if mode in PROJECT_MODES else cfg['command_timeout'])
        record(state,mode,'completed'); LOG.info('%s: recovery completed',project); notify(project,cfg,'completed',mode,target_name(c) if mode in CONTAINER_MODES else None)
    except (OSError,subprocess.SubprocessError):
        record(state,mode,'failed'); LOG.exception('%s: recovery failed',project); notify(project,cfg,'failed',mode,target_name(c) if mode in CONTAINER_MODES else None)
    finally:
        state.history[-1]['container']=target_name(c) if mode in CONTAINER_MODES else ''
        try: hook(project,cfg,'post_hook',mode,state.history[-1]['result'])
        except (OSError,subprocess.SubprocessError): LOG.exception('%s: post-hook failed',project)
        end=time.time()
        for k in affected:
            state.targets[k].update(failures=0,status='unknown',cooldown=end+(policy['cooldown'] if mode in PROJECT_MODES else cfg['cooldown']),next_check=0)
        if mode in PROJECT_MODES: state.cooldown=end+policy['cooldown']
        else:
            # Other eligible targets are not held by this container's cooldown.
            for r in eligible:
                if r[0]!=key: state.targets[r[0]]['next_check']=0
        aggregate_targets(state)
        store.save(project,state)


def metrics_server(store, port):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/metrics':
                self.send_error(404); return
            lines = ['# TYPE docker_watchdog_failures gauge', '# TYPE docker_watchdog_retained_restart_attempts gauge', '# TYPE docker_watchdog_container_failures gauge']
            for project, state in store.all_states():
                label = json.dumps(project)
                lines.extend([f'docker_watchdog_failures{{project={label}}} {state.failures}',
                              f'docker_watchdog_retained_restart_attempts{{project={label}}} {len(state.restarts)}',
                              f'docker_watchdog_paused{{project={label}}} {int(store.paused(project))}',
                              f'docker_watchdog_last_check_timestamp_seconds{{project={label}}} {state.last_check}'])
                for key,t in state.targets.items():
                    labels=f'project={label},container={json.dumps(key)}'
                    lines.append(f'docker_watchdog_container_failures{{{labels}}} {t.get("failures",0)}')
            data = ('\n'.join(lines)+'\n').encode()
            self.send_response(200); self.send_header('Content-Type','text/plain; version=0.0.4'); self.end_headers(); self.wfile.write(data)
        def log_message(self, *args): pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def event_listener(wake):
    while True:
        try:
            with subprocess.Popen(['docker','events','--filter','type=container','--format','{{json .}}'], stdout=subprocess.PIPE, text=True) as proc:
                LOG.info('Docker Events subscriber started (pid=%s)', proc.pid)
                first = True
                for line in proc.stdout:
                    if first:
                        LOG.info('Docker Events stream confirmed by first event')
                        first = False
                    wake.set()
                LOG.warning('Docker Events stream closed; polling continues, reconnecting')
        except OSError:
            LOG.warning('Docker events unavailable; polling continues')
        time.sleep(5)


def duration(value):
    m = re.fullmatch(r'([1-9][0-9]*)([smhd]?)', value)
    if not m: raise ValueError('duration must be e.g. 30m, 2h')
    return int(m[1])*{'':1,'s':1,'m':60,'h':3600,'d':86400}[m[2]]


def date_text(value):
    return datetime.datetime.fromtimestamp(value).astimezone().isoformat(timespec='seconds') if value else '-'


def display_status(state, paused, discovered, now):
    if paused: return 'paused'
    if state.recovery: return 'pending-up'
    if not discovered: return 'removed'
    if now < state.cooldown: return 'cooldown'
    if now < state.grace_until: return 'starting'
    if state.blocked and state.status == 'failed': return 'blocked'
    return state.status


def describe(project, containers, config, store, now=None):
    now = time.time() if now is None else now
    state = store.load(project)
    paused = store.paused(project)
    row = dict(project=project, discovered=bool(containers), paused=paused,
               status=display_status(state, paused, bool(containers), now),
               failures=state.failures, last_check=date_text(state.last_check),
               last_detail=state.last_detail, next_check=date_text(max(state.next_check,state.cooldown)),
               grace_until=date_text(state.grace_until), cooldown_until=date_text(state.cooldown),
               recovery=state.recovery)
    cfg = None
    if containers:
        try:
            targets, cfg = prepare_targets(containers,config)
            row['containers'] = []
            for key,c,tcfg,directory,files,scheme,path,host,endpoints in targets:
                t = state.targets.get(key,{})
                status=t.get('status','unknown')
                if t.get('generation') != c.get('Id','')+':'+c['State'].get('StartedAt',''): status='starting'
                if now<t.get('cooldown',0) or now<state.cooldown: status='cooldown'
                if tcfg.get('_error'): status='config-error'
                if paused: status='paused'
                http_match=re.search(r'HTTP (\d+)',t.get('detail',''))
                latency_match=re.search(r'duration=([0-9.]+)s',t.get('detail',''))
                row['containers'].append(dict(key=key,name=target_name(c),service=c['Config']['Labels'].get('com.docker.compose.service'),
                    check=check_method(c,tcfg),mode=tcfg['mode'],endpoints=sorted(endpoints),status=status,
                    healthcheck_status=(c['State'].get('Health') or {}).get('Status'),
                    http=int(http_match[1]) if http_match else None,
                    latency_ms=round(float(latency_match[1])*1000,1) if latency_match else None,
                    failures=t.get('failures',0),threshold=t.get('threshold',(tcfg['healthcheck_failures'] or tcfg['failures']) if check_method(c,tcfg)=='healthcheck' else tcfg['failures']),
                    detail=tcfg.get('_error',t.get('detail','')),last_check=date_text(t.get('last_check',0)),next_check=date_text(t.get('next_check',0)),
                    effective_options={k:tcfg[k] for k in ('check','mode','recovery','interval','failures','healthcheck_failures','cooldown','startup_grace','max_restarts','window','http_timeout','command_timeout','project_failure_threshold','project_recovery_mode')}))
            _,_,tcfg,directory,files,scheme,path,host,endpoints=targets[0]
            row.update(directory=directory, files=files, scheme=scheme, path=path, host=host,
                       endpoints=sorted({e for r in targets for e in r[-1]}),mode=tcfg['mode'] if len(targets)==1 else 'multiple')
            if len(targets)==1: cfg={**tcfg,**{k:cfg[k] for k in ('max_restarts','window','project_failure_threshold','project_recovery_mode')}}
        except (ValueError, KeyError) as exc:
            row.update(error=str(exc), status='config-error' if not paused else 'paused')
    elif state.recovery:
        cfg = {**config, **state.recovery_options}
    if cfg is not None:
        keys = ('mode','recovery','interval','failures','cooldown','max_restarts','window','http_timeout','command_timeout','startup_grace','dry_run','check','healthcheck_failures','project_failure_threshold','project_recovery_mode')
        row['effective_options'] = {k: cfg[k] for k in keys}
        attempts = sorted(t for t in state.restarts if now-t < cfg['window'])
        limit = cfg['max_restarts']
        row.update(attempts_in_window=len(attempts), next_recovery_allowed=date_text(max(state.cooldown, attempts[len(attempts)-limit]+cfg['window'] if limit and len(attempts)>=limit else 0)))
    match = re.search(r'HTTP (\d+)', state.last_detail)
    latency = re.search(r'duration=([0-9.]+)s', state.last_detail)
    row['http'] = int(match[1]) if match else None
    row['latency_ms'] = round(float(latency[1])*1000, 1) if latency else None
    return row


def print_table(headers, rows):
    rows = [[str(v) if v is not None else '-' for v in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i,h in enumerate(headers)]
    for r in [headers, ['-'*n for n in widths], *rows]:
        print('  '.join(v.ljust(n) for v,n in zip(r,widths)).rstrip())


def print_status(rows):
    print(f'Docker Watchdog v{VERSION}')
    print_table(['PROJECT','STATUS','PORT','HTTP','LATENCY MS','FAILURES','RESTARTS','MODE'], [
        [r['project'],r['status'].upper(),','.join(map(str,sorted({p for _,p in r.get('endpoints',[])}))) or '-',r['http'],r['latency_ms'],
         f"{r['containers'][0]['failures']}/{r['containers'][0]['threshold']}" if len(r.get('containers',[]))==1 else r['failures'],
         f"{r['attempts_in_window']}/{r['effective_options']['max_restarts'] or 'unlimited'}" if 'effective_options' in r else '-',r.get('mode','-')] for r in rows])
    for r in rows:
        print(f"  {r['project']}: last check={r['last_check']}; next check={r['next_check']}; recovery allowed={r.get('next_recovery_allowed','-')}")
        if r.get('error'): print('    ERROR: '+r['error'])
        for t in r.get('containers',[]):
            print(f"    {t['name']}: {t['status'].upper()} check={t['check']} mode={t['mode']} failures={t['failures']}/{t['threshold']} {t['detail']}")
    counts = defaultdict(int)
    for r in rows: counts[r['status']] += 1
    print(f'Projects: {len(rows)}' + ''.join(f' | {k}: {v}' for k,v in sorted(counts.items())))


def main():
    parser = argparse.ArgumentParser(description='Docker Compose watchdog')
    parser.add_argument('--config', default='/etc/docker-watchdog/watchdog.conf')
    parser.add_argument('--state-dir', default='/var/lib/docker-watchdog')
    parser.add_argument('--json', action='store_true', help='machine-readable output for list, status and history')
    parser.add_argument('--table', action='store_true', help='human-readable list instead of legacy JSON lines')
    parser.add_argument('--raw', action='store_true', help='legacy raw state for status or history')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--version', action='version', version=VERSION)
    actions = parser.add_mutually_exclusive_group()
    for action in ('pause','resume','history','reset-limit','recover'):
        actions.add_argument('--'+action, metavar='PROJECT')
    actions.add_argument('--status', action='store_true')
    actions.add_argument('--list', action='store_true')
    parser.add_argument('--duration')
    parser.add_argument('--mode', choices=sorted(MODES))
    parser.add_argument('--container', help='container name, full ID, unique service or service:number for --recover')
    args = parser.parse_args()
    if args.container and not args.recover: parser.error('--container requires --recover')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    config = load_config(args.config)
    config['dry_run'] = config['dry_run'] or args.dry_run
    logging.getLogger().setLevel(config['log_level'])
    admin = Store(args.state_dir) if any((args.pause,args.resume,args.history,args.reset_limit,args.recover,args.status)) else None
    if admin:
        project = args.pause or args.resume or args.history or args.reset_limit or args.recover
        if args.status:
            active = discover()
            names = set(active) | {name for name, _ in admin.all_states()}
            if args.raw:
                for name in sorted(names):
                    state = admin.load(name)
                    print(json.dumps(dict(vars(state), project=name, discovered=name in active, paused=admin.paused(name), restarts=list(state.restarts))))
            else:
                rows = [describe(name, active.get(name, []), config, admin) for name in sorted(names)]
                if args.json:
                    for row in rows: print(json.dumps(row))
                else: print_status(rows)
            return
        if args.history:
            state = admin.load(project)
            if args.raw:
                print(json.dumps(dict(history=state.history, restart_timestamps=list(state.restarts)), indent=2))
            elif args.json:
                print(json.dumps(dict(history=[dict(r, date=date_text(r['time'])) for r in state.history], restart_timestamps=[date_text(t) for t in state.restarts]), indent=2))
            else:
                print(f'Recovery history: {project}')
                print_table(['DATE','MODE','RESULT','CONTAINER'], [[date_text(r['time']),r['mode'],r['result'],r.get('container') or '-'] for r in state.history])
                print('Retained attempt timestamps (including legacy attempts):')
                for t in state.restarts: print('  '+date_text(t))
            return
        if config['dry_run']:
            parser.error('administrative mutation disabled in dry-run')
        with admin.guard(project):
            state = admin.load(project)
            if args.pause:
                until = time.time()+duration(args.duration) if args.duration else 0
                tmp = admin.pause_path(project).with_suffix('.pause.tmp')
                tmp.write_text(str(until)); os.replace(tmp, admin.pause_path(project))
            elif args.resume:
                if admin.pause_path(project).exists(): admin.pause_path(project).unlink()
                state.failures = 0; state.next_check = 0
                for t in state.targets.values(): t.update(failures=0,next_check=0,status='unknown')
                admin.save(project,state)
            elif args.reset_limit:
                state.restarts.clear(); state.blocked=False
                if state.recovery: state.recovery_options.update(max_restarts=config['max_restarts'], window=config['window'])
                admin.save(project,state)
            elif args.recover:
                containers = discover().get(project, [])
                if not containers and not state.recovery: parser.error('project not discovered')
                if admin.paused(project): parser.error('project paused')
                try:
                    if args.mode: mode=args.mode
                    elif containers:
                        candidates=[c for c in containers if not args.container or args.container in (target_key(c),target_name(c),c.get('Id'),c['Config']['Labels'].get('com.docker.compose.service'))]
                        if len(candidates)!=1: parser.error('specify --mode or a unique --container')
                        mode=project_options(candidates,config)['mode']
                    else: mode=config['mode']
                    check_locked(project,containers,admin,config,mode,args.container)
                except ValueError as exc: parser.error(str(exc))
        return
    if args.list:
        rows = [describe(name, containers, config, Store(args.state_dir, readonly=True)) for name, containers in sorted(discover().items())]
        if args.table:
            print_status(rows)
        else:
            for row in rows:
                keys = ('project','directory','files','scheme','path','host','endpoints','mode','error','containers')
                print(json.dumps(row if args.json else {k: row[k] for k in keys if k in row}))
        return
    LOG.info('Docker Watchdog v%s starting (dry_run=%s, docker_events=%s)', VERSION, config['dry_run'], config['docker_events'])
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
    known = set()
    wake = threading.Event()
    last_summary = 0
    if config['metrics_port'] and not args.once: metrics_server(store, config['metrics_port'])
    if config['docker_events'] and not args.once: threading.Thread(target=event_listener,args=(wake,),daemon=True).start()
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
                discovered = set(projects)
                for project in sorted(discovered-known): LOG.info('%s: discovered, monitoring started', project)
                for project in sorted(known-discovered): LOG.info('%s: removed from discovery', project)
                known = discovered
                if config['summary_interval'] and time.monotonic()-last_summary >= config['summary_interval']:
                    statuses = defaultdict(int)
                    for name in known:
                        statuses[display_status(store.load(name), store.paused(name), True, time.time())] += 1
                    LOG.info('summary: projects=%s %s workers_active=%s', len(known), ' '.join(f'{k}={statuses[k]}' for k in ('healthy','failed','starting','unknown','paused','blocked','cooldown','pending-up','config-error')), len(pending))
                    last_summary=time.monotonic()
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
            wake.wait(max(0, min(1, config['interval']) - (time.monotonic() - started)))
            wake.clear()


if __name__ == '__main__':
    main()
