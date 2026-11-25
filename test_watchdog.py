import unittest
from unittest.mock import patch
import watchdog as w


def container(running=True):
    return {'Config': {'Labels': {'com.docker.compose.project.working_dir': '/opt/app', 'com.docker.compose.project.config_files': '/opt/app/compose.yml', 'com.docker.compose.service': 'web'}}, 'State': {'Running': running}, 'HostConfig': {'PortBindings': {'80/tcp': [{'HostIp': '', 'HostPort': '8080'}]}}, 'NetworkSettings': {'Ports': {'80/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '8080'}]}}}


class Tests(unittest.TestCase):
    def test_port(self):
        self.assertEqual(w.target([container()])[-1], {('127.0.0.1', 8080)})

    @patch('watchdog.subprocess.run')
    @patch('watchdog.healthy', return_value=False)
    def test_three_failures(self, health, run):
        state = w.State()
        for _ in range(2):
            w.check('app', [container()], state)
        run.assert_not_called()
        w.check('app', [container()], state)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][-1], 'restart')
        w.check('app', [container()], state)
        self.assertEqual(run.call_count, 1)

    @patch('watchdog.healthy', return_value=True)
    def test_reset(self, health):
        state = w.State(failures=2)
        w.check('app', [container()], state)
        self.assertEqual(state.failures, 0)

    @patch('watchdog.subprocess.run')
    @patch('watchdog.healthy', return_value=False)
    def test_limit(self, health, run):
        state = w.State(failures=2)
        state.restarts.extend([w.time.monotonic()] * 3)
        w.check('app', [container()], state)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
