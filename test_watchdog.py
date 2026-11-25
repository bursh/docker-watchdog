import unittest
import tempfile
from unittest.mock import patch
import subprocess
from pathlib import Path
import watchdog as w

class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        Path(self.dir, 'compose.yml').touch()
        self.store = w.Store(self.dir)
        self.cfg = w.options({'interval': 1, 'cooldown': 0})
        self.c = {'Config': {'Labels': {'com.docker.compose.project.working_dir': self.dir,
            'com.docker.compose.project.config_files': 'compose.yml', 'com.docker.compose.service': 'web'}},
            'State': {'Running': True}, 'HostConfig': {'PortBindings': {'80/tcp': [{'HostPort': '0'}]}},
            'NetworkSettings': {'Ports': {'80/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '12345'}]}}}
    def tick(self):
        state = self.store.load('p'); state.next_check = 0; self.store.save('p', state)
        w.check('p', [self.c], self.store, self.cfg)
    def test_port(self):
        self.assertEqual(w.target([self.c])[-1], {('127.0.0.1',12345)})
    def test_modes(self):
        self.assertEqual(w.commands(['docker'], 'restart'), [['docker','restart']])
        self.assertEqual(w.commands(['docker'], 'down-up'), [['docker','down'],['docker','up','-d']])
        self.assertEqual(w.commands(['docker'], 'force-recreate'), [['docker','up','-d','--force-recreate']])
    def test_labels(self):
        self.c['Config']['Labels']['watchdog.mode']='force-recreate'
        self.assertEqual(w.project_options([self.c], self.cfg)['mode'],'force-recreate')
    def test_invalid(self):
        with self.assertRaises(ValueError): w.options({'mode':'invalid'})
    @patch('watchdog.run_command')
    @patch('watchdog.healthy', return_value=False)
    def test_three_failures(self, health, cmd):
        self.tick(); self.tick(); cmd.assert_not_called(); self.tick(); cmd.assert_called_once()
        self.assertEqual(len(self.store.load('p').restarts),1)
    @patch('watchdog.healthy', return_value=True)
    def test_reset(self, health):
        self.store.save('p', w.State(failures=2)); self.tick()
        self.assertEqual(self.store.load('p').failures,0)
    @patch('watchdog.run_command')
    @patch('watchdog.healthy', return_value=False)
    def test_dry_run(self, health, cmd):
        self.cfg['dry_run']=True; self.cfg['failures']=1; self.tick(); cmd.assert_not_called()
    @patch('watchdog.run_command')
    @patch('watchdog.healthy', return_value=False)
    def test_limit(self, health, cmd):
        self.cfg.update(failures=1,max_restarts=1); self.tick(); self.tick(); cmd.assert_called_once()
    @patch('watchdog.run_command')
    @patch('watchdog.healthy', return_value=False)
    def test_down_up_recovery(self, health, cmd):
        self.cfg.update(mode='down-up',failures=1)
        cmd.side_effect=[None, subprocess.CalledProcessError(1,'up')]
        self.tick(); self.assertEqual(list(self.store.recovering()),['p'])
        cmd.side_effect=None
        w.check('p', [], w.Store(self.dir), self.cfg)
        self.assertEqual(self.store.load('p').recovery,'')
        self.assertEqual(cmd.call_count,3)
    @patch('watchdog.run_command')
    @patch('watchdog.healthy', return_value=False)
    def test_down_failure(self, health, cmd):
        self.cfg.update(mode='down-up',failures=1)
        cmd.side_effect=[subprocess.CalledProcessError(1,'down'),None]
        self.tick(); self.assertEqual(cmd.call_count,2); self.assertEqual(self.store.load('p').recovery,'')
