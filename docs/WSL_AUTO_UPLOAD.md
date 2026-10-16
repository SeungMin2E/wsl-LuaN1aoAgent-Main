# WSL source automatic upload

The local Windows uploader sends saved source from this WSL working directory to
[SeungMin2E/wsl-LuaN1aoAgent-Main](https://github.com/SeungMin2E/wsl-LuaN1aoAgent-Main)
on the main branch.

The initial import contains a verified snapshot of the current source and the
original LICENSE. Previous Git history is not part of this import. The WSL
working directory retains its original upstream origin.

## Local operation

The Windows uploader is configured in Deeppoint/github-auto-upload/config.json.
It reads /home/ksm/LuaN1aoAgent in the Ubuntu-24.04 distribution through the WSL
network share. After files remain unchanged for 60 seconds, it builds the traffic
proxy and server/Web UI, runs server tests sequentially, and runs Web tests in
WSL. Passing changes are committed through the GitHub API without forcing the
remote branch.

The uploader starts at Windows login. It requires a logged-in Windows session,
access to this WSL distribution, and the existing GitHub CLI login. This chat
and VS Code may be closed. It uploads saved files without making a local Git
commit, so local git status may still show changes. Independent remote changes
to the same files pause upload rather than overwrite them. Test failures and
unavailable network access also postpone upload.

The local check runner selects Node >=25 from the WSL user's nvm installation.
Its Go build tool is installed separately under
~/.local/share/luanniao-build/go1.27.2/go/bin.
These local tools and the Windows watcher are not bundled in the source snapshot.

## Source and private state

The source, tests, documentation, LICENSE, and .env.example are included.
The real .env, environment backups, private keys, recognized tokens, Git
metadata, dependencies, build outputs, and .agent-runtime data are excluded.
Sessions, login databases, captured traffic, runtime credentials, and project
skills are not uploaded. The repository is public.

Status and test errors are available through github-auto-upload/manage.ps1 status
and github-auto-upload/state/status.json. The existing enable/disable commands
control the shared watcher, including the two previously configured projects.
To pause only this repository, stop the watcher, set this project's enabled
value to false, and restart the watcher.

## Linux internal bridge compatibility

This source includes the opt-in Linux task bridge compatibility fix.
Set LUANNIAO_LINUX_BRIDGE_COMPAT=1 in the local, untracked .env to enable it on
Linux hosts using Docker's iptables backend. The task-network lifecycle admits
only layer-2 frames remaining on that same internal task bridge, so the
container Gateway can receive traffic addressed to authorized destinations.

The Gateway continues enforcing authorized scope and recording traffic.
The compatibility rule is removed on normal task cleanup. A crash may leave an
unused rule referring to a removed bridge; it is not a global firewall disable.
