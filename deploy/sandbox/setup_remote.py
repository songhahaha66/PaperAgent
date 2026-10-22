"""Run on the execution host after `uv tool install opensandbox-server==1.1.1`.

Creates a dedicated loopback service and a mode-0600 client env file. Prints no secrets.
"""
import os
import secrets
import subprocess
from pathlib import Path

root = Path.home() / "paperagent-sandbox"
root.mkdir(mode=0o700, exist_ok=True)
key_file = root / "api-key"
if not key_file.exists():
    key_file.write_text(secrets.token_urlsafe(40))
key_file.chmod(0o600)
key = key_file.read_text().strip()
(root / "volumes" / "files").mkdir(parents=True, exist_ok=True)
(root / "volumes" / "evidence").mkdir(parents=True, exist_ok=True)
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
allowed_host_paths = ["{root / 'volumes' / 'files'}", "{root / 'volumes' / 'evidence'}"]
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
PAPERAGENT_SANDBOX_HOST_WORKSPACE_ROOT={root / 'volumes' / 'files'}
PAPERAGENT_SANDBOX_HOST_EVIDENCE_ROOT={root / 'volumes' / 'evidence'}
# REQUIRED: paths to these same filesystems on the backend machine/container.
PAPERAGENT_WORKSPACE_ROOT=
PAPERAGENT_EVIDENCE_ROOT=
''')
client.chmod(0o600)
unit = Path.home() / ".config/systemd/user/paperagent-sandbox.service"
unit.parent.mkdir(parents=True, exist_ok=True)
unit.write_text(f'''[Unit]
Description=PaperAgent OpenSandbox execution service
After=network-online.target
[Service]
ExecStart={Path.home()}/.local/bin/opensandbox-server --config {config}
Restart=on-failure
RestartSec=5
UMask=0077
[Install]
WantedBy=default.target
''')
subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
subprocess.run(["systemctl", "--user", "enable", "--now", "paperagent-sandbox"], check=True)
print("Service configured on 127.0.0.1:18080. Client settings:", client)
