# Remote sandbox execution

PaperAgent's `KernelPool` uses `sandbox.factory.create_kernel`. The `local` backend
keeps the existing development subprocess. `opensandbox` uses an authenticated
remote service; it never falls back to local execution on failure.

`contracts.SandboxSession` is the extension point for another platform: implement
start/upload/delete/execute/files/download/interrupt/close and add the provider to
the factory. `RemoteKernel` owns run IDs, logs, transfer limits, immutable artifact
copies and terminal statuses. SDK classes never enter research tools or business state.
`ExecutionKernel` is the shared interface used by research code.

## Execution host (ASUS)

Requires Linux, Docker and UV. The current tested combination uses server/SDK 1.1.1,
code-interpreter/execd v1.1.0, and egress v1.1.7. The research image supplies Python
3.11 numerical packages and Chinese fonts; the PaperAgent API can use Python 3.14.
No model credentials or backend source are mounted inside execution containers.

```sh
uv tool install opensandbox-server==1.1.1
mkdir -p ~/paperagent-sandbox
# Copy Dockerfile and .dockerignore from this directory into that directory.
docker build -t paperagent-research:opensandbox-1.1.0 ~/paperagent-sandbox
python3 setup_remote.py
```

`setup_remote.py` creates a dedicated user systemd service on **127.0.0.1:18080**,
an API key and a mode-0600 `~/paperagent-sandbox/client.env`. It does not modify
other services. Docker sandbox ports also bind loopback; outbound network policy
uses `dns+nft` plus `defaultAction=deny`. The service can manage Docker and must
remain a trusted private service. This deployment uses Docker isolation, not a VM.

```sh
systemctl --user status paperagent-sandbox
journalctl --user -u paperagent-sandbox -n 50
systemctl --user restart paperagent-sandbox  # after editing server.toml
loginctl show-user "$USER" -p Linger       # yes keeps the service after logout
```

## PaperAgent host (Mac / another server)

Only install the client extra, **not** OpenSandbox Server or Docker:

```sh
cd backend
uv sync --extra sandbox
# Copy ASUS's paperagent-sandbox/client.env to .sandbox.env; chmod 600 .sandbox.env.
```

From the repository root, keep the tunnel running:

```sh
sh deploy/sandbox/connect.sh ASUS-remote
```

The SSH alias can use an Aliyun jump host. No public sandbox listener is required.
The SDK's `use_server_proxy=True` routes both lifecycle and execution/file calls
through this single port; forwarding just the API without that setting is insufficient.

Start the backend with an explicit configuration path (or put this pointer in its
existing private `.env`):

```sh
cd backend
PAPERAGENT_SANDBOX_CONFIG=.sandbox.env uv run --extra sandbox main.py
```

Environment variables override the selected file. `PAPERAGENT_SANDBOX_PROVIDER=local`
explicitly selects local development execution. Non-loopback endpoints require HTTPS.
The example env file lists endpoint, image, CPU/memory and lifetime settings.

## Verification

```sh
cd backend
PAPERAGENT_SANDBOX_PROVIDER=local uv run --extra sandbox python -m pytest tests/test_sandbox_providers.py tests/test_kernel.py
PAPERAGENT_SANDBOX_CONFIG=.sandbox.env uv run --extra sandbox python tests/smoke_remote_sandbox.py
```

The explicit integration check creates a real remote sandbox, checks stateful Python,
streaming stdout/stderr/native/subprocess output and downloads a CSV and PNG before
destroying the sandbox. Unit tests use a fake provider and never call a remote service.

## Data and lifecycle contract

- Only data/, attachment/, code/, outputs/ and inherited run artifacts are uploaded.
  Hidden backend state, manifests and credentials remain local. Local file changes
  are synchronized before each cell. Files are transferred, not host-mounted.
- Output logs are appended locally before UI updates. UI previews keep the last
  6,000 characters; model observations keep a bounded excerpt. Full received logs
  are saved up to the configured quota (default 8 MiB, then execution is stopped).
- Changed output files are verified against size/SHA-256 and downloaded atomically.
  Each run keeps an immutable copy. Default transfer limits: 64 MiB/file,
  256 MiB per synchronization, 1,000 files. These are **transfer limits**, not remote
  disk quotas; host disk needs operational monitoring and cleanup.
- Files are published under outputs/research/<attempt>/<run>/ even when later model
  interpretation fails. Availability does not mean scientific validation. A source
  run's success/error status remains in the run manifest.
- Timeout/cancel interrupts execution, attempts a bounded partial file retrieval,
  then destroys the sandbox. Lost connections never cause automatic code replay or
  local fallback. When deletion cannot be confirmed, the server TTL is the backstop.
  Outputs not yet downloaded can be lost on remote termination or TTL expiry.
- Containers have a fixed lifetime (default 30 minutes); interpreter memory is not
  restored after restart. Saved local artifacts can be used by a new attempt.
- Platform-wide admission control and stronger VM isolation are not implemented
  by this adapter. Set appropriate concurrency outside the SDK when increasing load.

The tunnel script reconnects SSH after a network interruption; it never replays code.
Interrupted executions are reported as failures. Explicitly resume the study after
connectivity returns. The script must remain running while using the remote backend.

## Validation scope

The ASUS integration check covers stateful execution, live output, CSV/PNG retrieval,
input synchronization, network denial, timeout/cancel cleanup and local persistence.
The browser research trial also confirmed live log previews and readable CSV/PNG
files. It exposed fixes for Debian's Noto CJK font names, nested figure artifact paths
and published figure paths inherited by continued attempts; regression tests cover
these cases. That trial exhausted its research attempt budget before final report
delivery. A complete model-driven report run after the fixes has **not** been verified;
passing the sandbox checks does not establish end-to-end research completion.
