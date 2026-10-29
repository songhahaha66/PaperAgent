"""Run on the execution host with opensandbox-server==1.1.1 on PATH.

Root installs a system service; other users install a user service.
Creates a dedicated loopback service and a mode-0600 client env file. Prints no secrets.
"""
import os
import secrets
import shutil
import subprocess
from pathlib import Path

executable = shutil.which('opensandbox-server')
if not executable:
    raise SystemExit('Install opensandbox-server==1.1.1 and put its executable on PATH first')
system_service = os.geteuid() == 0

root = Path.home() / "paperagent-sandbox"
root.mkdir(mode=0o700, exist_ok=True)
key_file = root / "api-key"
if not key_file.exists():
    key_file.write_text(secrets.token_urlsafe(40))
key_file.chmod(0o600)
key = key_file.read_text().strip()
(root / "volumes" / "execution-cache").mkdir(parents=True, exist_ok=True)
registry = "sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox"
config = root / "server.toml"
config.write_text(f'''[server]
host = "127.0.0.1"
port = 18080
api_key = "{key}"
max_sandbox_timeout_seconds = 3600
[runtime]
type = "docker"
execd_image = "{registry}/execd:v1.1.0"
[docker]
network_mode = "bridge"
publish_host = "127.0.0.1"
port_range_min = 41000
port_range_max = 41100
pids_limit = 256
no_new_privileges = true
[storage]
allowed_host_paths = ["{root / 'volumes' / 'execution-cache'}"]
[store]
type = "sqlite"
path = "{root / 'state.db'}"
[egress]
image = "{registry}/egress:v1.1.7"
mode = "dns+nft"
''')
config.chmod(0o600)
client = root / "client.env"
client.write_text(f'''PAPERAGENT_SANDBOX_PROVIDER=opensandbox
PAPERAGENT_SANDBOX_ENDPOINT=http://127.0.0.1:18080
PAPERAGENT_SANDBOX_API_KEY={key}
PAPERAGENT_SANDBOX_IMAGE=paperagent-research:workspace-v2
PAPERAGENT_SANDBOX_CPU=1
PAPERAGENT_SANDBOX_MEMORY=768Mi
# This directory exists on the sandbox server only.
PAPERAGENT_SANDBOX_HOST_WORKSPACE_ROOT={root / 'volumes' / 'execution-cache'}
''')
client.chmod(0o600)
unit = (Path('/etc/systemd/system/paperagent-sandbox.service') if system_service
        else Path.home() / '.config/systemd/user/paperagent-sandbox.service')
unit.parent.mkdir(parents=True, exist_ok=True)
unit.write_text(f'''[Unit]
Description=PaperAgent OpenSandbox execution service
After=network-online.target{' docker.service' if system_service else ''}
Wants=network-online.target
[Service]
ExecStart={executable} --config {config}
Restart=on-failure
RestartSec=5
UMask=0077
[Install]
WantedBy={'multi-user.target' if system_service else 'default.target'}
''')
control = ['systemctl'] if system_service else ['systemctl', '--user']
subprocess.run([*control, 'daemon-reload'], check=True)
subprocess.run([*control, 'enable', '--now', 'paperagent-sandbox'], check=True)
print("Service configured on 127.0.0.1:18080. Client settings:", client)
