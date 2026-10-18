"""Point the user's local agent at the WSL-only RunPod SSH forward."""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import tempfile

root = Path('/home/ksm/LuaN1aoAgent')
path = root/'.env'
if not root.is_dir() or path.is_symlink():
    raise RuntimeError('Unexpected agent path; nothing changed')
old = path.read_text(encoding='utf-8') if path.exists() else ''
updates = {
    'LLM_API_KEY':'runpod-local',
    'LLM_API_BASE_URL':'http://127.0.0.1:18000/v1',
    'LLM_DEFAULT_MODEL':'Inferact/Qwen3.8-27B-NVFP4',
    'LLM_API_TYPE':'openai-completions',
    'LLM_MAX_TOKENS':'2048',
    'LLM_CONTEXT_WINDOW':'8192',
    'LLM_THINKING':'off',
    'LLM_THINKING_FORMAT':'qwen-chat-template',
    'LLM_PROVIDER_MAX_CONCURRENT':'1',
}
for role in ('PLANNER','EXECUTOR','SUPERVISOR','PROJECTOR'):
    for field,value in (('MODEL',updates['LLM_DEFAULT_MODEL']),('BASE_URL',updates['LLM_API_BASE_URL']),
                        ('API_KEY',updates['LLM_API_KEY']),('MAX_TOKENS','2048'),
                        ('CONTEXT_WINDOW','8192'),('THINKING','off')):
        updates['LLM_'+role+'_'+field] = value
lines = []
pending = dict(updates)
for line in old.splitlines():
    match = re.match(r'^\s*([A-Z][A-Z0-9_]*)\s*=',line)
    if match and match[1] in updates:
        key = match[1]
        if key in pending:
            lines.append(key+'='+pending.pop(key))
    else:
        lines.append(line)
lines.extend(key+'='+value for key,value in pending.items())
new = '\n'.join(lines)+'\n'
if new != old:
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = root/('.env.runpod-'+stamp+'.bak')
        fd = os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            handle.write(old)
        print('Original configuration backed up:',backup.name)
    fd,temp = tempfile.mkstemp(prefix='.env-runpod-',dir=root)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            handle.write(new)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp,0o600)
        os.replace(temp,path)
    finally:
        Path(temp).unlink(missing_ok=True)
print('Agent configured for http://127.0.0.1:18000/v1, context 8192, output 2048.')
print('The SSH forward must be open before starting an agent run.')
