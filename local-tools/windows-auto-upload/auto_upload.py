"""Local save -> quiet period -> tests -> non-forced GitHub commit.

Uses the existing GitHub CLI login. No token is stored in this directory.
"""
import argparse
import base64
import fnmatch
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import subprocess
import sys
import time

HOME = Path(__file__).resolve().parent
STATE = HOME / 'state'
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
SKIP_DIRS = {'.git', '.venv', 'venv', '__pycache__', 'node_modules', '.pytest_cache',
             'hf-cache', 'results', 'dist', 'build', '.idea', '.vscode',
             '.agent-runtime', '.agents', '.runtime', '.playwright-cli'}
PRIVATE = ('.env*', '*.pem', '*.key', '*.token', '*.p12', '*.pfx', '*credentials*',
           'id_rsa*', 'id_ed25519*', '*.log', '*.pyc', '*.pyo', '*.swp', '*.tmp')
SECRETS = re.compile(rb'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----|'
                     rb'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|'
                     rb'AKIA[A-Z0-9]{16}|hf_[A-Za-z0-9]{25,}|sk-[A-Za-z0-9_-]{30,})\b')


class Blocked(RuntimeError):
    pass


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def blob_sha(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def hashes(files):
    return {name: blob_sha(data) for name, data in files.items()}


def digest(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def ignored(name, rules):
    parts = name.split('/')
    if any(p in SKIP_DIRS or p.endswith('-state') for p in parts[:-1]):
        return True
    if any(fnmatch.fnmatchcase(p.lower(), pattern)
           for p in parts if p != '.env.example' for pattern in PRIVATE):
        return True
    for scope, pattern in rules:
        if not name.startswith(scope):
            continue
        relative = name[len(scope):]
        directory = pattern.endswith('/')
        anchored = pattern.startswith('/')
        pattern = pattern.strip('/')
        # Patterns without a slash apply at any depth in this .gitignore scope.
        candidates = [relative] if anchored or '/' in pattern else relative.split('/')
        if directory:
            candidates = (['/'.join(relative.split('/')[:i])
                           for i in range(1, len(relative.split('/')))]
                          if anchored or '/' in pattern else relative.split('/')[:-1])
        if any(fnmatch.fnmatchcase(c, pattern) for c in candidates):
            return True
    return False


def snapshot(root):
    root = Path(root)
    if not root.is_dir() or root.is_symlink() or root.is_junction():
        raise Blocked('Project folder missing or redirected; upload paused')
    paths, rules = [], []
    for directory, dirs, names in os.walk(root, followlinks=False):
        here = Path(directory)
        for name in dirs:
            path = here / name
            if name not in SKIP_DIRS and (path.is_symlink() or path.is_junction()):
                raise Blocked('Redirected subfolder requires review: ' + path.relative_to(root).as_posix())
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith('-state')
                   and not (here / d).is_symlink() and not (here / d).is_junction()]
        if '.gitignore' in names:
            if (here / '.gitignore').is_symlink():
                raise Blocked('Symlink .gitignore requires review')
            scope = here.relative_to(root).as_posix()
            scope = '' if scope == '.' else scope + '/'
            for line in (here / '.gitignore').read_text(encoding='utf-8-sig').splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('!') or '\\' in line:
                    raise Blocked('Unsupported .gitignore exception/escape; review before upload')
                rules.append((scope, line))
        paths.extend(here / name for name in names)
    files = {}
    for path in paths:
        name = path.relative_to(root).as_posix()
        if ignored(name, rules):
            continue
        if path.is_symlink():
            raise Blocked('Symlink file requires review: ' + name)
        if path.stat().st_size > 8 * 1024 * 1024:
            raise Blocked('File exceeds 8 MiB automatic upload limit: ' + name)
        data = path.read_bytes()
        if SECRETS.search(data):
            raise Blocked('Possible secret detected in ' + name)
        files[name] = data
    if not files:
        raise Blocked('No source files found; refusing an empty upload')
    if sum(map(len, files.values())) > 40 * 1024 * 1024:
        raise Blocked('Project exceeds 40 MiB automatic upload limit')
    return files, rules


def changes_for(baseline, current, rules):
    changes = {p: current.get(p) for p in baseline.keys() | current.keys()
               if not ignored(p, rules) and baseline.get(p) != current.get(p)}
    deletions = sum(value is None for value in changes.values())
    if deletions and (deletions == len(baseline) or deletions > max(3, len(baseline) // 2)):
        raise Blocked('Large deletion requires review; upload paused')
    return changes


def verify_conflicts(baseline, current, remote, changes):
    for path, wanted in changes.items():
        actual = remote.get(path, {}).get('sha')
        if actual not in (baseline.get(path), wanted):
            raise Blocked('GitHub file changed independently: ' + path)
        if actual is not None and remote[path]['mode'] not in ('100644', '100755'):
            raise Blocked('Remote file type requires review: ' + path)


class GitHub:
    def __init__(self, exe, repo):
        self.exe, self.repo = str(exe), repo

    def api(self, path, body=None, method=None):
        args = [self.exe, 'api', path, '--method', method or ('POST' if body is not None else 'GET'),
                '-H', 'Accept: application/vnd.github+json',
                '-H', 'X-GitHub-Api-Version: 2026-03-10']
        if body is not None:
            args += ['--input', '-']
        result = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                                capture_output=True, text=True, encoding='utf-8', timeout=90,
                                creationflags=HIDDEN)
        if result.returncode:
            # Do not include CLI stderr: an upstream response may contain file contents.
            raise Blocked('GitHub request failed; check login/network/permissions (' + path + ')')
        return json.loads(result.stdout)

    def tree(self, revision):
        commit = self.api(f'repos/{self.repo}/git/commits/{revision}')
        tree_sha = commit['tree']['sha']
        tree = self.api(f'repos/{self.repo}/git/trees/{tree_sha}?recursive=1')
        if tree.get('truncated'):
            raise Blocked('Remote tree is truncated; manual review required')
        return tree_sha, {x['path']: x for x in tree['tree'] if x['type'] == 'blob'}

    def head(self, branch):
        return self.api(f'repos/{self.repo}/git/ref/heads/{branch}')['object']['sha']


def run_checks(project):
    root = Path(project['root'])
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    for command in project['checks']:
        args = [sys.executable if x == '{python}' else x for x in command]
        try:
            result = subprocess.run(args, cwd=root, env=env, capture_output=True,
                                    text=True, encoding='utf-8', errors='replace', timeout=240,
                                    creationflags=HIDDEN)
        except subprocess.TimeoutExpired:
            raise Blocked('Tests timed out after 240 seconds') from None
        if result.returncode:
            # Test output is kept locally, with recognizable credentials redacted.
            output = (result.stdout + '\n' + result.stderr)[-12000:].encode('utf-8')
            output = SECRETS.sub(b'[REDACTED]', output).decode('utf-8')
            STATE.mkdir(exist_ok=True)
            (STATE / (project['id'] + '-tests.txt')).write_text(output, encoding='utf-8')
            raise Blocked('Tests failed; see state/' + project['id'] + '-tests.txt')


def sync(project, client, baseline, files, rules, check=run_checks):
    current = hashes(files)
    changes = changes_for(baseline, current, rules)
    if not changes:
        return baseline, None
    check(project)
    if hashes(snapshot(project['root'])[0]) != current:
        raise Blocked('Files changed during tests; waiting for a fresh quiet period')
    if client.api('user')['login'].lower() != project['repo'].split('/')[0].lower():
        raise Blocked('GitHub login changed; upload paused')
    head = client.head(project['branch'])
    tree_sha, remote = client.tree(head)
    verify_conflicts(baseline, current, remote, changes)
    entries = []
    for path, wanted in sorted(changes.items()):
        if remote.get(path, {}).get('sha') == wanted:
            continue
        mode = remote.get(path, {}).get('mode', '100755' if path.endswith('.sh') else '100644')
        if wanted is not None:
            blob = client.api(f'repos/{client.repo}/git/blobs',
                              {'content': base64.b64encode(files[path]).decode(), 'encoding': 'base64'})
            if blob['sha'] != wanted:
                raise Blocked('GitHub blob verification failed: ' + path)
        entries.append({'path': path, 'mode': mode, 'type': 'blob', 'sha': wanted})
    if entries:
        tree = client.api(f'repos/{client.repo}/git/trees', {'base_tree': tree_sha, 'tree': entries})
        commit = client.api(f'repos/{client.repo}/git/commits',
                            {'message': 'Auto-update after local tests: ' + time.strftime('%Y-%m-%d %H:%M:%S'),
                             'tree': tree['sha'], 'parents': [head]})
        if hashes(snapshot(project['root'])[0]) != current:
            raise Blocked('Files changed before upload; waiting for a fresh quiet period')
        client.api(f'repos/{client.repo}/git/refs/heads/{project["branch"]}',
                   {'sha': commit['sha'], 'force': False}, method='PATCH')
        head = commit['sha']
    # Unrelated remote updates are preserved. Only changed local paths advance their baseline.
    updated = dict(baseline)
    for path, wanted in changes.items():
        if wanted is None:
            updated.pop(path, None)
        else:
            updated[path] = wanted
    return updated, head


def initialize(config):
    if (STATE / 'baseline.json').exists():
        raise Blocked('Baseline already exists; refusing to overwrite sync history')
    expected = config['account']
    if GitHub(config['gh'], '').api('user')['login'].lower() != expected.lower():
        raise Blocked('GitHub account does not match configuration')
    baseline = {}
    for project in config['projects']:
        snapshot(project['root'])
        client = GitHub(config['gh'], project['repo'])
        _, tree = client.tree(project['initial_commit'])
        # Anchor to the actual previous upload, so edits made since then are not silently skipped.
        baseline[project['id']] = {p: x['sha'] for p, x in tree.items()}
        client.tree(client.head(project['branch']))
        run_checks(project)
    write_json(STATE / 'baseline.json', baseline)
    print('Configured projects verified; initial sync baseline saved.')


def watch(config, once=False):
    STATE.mkdir(exist_ok=True)
    import msvcrt
    lock = (STATE / 'watcher.lock').open('a+b')
    lock.seek(0)
    if not lock.read(1):
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return  # Another instance owns the lock.
    handler = RotatingFileHandler(STATE / 'watcher.log', maxBytes=1024 * 1024,
                                 backupCount=3, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format='%(asctime)s %(levelname)s %(message)s')
    baseline = json.loads((STATE / 'baseline.json').read_text(encoding='utf-8'))
    pending, last_attempt, statuses = {}, {}, {}
    if (STATE / 'status.json').exists():
        try:
            statuses = json.loads((STATE / 'status.json').read_text(encoding='utf-8')).get('projects', {})
        except (OSError, ValueError):
            pass
    logging.info('Watcher started (PID %s)', os.getpid())
    try:
        while not (HOME / 'STOP').exists():
            for project in config['projects']:
                key = project['id']
                # An explicit per-project hold survives restarts and Windows login.
                # Treat invalid values as disabled instead of accidentally publishing.
                if project.get('enabled', True) is not True:
                    pending.pop(key, None)
                    statuses.setdefault(key, {}).update(
                        message='Automatic upload disabled; awaiting explicit user request',
                        time=time.strftime('%Y-%m-%d %H:%M:%S'))
                    continue
                now = time.monotonic()
                try:
                    files, rules = snapshot(project['root'])
                    current = hashes(files)
                    fingerprint = digest(current)
                    changes = changes_for(baseline[key], current, rules)
                    if not changes:
                        pending.pop(key, None)
                        message = 'Watching; local files synced'
                    else:
                        if pending.get(key, (None,))[0] != fingerprint:
                            pending[key] = (fingerprint, now)
                            last_attempt.pop(key, None)
                        stable = now - pending[key][1] >= config['quiet_seconds']
                        retry_due = now - last_attempt.get(key, -float('inf')) >= config['retry_seconds']
                        message = 'Pending changes: ' + str(len(changes))
                        if stable and retry_due:
                            last_attempt[key] = now
                            statuses.setdefault(key, {}).update(message='Running tests before upload', time=time.strftime('%Y-%m-%d %H:%M:%S'))
                            write_json(STATE / 'status.json', {'pid': os.getpid(), 'heartbeat': time.time(), 'projects': statuses})
                            updated, commit = sync(project, GitHub(config['gh'], project['repo']),
                                                   baseline[key], files, rules)
                            baseline[key] = updated
                            write_json(STATE / 'baseline.json', baseline)
                            pending.pop(key, None)
                            message = 'Uploaded: ' + str(commit)
                            statuses[key]['last_upload'] = {'commit': commit, 'time': time.strftime('%Y-%m-%d %H:%M:%S')}
                except Exception as exc:
                    message = 'Paused: ' + str(exc)
                if statuses.get(key, {}).get('message') != message:
                    logging.info('%s: %s', key, message)
                statuses.setdefault(key, {}).update(message=message, time=time.strftime('%Y-%m-%d %H:%M:%S'))
            write_json(STATE / 'status.json', {'pid': os.getpid(), 'heartbeat': time.time(), 'projects': statuses})
            if once:
                break
            for _ in range(config['poll_seconds']):
                if (HOME / 'STOP').exists():
                    break
                time.sleep(1)
    finally:
        logging.info('Watcher stopped')
        write_json(STATE / 'status.json', {'pid': None, 'heartbeat': time.time(), 'projects': statuses})
        lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['init', 'watch', 'once', 'status'])
    args = parser.parse_args()
    config = json.loads((HOME / 'config.json').read_text(encoding='utf-8'))
    if args.command == 'status':
        print((STATE / 'status.json').read_text(encoding='utf-8') if (STATE / 'status.json').exists() else 'Not started')
    elif args.command == 'init':
        initialize(config)
    else:
        watch(config, once=args.command == 'once')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        STATE.mkdir(exist_ok=True)
        (STATE / 'fatal.txt').write_text(str(exc), encoding='utf-8')
        raise
