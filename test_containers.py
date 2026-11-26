"""Tests for v1.3 target checks, coordination and recovery policy."""
import copy
import concurrent.futures
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import watchdog as w


class ContainerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = temp.name
        Path(temp.name, 'compose.yml').touch()
        self.store = w.Store(temp.name)
        self.cfg = w.options(dict(interval=1, cooldown=0, startup_grace=0,
                                  max_restarts=0, mode='container-restart'))
        for name in ('fresh', 'validate_container_recreate'):
            mock = patch('watchdog.'+name, return_value=True)
            mock.start()
            self.addCleanup(mock.stop)
        self.a = self.container('web')
        self.b = self.container('worker', check='healthcheck', health='healthy')

    def container(self, name, check='http', health=None, running=True, number='1', **labels):
        result = {'Id':name+number+'-id', 'Name':'/p-'+name+'-'+number,
                  'Config':{'Labels':{'com.docker.compose.project':'p',
                                      'com.docker.compose.project.working_dir':self.directory,
                                      'com.docker.compose.project.config_files':'compose.yml',
                                      'com.docker.compose.service':name,
                                      'com.docker.compose.container-number':number,
                                      'watchdog.enabled':'true','watchdog.check':check,
                                      **{'watchdog.'+k:str(v) for k,v in labels.items()}}},
                  'State':{'Running':running,'StartedAt':'initial'},
                  'HostConfig':{'PortBindings':{}},'NetworkSettings':{'Ports':{}}}
        if check in ('http','both','auto'):
            result['HostConfig']['PortBindings']={'80/tcp':[{'HostIp':'0.0.0.0','HostPort':'8080'}]}
            result['NetworkSettings']['Ports']=copy.deepcopy(result['HostConfig']['PortBindings'])
        if health is not None:
            result['Config']['Healthcheck']={'Test':['CMD','true']}
            result['State']['Health']={'Status':health}
        return result

    def due(self, *keys):
        s=self.store.load('p')
        for key,t in s.targets.items():
            if not keys or key in keys: t['next_check']=0
        self.store.save('p',s)

    def tick(self, containers=None, due=True):
        if due: self.due()
        w.check('p',containers if containers is not None else [self.a,self.b],self.store,self.cfg)
        return self.store.load('p')

    def test_command_modes(self):
        self.assertEqual(w.commands(['compose'],'container-restart','abc','web'),[['docker','restart','abc']])
        self.assertEqual(w.commands(['compose'],'container-recreate','abc','web'),[['compose','up','-d','--no-deps','--force-recreate','--','web']])
        with self.assertRaises(ValueError): w.commands(['compose'],'container-restart')

    def test_healthcheck_without_port(self):
        with patch('watchdog.healthy') as http:
            s=self.tick([self.b]); http.assert_not_called()
        self.assertEqual(s.targets['worker:1']['status'],'healthy')
        self.assertEqual(w.describe('p',[self.b],self.cfg,self.store)['endpoints'],[])

    def test_healthcheck_ignores_multiple_ports(self):
        self.b['HostConfig']['PortBindings']={'80/tcp':[{'HostPort':'8080'}],'81/tcp':[{'HostPort':'8081'}]}
        self.b['NetworkSettings']['Ports']=copy.deepcopy(self.b['HostConfig']['PortBindings'])
        s=self.tick([self.b]); self.assertEqual(s.status,'healthy')

    def test_default_http_ignores_docker_health(self):
        c=self.container('web',health='unhealthy')
        c['Config']['Labels'].pop('watchdog.check')
        with patch('watchdog.healthy',return_value=True) as http:
            self.assertEqual(self.tick([c]).status,'healthy'); http.assert_called_once()

    def test_auto_fallback_and_preference(self):
        hc=self.container('web',check='auto',health='healthy')
        fallback=self.container('other',check='auto')
        with patch('watchdog.healthy',return_value=True) as http:
            s=self.tick([hc,fallback]); http.assert_called_once()
        self.assertEqual(s.targets['web:1']['method'],'healthcheck')
        self.assertEqual(s.targets['other:1']['method'],'http')

    def test_both_requires_healthy_and_http(self):
        c=self.container('web',check='both',health='healthy')
        with patch('watchdog.healthy',return_value=False):
            self.assertEqual(self.tick([c]).status,'failed')
        c['State']['Health']['Status']='unhealthy'
        with patch('watchdog.healthy') as http:
            self.assertEqual(self.tick([c]).targets['web:1']['failures'],2); http.assert_not_called()

    def test_starting_does_not_count_and_resets_streak(self):
        self.b['State']['Health']['Status']='unhealthy'
        self.tick([self.b])
        self.b['State']['Health']['Status']='starting'
        with patch('watchdog.run_command') as cmd:
            s=self.tick([self.b]); cmd.assert_not_called()
        self.assertEqual(s.targets['worker:1']['failures'],0)
        self.assertEqual(s.status,'starting')

    def test_missing_healthcheck_is_config_error(self):
        c=self.container('web',check='healthcheck')
        with patch('watchdog.run_command') as cmd:
            s=self.tick([c]); cmd.assert_not_called()
        self.assertEqual(s.status,'config-error')

    def test_healthcheck_threshold_default_and_override(self):
        self.b['State']['Health']['Status']='unhealthy'
        with patch('watchdog.run_command') as cmd:
            self.tick([self.b]); self.tick([self.b]); cmd.assert_not_called()
            self.tick([self.b]); cmd.assert_called_once_with(['docker','restart','worker1-id'],180)
        self.b['Config']['Labels']['watchdog.healthcheck_failures']='1'
        with patch('watchdog.run_command') as cmd:
            self.tick([self.b]); cmd.assert_called_once()

    def test_independent_failure_counters_and_reset(self):
        with patch('watchdog.healthy',return_value=False):
            s=self.tick(); self.assertEqual(s.targets['web:1']['failures'],1)
            self.assertEqual(s.targets['worker:1']['failures'],0)
            self.b['State']['Health']['Status']='unhealthy'
            s=self.tick(); self.assertEqual(s.targets['web:1']['failures'],2)
            self.assertEqual(s.targets['worker:1']['failures'],1)
        with patch('watchdog.healthy',return_value=True):
            s=self.tick(); self.assertEqual(s.targets['web:1']['failures'],0)
            self.assertEqual(s.targets['worker:1']['failures'],2)

    def test_promotion_on_two_confirmed_failed_one_ready(self):
        self.cfg['failures']=3
        self.b['State']['Health']['Status']='unhealthy'
        with patch('watchdog.healthy',return_value=False),patch('watchdog.run_command') as cmd:
            self.b['Config']['Labels']['watchdog.failures']='9'
            self.tick();self.tick();s=self.tick()
        cmd.assert_called_once()
        self.assertEqual(cmd.call_args.args[0][-1],'restart')
        self.assertEqual(len(s.restarts),1)
        self.assertEqual([t['failures'] for t in s.targets.values()],[0,0])
        self.assertEqual(s.history[-1]['container'],'')

    def test_promotion_override_one_label(self):
        self.cfg['failures']=1
        self.a['Config']['Labels']['watchdog.project_recovery_mode']='force-recreate'
        self.b['State']['Health']['Status']='unhealthy'
        with patch('watchdog.healthy',return_value=False),patch('watchdog.run_command') as cmd:
            self.tick()
        cmd.assert_called_once()
        self.assertEqual(cmd.call_args.args[0][-1],'--force-recreate')

    def test_promotion_disabled_and_individual_cooldown(self):
        self.cfg.update(failures=1,project_failure_threshold=0,cooldown=120)
        self.b['State']['Health']['Status']='unhealthy'
        with patch('watchdog.healthy',return_value=False),patch('watchdog.run_command') as cmd:
            s=self.tick(); self.assertEqual(cmd.call_count,1)
            self.assertEqual(s.cooldown,0)
            self.assertGreater(s.targets['web:1']['cooldown'],time.time())
            self.a['State']['StartedAt']='new'
            s=self.tick(); self.assertEqual(cmd.call_count,2)
        self.assertEqual([c.args[0][-1] for c in cmd.call_args_list],['web1-id','worker1-id'])
        self.assertGreater(s.targets['web:1']['cooldown'],time.time())

    def test_stale_failure_reconfirmed_before_promotion(self):
        self.cfg['failures']=2
        self.b['State']['Health']['Status']='unhealthy'
        with patch('watchdog.healthy',return_value=False),patch('watchdog.run_command') as cmd:
            self.tick(); self.b['State']['Health']['Status']='healthy'
            self.due('web:1'); self.tick(due=False)
        cmd.assert_called_once_with(['docker','restart','web1-id'],180)

    def test_shared_budget(self):
        self.cfg.update(failures=1,project_failure_threshold=0,max_restarts=1)
        self.b['State']['Health']['Status']='unhealthy'
        with patch('watchdog.healthy',return_value=False),patch('watchdog.run_command') as cmd:
            self.tick();s=self.tick();cmd.assert_called_once()
        self.assertTrue(s.blocked)

    def test_mixed_rate_policy(self):
        self.a['Config']['Labels'].update({'watchdog.max_restarts':'0','watchdog.window':'100'})
        self.b['Config']['Labels'].update({'watchdog.max_restarts':'2','watchdog.window':'200'})
        _,policy=w.prepare_targets([self.a,self.b],self.cfg)
        self.assertEqual((policy['max_restarts'],policy['window']),(2,200))

    def test_manual_selection_and_history(self):
        with patch('watchdog.run_command') as cmd:
            with self.assertRaises(ValueError): w.check('p',[self.a,self.b],self.store,self.cfg,'container-restart')
            w.check('p',[self.a,self.b],self.store,self.cfg,'container-restart','worker')
            cmd.assert_called_once_with(['docker','restart','worker1-id'],180)
        self.assertEqual(self.store.load('p').history[-1]['container'],'p-worker-1')

    def test_container_recreate_requires_single_replica(self):
        replica=self.container('web',number='2')
        with patch('watchdog.run_command') as cmd:
            w.check('p',[self.a,replica],self.store,self.cfg,'container-recreate','web:1')
            cmd.assert_not_called()
        self.assertEqual(self.store.load('p').targets['web:1']['status'],'config-error')

    def test_container_recreate_command(self):
        with patch('watchdog.run_command') as cmd:
            w.check('p',[self.a,self.b],self.store,self.cfg,'container-recreate','worker')
        self.assertEqual(cmd.call_args.args[0][-6:],['up','-d','--no-deps','--force-recreate','--','worker'])

    def test_invalid_target_does_not_block_other_target(self):
        self.a['Config']['Labels']['watchdog.check']='invalid'
        self.cfg['failures']=1
        self.b['State']['Health']['Status']='unhealthy'
        with patch('watchdog.run_command') as cmd:
            s=self.tick(); cmd.assert_called_once_with(['docker','restart','worker1-id'],180)
        self.assertEqual(s.targets['web:1']['status'],'config-error')

    def test_conflicting_project_policy_is_rejected(self):
        self.a['Config']['Labels']['watchdog.project_failure_threshold']='1'
        self.b['Config']['Labels']['watchdog.project_failure_threshold']='2'
        with patch('watchdog.run_command') as cmd:
            s=self.tick(); cmd.assert_not_called()
        self.assertEqual(s.status,'config-error')

    def test_legacy_state_preserves_attempts_and_history(self):
        s=w.State(restarts=w.deque([time.time()]),history=[dict(time=time.time(),mode='restart',result='completed')])
        raw=dict(vars(s),project='p',restarts=list(s.restarts));raw.pop('targets')
        self.store.path('p').write_text(json.dumps(raw))
        with patch('watchdog.healthy',return_value=True): s=self.tick()
        self.assertEqual(len(s.restarts),1); self.assertEqual(len(s.history),1)
        self.assertEqual(len(s.targets),2)

    def test_pending_up_uses_saved_state(self):
        self.store.save('p',w.State(recovery='up',recovery_cmd=['docker','compose','up','-d'],recovery_options={'max_restarts':0}))
        with patch('watchdog.run_command') as cmd:
            w.check('p',[],self.store,self.cfg);cmd.assert_called_once_with(['docker','compose','up','-d'],180)
        self.assertFalse(self.store.load('p').recovery)

    def test_parallel_checks_do_not_duplicate_project_recovery(self):
        self.cfg.update(failures=1,cooldown=120)
        self.b['State']['Health']['Status']='unhealthy'
        with patch('watchdog.healthy',return_value=False),patch('watchdog.run_command') as cmd:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                jobs=[pool.submit(w.check,'p',[self.a,self.b],self.store,self.cfg) for _ in range(2)]
                for job in jobs: job.result()
            cmd.assert_called_once()
            self.assertEqual(cmd.call_args.args[0][-1],'restart')
        self.assertEqual(len(self.store.load('p').restarts),1)

    def test_pause_applies_to_all_targets(self):
        self.store.pause_path('p').write_text('0')
        with patch('watchdog.healthy') as http,patch('watchdog.run_command') as cmd:
            self.tick(); http.assert_not_called();cmd.assert_not_called()


class RecreateValidationTests(unittest.TestCase):
    @patch('watchdog.docker',return_value='abc\ndef\n')
    def test_live_replica_count(self,docker):
        with self.assertRaises(ValueError): w.validate_container_recreate('p','/','f','web','abc')

    @patch('watchdog.subprocess.check_output',return_value='{"services":{"web":{"deploy":{"replicas":2}}}}')
    @patch('watchdog.docker',return_value='abc\n')
    def test_configured_replica_count(self,docker,compose):
        with self.assertRaises(ValueError): w.validate_container_recreate('p','/',['f'],'web','abc')

    @patch('watchdog.subprocess.check_output',return_value='{"services":{"web":{}}}')
    @patch('watchdog.docker',return_value='abc\n')
    def test_single_replica(self,docker,compose):
        w.validate_container_recreate('p','/',['f'],'web','abc')
