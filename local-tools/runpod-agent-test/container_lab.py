"""Adapt only lab listener addresses inside a private Docker network."""
import ipaddress
from pathlib import Path
import socket

address = socket.gethostbyname(socket.gethostname())
ip = ipaddress.ip_address(address)
if not ip.is_private or ip.is_loopback:
    raise RuntimeError('A private container address is required')
path = Path('/lab-original/server.py')
source = path.read_text(encoding='utf-8')
changes = {
    "server = Server(('127.0.0.1', port), Handler)":
        "server = Server(('127.0.0.1' if isinstance(lab, Internal) else '0.0.0.0', port), Handler)",
    "allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}":
        "allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}', f'{_LAB_CONTAINER_IP}:{self.server.server_port}'}",
}
for before,after in changes.items():
    if source.count(before) != 1:
        raise RuntimeError('Lab source changed; inspect before adapting')
    source = source.replace(before,after,1)
before = "f'http://127.0.0.1:{self.base_port + i}'"
if source.count(before) != 2:
    raise RuntimeError('Lab portal source changed; inspect before adapting')
source = source.replace(before,"f'http://{_LAB_CONTAINER_IP}:{self.base_port + i}'")
print('Docker lab target: http://'+address+':8101',flush=True)
exec(compile(source,str(path),'exec'),{
    '__name__':'__main__','__file__':str(path),'_LAB_CONTAINER_IP':address,
})
