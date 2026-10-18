"""Build and test the WSL agent without running research tasks or changing its env."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path('/home/ksm/LuaN1aoAgent')
    versions = Path.home() / '.nvm/versions/node'
    candidates = []
    for directory in versions.glob('v*/bin'):
        try:
            version = tuple(int(part) for part in directory.parent.name.lstrip('v').split('.'))
        except ValueError:
            continue
        if version[0] >= 25 and (directory / 'node').is_file():
            candidates.append((version, directory))
    if not candidates:
        raise RuntimeError('Node >=25 is required in the WSL user environment')
    node_bin = max(candidates)[1]
    go_bin = Path.home() / '.local/share/luanniao-build/go1.27.2/go/bin'
    env = {**os.environ, 'PATH': os.pathsep.join([str(node_bin), str(go_bin), os.environ.get('PATH', '')])}
    proxy_build = subprocess.run([str(node_bin / 'npm'), 'run', 'build:traffic-proxy'], cwd=root, env=env)
    if proxy_build.returncode:
        return proxy_build.returncode
    build = subprocess.run([str(node_bin / 'npm'), 'run', 'build'], cwd=root, env=env)
    if build.returncode:
        return build.returncode
    commands = [
        [str(node_bin / 'node'), '--test', '--test-concurrency=1', '--test-force-exit',
         *(str(path) for path in sorted((root / 'dist').rglob('*.test.js')))],
        [str(node_bin / 'node'), 'node_modules/vitest/vitest.mjs', 'run', '--config', 'web/vite.config.ts'],
    ]
    for command in commands:
        print('Checking:', command[1:4], flush=True)
        result = subprocess.run(command, cwd=root, env=env)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == '__main__':
    sys.exit(main())
