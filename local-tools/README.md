# Local deployment and diagnostics tools

These are source copies of the tools used for this WSL agent deployment.
The live Windows watcher remains in Deeppoint/github-auto-upload. Copying these
files does not install, start, or replace a watcher.

## windows-auto-upload

Includes the uploader, regression tests, WSL build/test runner, management
commands, and a configuration example for the three selected repositories.
The GitHub CLI executable and credentials, watcher state/baselines, logs, and
actual local configuration are excluded.

To adapt the source to another machine, copy config.example.json to config.json
and adjust its account, paths, repositories, and check commands. The Python path
in manage.ps1 and WSL paths in verify_wsl_agent.py are deployment-specific.
Install GitHub CLI separately and sign in. With matching repositories already
initialized, run python auto_upload.py init once to create the local baseline.
Use the management commands to enable or disable the watcher. Do not run two
separate watchers against the same repositories with different baselines.

The current Windows installation already has its baseline and startup entry.
Do not replace its baseline with the example or run init again there.

## runpod-agent-test

Includes the local RunPod model-configuration helper, private Docker lab
adapter/manager, connectivity integration check, Linux bridge patch, and recovery
notes. No SSH private keys, real .env files, database sessions, or captured
traffic are included.

configure.py points to /home/ksm/LuaN1aoAgent and writes local model configuration
with a backup. It does not open the SSH forward. Inspect and adapt its settings
before using it for a different deployment.

lab.py expects the separate test-site repository in a sibling folder named
해킹 툴 테스트 사이트. For example, beside runpod-agent-test clone
https://github.com/SeungMin2E/ai-agent-tool-test-site into that folder.
The executor image must already exist. Run these tools under Linux/WSL.
The lab adapter exposes the practice listeners only inside its private Docker
network and leaves the lab's internal service on loopback.

With the live private lab running, the connectivity check can be run from this
repository with Node 25:
node local-tools/runpod-agent-test/check_agent_connectivity.mjs

This creates a temporary task network, verifies HTTP access to the managed lab,
checks out-of-scope rejection, and removes its containers and compatibility
rule. It does not run a model or attempt to exploit the practice application.
The patch and recovery note document the original troubleshooting snapshot;
the current agent source is the maintained implementation.
