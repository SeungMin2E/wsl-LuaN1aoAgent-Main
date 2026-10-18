// Run in WSL with Node 25. Creates and cleans up one diagnostic task network.
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir, homedir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const exec = promisify(execFile);
const root = process.env.LUANNIAO_ROOT || join(homedir(), 'LuaN1aoAgent');
const { NetworkSandboxManager } = await import(pathToFileURL(join(root, 'dist/src/connectivity/network-sandbox-manager.js')));
const { createDockerTaskSandbox } = await import(pathToFileURL(join(root, 'dist/src/executor-sandbox-docker.js')));
const docker = async args => (await exec('docker', args, { timeout: 30_000 })).stdout.trim();
const lab = JSON.parse(await docker(['inspect', 'ksm-luan1ao-lab']))[0];
const address = lab.NetworkSettings.Networks['ksm-luan1ao-lab-net'].IPAddress;
assert.match(address, /^172\.18\.\d{1,3}\.\d{1,3}$/);
const runtimeDir = await mkdtemp(join(tmpdir(), 'luanniao-bridge-verify-'));
const manager = new NetworkSandboxManager({
  runtimeDir, runRef: 'bridge-verification', manageFlowIndex: false,
  environment: { ...process.env, LUANNIAO_LINUX_BRIDGE_COMPAT: '1' }
});
let sandbox;
let bridge;
try {
  await manager.configureAuthorizedScope(`${address}/32`);
  const gateway = await manager.createGateway({ taskId: 'http-check', epochId: 'http-check' });
  const repeated = await manager.createGateway({ taskId: 'http-check', epochId: 'http-check' });
  assert.equal(repeated.containerName, gateway.containerName);
  const id = await docker(['network', 'inspect', '--format', '{{.Id}}', gateway.networkName]);
  bridge = `br-${id.slice(0, 12)}`;
  sandbox = await createDockerTaskSandbox({
    runtimeDir, runRef: 'bridge-verification', taskId: 'http-check',
    network: gateway, transparentCaPath: manager.gatewayCaPath()
  });
  await sandbox.start();
  const curl = url => docker(['exec', sandbox.containerName, 'curl', '--noproxy', '*',
    '--connect-timeout', '4', '--max-time', '8', '-sS', '-o', '/dev/null', '-w', '%{http_code}', url]);
  assert.equal(await curl(`http://${address}:8101/`), '200');
  console.log('PASS: Executor -> Gateway -> host broker -> lab HTTP 200');
  await assert.rejects(curl('http://192.0.2.1:8101/'));
  console.log('PASS: out-of-scope TCP remains blocked');
} finally {
  try { if (sandbox) await sandbox.dispose(); } finally { await manager.close(); }
}
const rules = await docker(['run', '--rm', '--network', 'host', '--user', '0',
  '--cap-drop', 'ALL', '--cap-add', 'NET_ADMIN', '--read-only',
  '--entrypoint', 'iptables', manager.image, '-S', 'DOCKER-USER']);
assert.ok(!rules.includes(bridge));
console.log('PASS: task bridge rule removed during cleanup');
console.log(`Diagnostic metadata retained: ${runtimeDir}`);
