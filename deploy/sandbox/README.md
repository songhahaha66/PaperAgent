# API-backed persistent execution cache

The backend and sandbox server have **independent filesystems**. No NFS, shared mount, or backend-visible sandbox directory is required. An SSH tunnel can carry the OpenSandbox API alone. This change has not been deployed or accepted on the ASUS server.

## Shared adapter entry point

Research and document compilation both call `ai_system.sandbox.factory.create_session`. OpenSandbox is the only production adapter today. Its official SDK handles HTTP and streamed execution; business code does not select a wire protocol. SSH/VPN/HTTPS routing remains deployment configuration.

To add a provider, implement `SandboxSession` in `backend/ai_system/sandbox/contracts.py` and call `register_provider(name, constructor)` during trusted backend startup, before requests are served. Constructors receive settings, the backend workspace identity, optional work/experiment IDs and an optional compiler cache key. Select the registered name with `PAPERAGENT_SANDBOX_PROVIDER`. Unknown names fail; there is no automatic fallback or dynamic import from configuration.

The contract includes stateful Python execution, literal argument lists and stdin for compiler execution, checksummed file transfer, frozen-output recovery, repeatable output finalization and remote destruction on `close`. An adapter must preserve these semantics, or reject unsupported operations explicitly. `close` does not mean merely disconnecting a client. The OpenSandbox-only host cache field and existing env names remain compatible; another adapter need not use a host bind mount or HTTP endpoint. Its authentication and endpoint validation belong inside that adapter.

New receipts identify the provider. Pending research delivery and compiler receipt reuse reject a provider mismatch; legacy compiler receipts belong to OpenSandbox. The current guard distinguishes provider types, not multiple deployments of the same provider. Stable deployment handles, a separate runtime manager, attach/pause capabilities and new providers are follow-up work described in [DESIGN.md](DESIGN.md).

## Authority and paths

The backend keeps authoritative user data under `PA_DATA_PATH/project-files`, immutable run evidence under `PA_DATA_PATH/evidence`, and ResearchStore/chat/checkpoints/manifests/leases under `PA_DATA_PATH/project-state`. Existing historical files remain under `PA_DATA_PATH/workspaces` and are export-only. Back up the backend data directory and application database. No automatic migration, private-evidence cleanup or disk quota is introduced.

The sandbox server separately owns `PAPERAGENT_SANDBOX_HOST_WORKSPACE_ROOT`, for example `/home/operator/paperagent-sandbox/volumes/execution-cache`. OpenSandbox mounts only `<that server-local root>/<opaque cache key>` into an execution container at `/workspace`. The backend never opens this host path. Containers receive no backend state/evidence directory or model/API credentials. Server-side cache files are untrusted execution copies, not formal evidence.

