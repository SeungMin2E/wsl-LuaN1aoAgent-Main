import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import auto_upload as app

PRIVATE_KEY_SENTINEL = b'-----BEGIN ' + b'PRIVATE KEY-----'


class FakeGitHub:
    repo = 'owner/repo'

    def __init__(self, remote):
        self.remote = {p: {'sha': sha, 'mode': '100755' if p.endswith('.sh') else '100644'}
                       for p, sha in remote.items()}
        self.calls = []
        self.race = False

    def head(self, branch):
        return 'old-commit'

    def tree(self, revision):
        return 'old-tree', self.remote

    def api(self, path, body=None, method=None):
        self.calls.append((path, body, method))
        if path == 'user':
            return {'login': 'owner'}
        if path.endswith('/blobs'):
            import base64
            return {'sha': app.blob_sha(base64.b64decode(body['content']))}
        if path.endswith('/trees'):
            return {'sha': 'new-tree'}
        if path.endswith('/commits'):
            return {'sha': 'new-commit'}
        if '/refs/' in path:
            if self.race:
                raise app.Blocked('Not a fast forward')
            return {'object': {'sha': body['sha']}}
        raise AssertionError(path)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / 'a.py'
        self.file.write_bytes(b'old')
        self.project = {'id': 'example', 'root': str(self.root), 'repo': 'owner/repo', 'branch': 'main'}
        self.base = {'a.py': app.blob_sha(b'old')}

    def run_sync(self, client, check=lambda project: None):
        files, rules = app.snapshot(self.root)
        return app.sync(self.project, client, self.base, files, rules, check=check)

    def mutations(self, client):
        return [call for call in client.calls if call[1] is not None]

    def test_success_keeps_unrelated_remote_files_and_no_force(self):
        self.file.write_bytes(b'new')
        client = FakeGitHub(dict(self.base, remote_only=app.blob_sha(b'external')))
        updated, commit = self.run_sync(client)
        self.assertEqual(updated, {'a.py': app.blob_sha(b'new')})
        self.assertEqual(commit, 'new-commit')
        tree = next(body for path, body, _ in client.calls if path.endswith('/trees'))
        self.assertEqual(tree['base_tree'], 'old-tree')
        self.assertEqual([entry['path'] for entry in tree['tree']], ['a.py'])
        self.assertEqual(client.calls[-1][1], {'sha': 'new-commit', 'force': False})

    def test_tests_fail_before_any_remote_call(self):
        self.file.write_bytes(b'new')
        client = FakeGitHub(self.base)
        def fail(project):
            raise app.Blocked('failed tests')
        with self.assertRaisesRegex(app.Blocked, 'failed tests'):
            self.run_sync(client, check=fail)
        self.assertEqual(client.calls, [])

    def test_changes_during_tests_defer_upload(self):
        self.file.write_bytes(b'new')
        client = FakeGitHub(self.base)
        with self.assertRaisesRegex(app.Blocked, 'during tests'):
            self.run_sync(client, check=lambda project: self.file.write_bytes(b'newer'))
        self.assertEqual(client.calls, [])

    def test_independent_remote_change_blocks_mutations(self):
        self.file.write_bytes(b'new')
        client = FakeGitHub({'a.py': app.blob_sha(b'external')})
        with self.assertRaisesRegex(app.Blocked, 'independently'):
            self.run_sync(client)
        self.assertEqual(self.mutations(client), [])

    def test_recover_after_commit_succeeded_but_state_not_saved(self):
        self.file.write_bytes(b'new')
        client = FakeGitHub({'a.py': app.blob_sha(b'new')})
        updated, commit = self.run_sync(client)
        self.assertEqual(updated['a.py'], app.blob_sha(b'new'))
        self.assertEqual(self.mutations(client), [])

    def test_concurrent_remote_commit_rejects_without_changing_baseline(self):
        self.file.write_bytes(b'new')
        client = FakeGitHub(self.base)
        client.race = True
        original = copy.deepcopy(self.base)
        with self.assertRaisesRegex(app.Blocked, 'fast forward'):
            self.run_sync(client)
        self.assertEqual(self.base, original)

    def test_new_file_conflict_is_not_overwritten(self):
        (self.root / 'new.md').write_bytes(b'local')
        client = FakeGitHub(dict(self.base, **{'new.md': app.blob_sha(b'remote')}))
        with self.assertRaisesRegex(app.Blocked, 'independently'):
            self.run_sync(client)
        self.assertEqual(self.mutations(client), [])

    def test_normal_delete_and_add_keep_shell_mode(self):
        (self.root / 'b.txt').write_bytes(b'keep')
        self.base['b.txt'] = app.blob_sha(b'keep')
        self.file.unlink()
        (self.root / 'setup.sh').write_bytes(b'#!/bin/sh\n')
        client = FakeGitHub(self.base)
        updated, _ = self.run_sync(client)
        self.assertNotIn('a.py', updated)
        tree = next(body for path, body, _ in client.calls if path.endswith('/trees'))
        entries = {x['path']: x for x in tree['tree']}
        self.assertIsNone(entries['a.py']['sha'])
        self.assertEqual(entries['setup.sh']['mode'], '100755')

    def test_unchanged_project_does_not_run_tests_or_use_network(self):
        client = FakeGitHub(self.base)
        self.run_sync(client, check=lambda project: self.fail('Tests should not run'))
        self.assertEqual(client.calls, [])

    def test_late_save_before_ref_update_defers_publish(self):
        self.file.write_bytes(b'new')
        client = FakeGitHub(self.base)
        original = client.api
        def mutate(path, body=None, method=None):
            result = original(path, body, method)
            if path.endswith('/commits'):
                self.file.write_bytes(b'newer')
            return result
        client.api = mutate
        with self.assertRaisesRegex(app.Blocked, 'before upload'):
            self.run_sync(client)
        self.assertFalse(any('/refs/' in path for path, _, _ in client.calls))

    def test_redirected_remote_file_type_requires_review(self):
        self.file.write_bytes(b'new')
        client = FakeGitHub(self.base)
        client.remote['a.py']['mode'] = '120000'
        with self.assertRaisesRegex(app.Blocked, 'file type'):
            self.run_sync(client)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'main.py').write_bytes(b'print(1)')

    def test_excludes_private_files_cache_and_gitignored_results(self):
        for name in ('.env.local', 'token.key', 'output.log'):
            (self.root / name).write_bytes(b'private')
        (self.root / '.gitignore').write_text('scratch/\n*.zip\n', encoding='utf-8')
        for name in ('__pycache__', 'scratch', 'node_modules', 'results'):
            (self.root / name).mkdir()
            (self.root / name / 'data.txt').write_bytes(b'excluded')
        (self.root / 'archive.zip').write_bytes(b'zip')
        files, _ = app.snapshot(self.root)
        self.assertEqual(set(files), {'main.py', '.gitignore'})

    def test_nested_ignore_is_scoped(self):
        (self.root / 'sub').mkdir()
        (self.root / 'sub' / '.gitignore').write_text('skip.txt\n', encoding='utf-8')
        (self.root / 'sub' / 'skip.txt').write_bytes(b'x')
        (self.root / 'skip.txt').write_bytes(b'keep')
        files, _ = app.snapshot(self.root)
        self.assertIn('skip.txt', files)
        self.assertNotIn('sub/skip.txt', files)

    def test_agent_runtime_directories_are_never_traversed(self):
        for name in ('.agent-runtime', '.agents', '.runtime', '.playwright-cli'):
            directory = self.root / name
            directory.mkdir()
            # These private directories must not be scanned, even if they
            # contain unsupported ignore rules or secret-shaped runtime data.
            (directory / '.gitignore').write_text('!private.txt\n', encoding='utf-8')
            (directory / 'private.txt').write_bytes(PRIVATE_KEY_SENTINEL)
        files, _ = app.snapshot(self.root)
        self.assertEqual(set(files), {'main.py'})

    def test_env_template_is_allowed_but_real_env_and_secrets_are_blocked(self):
        (self.root / '.env.example').write_bytes(b'LLM_API_KEY=your-api-key\n')
        (self.root / '.env').write_bytes(b'private')
        files, _ = app.snapshot(self.root)
        self.assertIn('.env.example', files)
        self.assertNotIn('.env', files)
        (self.root / '.env.example').write_bytes(b'ghp_' + b'x' * 36)
        with self.assertRaisesRegex(app.Blocked, 'Possible secret'):
            app.snapshot(self.root)

    def test_negated_ignore_blocks_instead_of_guessing(self):
        (self.root / '.gitignore').write_text('!include.txt\n', encoding='utf-8')
        with self.assertRaisesRegex(app.Blocked, 'Unsupported'):
            app.snapshot(self.root)

    def test_private_key_and_token_content_block(self):
        for data in (PRIVATE_KEY_SENTINEL, b'ghp_' + b'x' * 36):
            with self.subTest(data=data):
                (self.root / 'accidental.txt').write_bytes(data)
                with self.assertRaisesRegex(app.Blocked, 'Possible secret'):
                    app.snapshot(self.root)

    def test_missing_and_empty_roots_cannot_delete_remote(self):
        with self.assertRaises(app.Blocked):
            app.snapshot(self.root / 'missing')
        (self.root / 'main.py').unlink()
        with self.assertRaisesRegex(app.Blocked, 'No source'):
            app.snapshot(self.root)

    def test_mass_delete_blocks(self):
        baseline = {str(i): 'old' for i in range(10)}
        with self.assertRaisesRegex(app.Blocked, 'Large deletion'):
            app.changes_for(baseline, {'0': 'old'}, [])

    def test_newly_ignored_tracked_file_is_preserved_on_remote(self):
        changes = app.changes_for({'a.txt': 'old', 'main.py': 'same'}, {'main.py': 'same'}, [('', 'a.txt')])
        self.assertEqual(changes, {})

    def test_size_limit_blocks(self):
        (self.root / 'big.bin').write_bytes(b'x' * (8 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(app.Blocked, '8 MiB'):
            app.snapshot(self.root)

    def test_atomic_json_state(self):
        path = self.root / 'state' / 'baseline.json'
        app.write_json(path, {'one': 1})
        app.write_json(path, {'two': 2})
        self.assertEqual(json.loads(path.read_text()), {'two': 2})
        self.assertFalse(path.with_suffix('.tmp').exists())


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'a.py').write_bytes(b'new')
        self.state = self.root / 'state'
        self.state.mkdir()
        self.project = {'id': 'example', 'root': str(self.source), 'repo': 'owner/repo', 'branch': 'main'}
        self.config = {'projects': [self.project], 'gh': 'unused', 'quiet_seconds': 60,
                       'poll_seconds': 5, 'retry_seconds': 300}
        self.baseline = {'example': {'a.py': app.blob_sha(b'old')}}
        app.write_json(self.state / 'baseline.json', self.baseline)
        self.addCleanup(self.close_logs)

    def close_logs(self):
        import logging
        for handler in list(logging.getLogger().handlers):
            logging.getLogger().removeHandler(handler)
            handler.close()

    def watch(self, **kwargs):
        with patch.object(app, 'HOME', self.root), patch.object(app, 'STATE', self.state):
            app.watch(self.config, **kwargs)

    def test_quiet_period_does_not_upload_immediately(self):
        with patch.object(app, 'sync') as sync:
            self.watch(once=True)
        sync.assert_not_called()
        status = json.loads((self.state / 'status.json').read_text())
        self.assertIn('Pending', status['projects']['example']['message'])

    def test_success_persists_synced_baseline(self):
        self.config['quiet_seconds'] = 0
        with patch.object(app, 'GitHub', return_value=FakeGitHub(self.baseline['example'])):
            original_sync = app.sync
            with patch.object(app, 'sync', side_effect=lambda *args: original_sync(*args, check=lambda p: None)):
                self.watch(once=True)
        baseline = json.loads((self.state / 'baseline.json').read_text())
        self.assertEqual(baseline['example']['a.py'], app.blob_sha(b'new'))
        status = json.loads((self.state / 'status.json').read_text())
        self.assertEqual(status['projects']['example']['last_upload']['commit'], 'new-commit')

    def test_upload_failure_preserves_baseline_and_records_reason(self):
        self.config['quiet_seconds'] = 0
        with patch.object(app, 'sync', side_effect=app.Blocked('network unavailable')):
            self.watch(once=True)
        self.assertEqual(json.loads((self.state / 'baseline.json').read_text()), self.baseline)
        status = json.loads((self.state / 'status.json').read_text())
        self.assertIn('network unavailable', status['projects']['example']['message'])

    def test_stop_signal_prevents_new_work(self):
        (self.root / 'STOP').write_text('stop')
        with patch.object(app, 'sync') as sync:
            self.watch(once=True)
        sync.assert_not_called()

    def test_disabled_project_is_not_read_or_uploaded_and_preserves_history(self):
        self.project['enabled'] = False
        self.config['quiet_seconds'] = 0
        prior = {'projects': {'example': {'last_upload': {'commit': 'previous'}}}}
        app.write_json(self.state / 'status.json', prior)
        with patch.object(app, 'snapshot') as snapshot, patch.object(app, 'sync') as sync, \
             patch.object(app, 'GitHub') as client:
            self.watch(once=True)
        snapshot.assert_not_called()
        sync.assert_not_called()
        client.assert_not_called()
        self.assertEqual(json.loads((self.state / 'baseline.json').read_text()), self.baseline)
        status = json.loads((self.state / 'status.json').read_text())['projects']['example']
        self.assertIn('disabled', status['message'])
        self.assertEqual(status['last_upload']['commit'], 'previous')

    def test_disabled_project_does_not_pause_another_enabled_project(self):
        self.config['projects'].insert(0, {'id': 'held', 'enabled': False, 'root': 'missing'})
        self.config['quiet_seconds'] = 0
        with patch.object(app, 'sync', return_value=(self.baseline['example'], 'new-commit')) as sync:
            self.watch(once=True)
        self.assertEqual(sync.call_count, 1)
        self.assertEqual(sync.call_args.args[0]['id'], 'example')

    def test_invalid_enable_value_cannot_authorize_upload(self):
        for value in ('false', 'true', None, 1, 0):
            with self.subTest(value=value), patch.object(app, 'sync') as sync:
                self.project['enabled'] = value
                self.config['quiet_seconds'] = 0
                self.watch(once=True)
                sync.assert_not_called()
                self.close_logs()


if __name__ == '__main__':
    unittest.main(verbosity=2)
