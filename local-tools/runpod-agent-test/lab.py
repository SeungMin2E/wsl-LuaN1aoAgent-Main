"""Manage only this private Docker lab; no published ports or image downloads."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

NAME = 'ksm-luan1ao-lab'
NETWORK = 'ksm-luan1ao-lab-net'
LABEL = 'local.ksm.runpod-lab'
IMAGE = 'luanniao-executor:latest'
HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent/'해킹 툴 테스트 사이트'

def docker(*args,optional=False):
    result = subprocess.run(['docker',*args],capture_output=True,text=True,timeout=45)
    if result.returncode:
        if optional:
            return None
        raise RuntimeError(result.stderr.strip()[:1500])
    return result.stdout.strip()

def inspect(kind,name):
    text = docker(kind,'inspect',name,optional=True)
    return json.loads(text)[0] if text else None

def owned(value,kind):
    labels = value.get('Config',{}).get('Labels',{}) if kind=='container' else value.get('Labels',{})
    if (labels or {}).get(LABEL) != '1':
        raise RuntimeError('Name belongs to another resource; nothing changed')

def ready(container):
    address = container['NetworkSettings']['Networks'][NETWORK]['IPAddress']
    url = 'http://'+address+':8101'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(30):
        try:
            with opener.open(url,timeout=2) as response:
                if response.status == 200 and b'Ledger Bank' in response.read():
                    break
        except OSError:
            time.sleep(0.5)
    else:
        raise RuntimeError('Lab not ready. Inspect: docker logs '+NAME)
    with opener.open('http://'+address+':8100/api/targets',timeout=3) as response:
        targets = json.load(response)['targets']
    if len(targets) != 10 or any(not item['url'].startswith('http://'+address+':') for item in targets):
        raise RuntimeError('Portal target address verification failed')
    if container['HostConfig'].get('PortBindings'):
        raise RuntimeError('Unexpected published ports; inspect the lab container')
    with socket.socket() as sock:
        sock.settimeout(1)
        if sock.connect_ex((address,8199)) == 0:
            raise RuntimeError('Internal service must remain loopback-only')
    print('LAB READY: '+url)
    print('Agent scope: '+address)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['start','status','stop'],nargs='?',default='status')
    command = parser.parse_args().command
    container = inspect('container',NAME)
    if container:
        owned(container,'container')
    if command=='stop':
        if container:
            docker('container','stop','--time','10',NAME)
        print('Managed lab stopped; source files preserved')
        return
    if command=='start':
        if not (SOURCE/'server.py').is_file():
            raise RuntimeError('Lab source folder missing: '+str(SOURCE))
        network = inspect('network',NETWORK)
        if network:
            owned(network,'network')
            if not network.get('Internal'):
                raise RuntimeError('The managed test network must be internal')
        else:
            docker('network','create','--internal','--label',LABEL+'=1',NETWORK)
        if not container:
            docker('image','inspect',IMAGE)
            docker('container','run','--detach','--rm','--name',NAME,'--network',NETWORK,
                   '--label',LABEL+'=1','--user','1000:1000','--read-only','--cap-drop','ALL',
                   '--security-opt','no-new-privileges','--env','PYTHONDONTWRITEBYTECODE=1',
                   '--mount','type=bind,source='+str(SOURCE)+',target=/lab-original,readonly',
                   '--mount','type=bind,source='+str(HERE)+',target=/lab-adapter,readonly',
                   '--entrypoint','python3',IMAGE,'-u','/lab-adapter/container_lab.py')
        elif not container['State']['Running']:
            docker('container','start',NAME)
        container = inspect('container',NAME)
    if not container or not container['State']['Running']:
        raise RuntimeError('Managed lab is stopped. Run this script with start')
    ready(container)

if __name__=='__main__':
    try:
        main()
    except (RuntimeError,OSError,subprocess.SubprocessError) as error:
        print('ERROR: '+str(error),file=sys.stderr)
        raise SystemExit(1)
