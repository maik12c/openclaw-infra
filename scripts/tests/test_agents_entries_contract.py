#!/usr/bin/env python3
"""Run the agents role's heartbeat and sandbox tasks through Ansible against a
fake OpenClaw 2026.9.7 config CLI.

Since 2026.9 agents are keyed by id under agents.entries; agents.list is an
unknown config path, so the 2026.6 index-based writes would fail on a live
server. 2026.9 also reports an unset path as a JSON error ('valid but unset',
exit 1) rather than 'Config path not found'.
"""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MAIN_TASKS = ROOT / 'ansible/roles/agents/tasks/main.yml'
HEARTBEAT_TASKS = ROOT / 'ansible/roles/agents/tasks/heartbeat.yml'
DEFAULTS = 'Set safe global heartbeat defaults'
SANDBOX = 'Set non-default agent sandbox mode override'
FAKE_OPENCLAW = '''#!/usr/bin/env python3
# A stateful stand-in for the OpenClaw 2026.9.7 config CLI.
import json, os, sys
args = sys.argv[1:]
root = os.environ['FIXTURE_ROOT']
store_path = os.path.join(root, 'store.json')
store = json.load(open(store_path))
def record():
    with open(os.path.join(root, 'writes'), 'a') as f:
        f.write(json.dumps(args) + '\\n')
def fail(message):
    if '--json' in args:
        print(json.dumps({'ok': False, 'error': {'type': 'cli_error', 'message': message}}, indent=2))
    else:
        print(message)
    sys.exit(1)
def walk(key, create=False):
    if key == 'agents.list' or key.startswith('agents.list.'):
        fail('Unknown config path: agents.list. Run openclaw config schema to inspect valid paths.')
    node, parts = store, key.split('.')
    for part in parts[:-1]:
        if part not in node:
            if not create: return None, parts[-1]
            node[part] = {}
        node = node[part]
    return node, parts[-1]
if os.path.exists(os.path.join(root, 'broken')):
    fail('config file is unreadable')
if args[:2] == ['config', 'get']:
    node, leaf = walk(args[2])
    if node is None or leaf not in node:
        fail('Config path is valid but unset: ' + args[2] + '. The runtime default applies.')
    value = node[leaf]
    print(json.dumps(value) if '--json' in args or not isinstance(value, str) else value)
    sys.exit(0)
if args[:2] == ['config', 'set']:
    node, leaf = walk(args[2], create=True)
    node[leaf] = json.loads(args[args.index('--json') + 1]) if '--json' in args else args[3]
    record(); json.dump(store, open(store_path, 'w')); sys.exit(0)
if args[:2] == ['config', 'unset']:
    node, leaf = walk(args[2])
    if node is None or leaf not in node:
        fail('Config path is valid but unset: ' + args[2])
    del node[leaf]; record(); json.dump(store, open(store_path, 'w')); sys.exit(0)
sys.exit('unexpected openclaw call: ' + ' '.join(args))
'''


def find_task(tasks, name):
    for task in tasks:
        if task.get('name') == name:
            return task
        found = find_task(task.get('block', []), name)
        if found:
            return found
    return None


def strip_notify(tasks):
    for task in tasks:
        task.pop('notify', None)
        for key in ('block', 'rescue', 'always'):
            strip_notify(task.get(key, []))
    return tasks


class AgentsEntriesContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ansible = shutil.which('ansible-playbook')
        if not cls.ansible:
            raise RuntimeError("Install the project's Ansible development dependency first")
        python = shlex.split(Path(cls.ansible).read_text().splitlines()[0].removeprefix('#!'))
        code = 'import json,sys,yaml; print(json.dumps([yaml.safe_load(open(p)) for p in sys.argv[1:]]))'
        main, cls.heartbeat, cls.defaults = json.loads(subprocess.run(
            [*python, '-c', code, str(MAIN_TASKS), str(HEARTBEAT_TASKS), str(ROOT / 'ansible/group_vars/all.yml')],
            capture_output=True, text=True, check=True).stdout)
        cls.set_defaults = find_task(main, DEFAULTS)
        cls.sandbox = find_task(main, SANDBOX)
        assert cls.set_defaults and cls.sandbox, 'Agents role tasks moved'

    def run_tasks(self, root, store, agents, automation, tasks):
        (root / 'bin').mkdir(exist_ok=True)
        fake = root / 'bin/openclaw'
        fake.write_text(FAKE_OPENCLAW)
        fake.chmod(0o700)
        if not (root / 'store.json').exists():
            (root / 'store.json').write_text(json.dumps(store))
        current = root / 'agents_current.json'
        current.write_text(json.dumps([{'id': agent['id']} for agent in agents]))
        patch = lambda value: json.loads(json.dumps(value).replace('/tmp/ansible_agents_current.json', str(current)))
        (root / 'heartbeat.json').write_text(json.dumps(strip_notify(patch(self.heartbeat))))
        play_tasks = strip_notify(patch(tasks)) + [{
            'name': 'Reconcile per-agent heartbeat targeting',
            'ansible.builtin.include_tasks': str(root / 'heartbeat.json'),
            'loop': '{{ openclaw_agents }}',
            'loop_control': {'loop_var': '_heartbeat_agent'},
        }]
        variables = {**self.defaults, 'openclaw_agents': agents,
                     '_openclaw_scheduled_automation_by_agent': automation}
        play = [{'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
                 'vars': variables, 'tasks': play_tasks}]
        (root / 'play.json').write_text(json.dumps(play))
        env = {'PATH': f"{root / 'bin'}:{os.environ['PATH']}", 'HOME': str(root),
               'FIXTURE_ROOT': str(root), 'ANSIBLE_NOCOLOR': '1',
               'ANSIBLE_LOCAL_TEMP': str(root / 'local'), 'ANSIBLE_REMOTE_TEMP': str(root / 'remote')}
        return subprocess.run([self.ansible, '-i', 'localhost,', str(root / 'play.json')],
                              env=env, capture_output=True, text=True, timeout=120)

    def writes(self, root):
        path = root / 'writes'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def store(self, root):
        return json.loads((root / 'store.json').read_text())

    MAIN = {'id': 'main', 'is_default': True, 'deliver_channel': 'telegram', 'deliver_to': '123', 'deliver_type': 'dm'}

    def test_enabled_agent_gets_an_entries_heartbeat_and_a_second_pass_converges(self):
        with tempfile.TemporaryDirectory(prefix='agents-entries-') as tmp:
            root = Path(tmp)
            store = {'agents': {'entries': {'main': {'name': 'main'}}}}
            first = self.run_tasks(root, store, [self.MAIN], {'main': True}, [self.set_defaults])
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            agents = self.store(root)['agents']
            self.assertEqual(agents['entries']['main']['heartbeat'],
                             {'target': 'telegram', 'to': '123', 'every': self.defaults['openclaw_heartbeat_interval']})
            # The safe global default uses the deployment's heartbeat model, never a hardcoded provider.
            self.assertEqual(agents['defaults']['heartbeat']['every'], '0m')
            self.assertEqual(agents['defaults']['heartbeat']['model'], self.defaults['openclaw_heartbeat_model'])
            self.assertTrue(all('agents.list' not in ' '.join(w) for w in self.writes(root)))
            (root / 'writes').unlink()
            second = self.run_tasks(root, store, [self.MAIN], {'main': True}, [self.set_defaults])
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(self.writes(root), [])

    def test_disabled_agent_loses_its_heartbeat_and_an_unset_one_is_left_alone(self):
        for existing in ({'every': '30m'}, None):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory(prefix='agents-entries-') as tmp:
                root = Path(tmp)
                entry = {'name': 'main', **({'heartbeat': existing} if existing else {})}
                store = {'agents': {'defaults': {}, 'entries': {'main': entry}}}
                result = self.run_tasks(root, store, [self.MAIN], {'main': False}, [])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn('heartbeat', self.store(root)['agents']['entries']['main'])
                expected = [['config', 'unset', 'agents.entries.main.heartbeat']] if existing else []
                self.assertEqual(self.writes(root), expected)

    def test_an_unreadable_config_fails_without_writing(self):
        with tempfile.TemporaryDirectory(prefix='agents-entries-') as tmp:
            root = Path(tmp)
            (root / 'broken').write_text('')
            result = self.run_tasks(root, {'agents': {'entries': {'main': {}}}}, [self.MAIN], {'main': True}, [])
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.writes(root), [])

    def test_non_default_sandbox_override_targets_the_agents_entry(self):
        with tempfile.TemporaryDirectory(prefix='agents-entries-') as tmp:
            root = Path(tmp)
            bob = {'id': 'bob', 'is_default': False, 'sandbox_mode': 'non-main',
                   'deliver_channel': 'telegram', 'deliver_to': '', 'deliver_type': 'dm'}
            store = {'agents': {'entries': {'main': {}, 'bob': {}}}}
            result = self.run_tasks(root, store, [self.MAIN, bob], {'main': False, 'bob': False}, [self.sandbox])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.store(root)['agents']['entries']['bob']['sandbox']['mode'], 'non-main')
            self.assertEqual(self.writes(root), [['config', 'set', 'agents.entries.bob.sandbox.mode', 'non-main']])


if __name__ == '__main__':
    unittest.main()