The pinned [OpenSandbox 1.1.1 Docker volume implementation](https://github.com/opensandbox-group/OpenSandbox/blob/release-1.1.1/server/opensandbox_server/services/docker/volumes.py) creates allowed host-cache subdirectories server-side. The server account still needs permission to create those directories. This is a source-code contract, not a claim about the installed server.

`setup_remote.py` configures the loopback OpenSandbox 1.1.1 service and allowed host paths on the execution server only. Do not run it without deployment authorization. It does not configure NFS, networking on another machine, or a backend data mount. Copy the field names from `client.env.example`; actual secrets belong in a private env file. Backend `docker-compose.yml` only persists its local `pa_data` and mounts private configuration. It has no mandatory shared-storage bind mounts.

Build the execution image as `paperagent-research:workspace-v2`; its Dockerfile includes numerical Python, Pandoc, XeLaTeX, BibTeX and CJK fonts. The backend uses Python 3.14 and `uv sync --extra sandbox`. Select configuration through `PAPERAGENT_SANDBOX_CONFIG`, with environment variables overriding that file. Non-loopback endpoints require HTTPS; the API stays private. Do not mount Docker sockets or backend environment files in execution containers.

## One-direction transfer and execution

- Task-selected local inputs are hashed and sent through OpenSandbox's file API only when the remote cache lacks that version. Temporary uploads are checksum-verified and atomically installed. Cache metadata contains paths/hashes only. Recreated containers verify actual cached bytes before claiming an input hit; they do not restore Python memory.
- A live kernel preserves its interpreter and remote files across cells. Backend-created or explicitly changed inputs are sent incrementally; unchanged local snapshots do not overwrite live remote computation state. Live input mutations are archived as actual run inputs without overwriting backend user files; unchanged inputs use the local frozen copy. No bidirectional merge occurs. Experiments use scoped cache identities; candidate containers keep separate memory and identical individual resource settings.
- Changed public outputs are frozen server-side into a run-specific snapshot and manifest. The backend persists the real completion receipt and that inventory before downloading. Downloads are bounded, size/hash-checked and committed atomically into backend evidence before registration. Temporary/cache directories, backend databases and checkpoints are excluded.
- If delivery fails after computation, `retry_outputs` retrieves the frozen original bytes, including after container replacement; it never executes the submitted cell again. A missing frozen inventory, lost unsaved figures, changed local destination or an unconfirmed completion remains an explicit gap. A pending run blocks new cells in that execution. The adapter does not invent a successful retry from mutable remote scratch files.
- Successfully delivered outputs remain available from the backend when the sandbox is offline. Formal `outputs/research` presentations are API-read-only; preview/export/compile resolve their verified backend archive bytes rather than trusting an edited presentation copy. Working intermediates remain ordinary workspace files; delivery refuses to overwrite manual changes made while a run was pending.
- Pool-scoped OS locks serialize a project's computations and compilation while retaining winner memory. Each container has a separate durable lease. Unknown cleanup/crashes conservatively block new modifications until the server TTL expires. Closing a container leaves the execution cache and backend files intact. Lost winner memory never triggers an implicit re-fit; completed predictions can still be published without training again.

## Compilation across the same API

Only the source snapshot and actual dependencies are uploaded to a separate compiler cache identity. Backend code never scans a remote directory via `Path`. Pandoc/XeLaTeX/BibTeX run in the sandbox with network deny, bounded logs/timeouts and true exit-code receipts. Generated files and logs return through the file API. There is no backend compiler fallback.

Default Word references make a complete round trip: remote Pandoc creates a reference, the backend downloads and styles it, and that exact styled version is uploaded for conversion. A stable private reference copy prevents timestamps from invalidating a pending conversion delivery. Uploaded template references likewise cross the API.

Compiler command receipts bind source/input hashes, arguments and pass ordinal. Completed passes with delivery gaps can retrieve their frozen output without recompiling. Missing completion receipts remain unknown and are not silently replayed; revise the source/runtime configuration after checking such a failure. A successful compile cache hit still downloads and verifies original frozen outputs, not mutable cache files. `.build` and execution trees never join the source version. Final source validation and PDF publication share the existing short edit-lock critical section.

## Checks and user acceptance

Local automated checks must not contact a real model or sandbox server. The transfer mocks use a physically separate server tree; only fake file-API upload/download methods cross the boundary. They cover cache hits/changes, incremental output, immutable delivery retries, replacement containers, path isolation, compiler input/output, Word references, candidate lifecycle and publication locking. Explicit offline compiler fixtures may execute trusted local compiler inputs on a separate temporary tree; these do not enable an application fallback.

```sh
cd backend
# Use a temporary local SQLite DATABASE_URL and a test-only SECRET_KEY.
uv run --extra sandbox python -m pytest tests/test_sandbox_factory.py tests/test_api_workspace_transfer.py tests/test_workspace_runtime_regressions.py tests/test_workspace_contract.py
cd ../frontend
npm run test:workspace
npm run build
```

The test suite uses the current transfer and evidence protocols. Passing offline tests is not a claim that v2 has passed remote acceptance. Existing smoke scripts are guarded against accidentally contacting an old server.

User-run acceptance on the actual server, not run by the implementer:

1. Confirm server/SDK 1.1.1, allowed host paths and a writable server-local cache directory; verify the image's Python/Pandoc/XeLaTeX/BibTeX/fonts. No shared filesystem is needed. Inspect that only the current cache is mounted, network is denied, and backend credentials/state are absent.
2. Create a new project, upload data/template, run Python cells and compile. Check input-cache hits and changed input versions, live variables, output/log transfer, timeout/cancel and true failures. Verify old projects remain export-only and existing template copies survive library updates/deletion.
3. Break API connectivity after a computation completes. Retry delivery without another computation; destroy/recreate the container and verify persistent files and frozen output manifests. Lost memory/unsaved figures must remain explicit, not false success.
4. Repeat compilation with both an uploaded Word template and a default reference. Verify a failed output download is retried from its completed receipt, source edits during compilation preserve the old PDF, and build dependencies do not change the source version.
5. Stop the sandbox service and verify already delivered backend files still preview/download/export. Alter a formal presentation copy and confirm the archive remains authoritative. Try unsafe paths/symlinks and oversized single files; no project quota is enabled.
6. Preserve backend evidence and server caches during testing. Explicit project deletion currently retains separately stored private evidence/state; cleanup policy has not been chosen and this change does not remove those retained records.
