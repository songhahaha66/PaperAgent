# Persistent workspace v2

This is a code/configuration contract, **not a claim that the ASUS server has been configured or tested**. No deployment or remote smoke test was performed for this cutover. Server/SDK dependencies are pinned to OpenSandbox 1.1.1. Linux file locking and `/proc/self/fd` are required by the backend.

## Storage that must be provisioned first

Use one persistent file tree on the sandbox server and make that **same filesystem** visible to the backend. A backend on another machine must have an explicitly provisioned shared filesystem (for example NFS), with compatible ownership, atomic rename, fsync and advisory-lock behavior. An SSH API tunnel does **not** provide filesystem sharing. A host path is resolved on the Docker/OpenSandbox server, never on the API client machine.

The implementation does not create an NFS service, mount remote folders, connect a user's computer, or substitute uploads/downloads when sharing is absent. If shared storage is unavailable, configure it before using new projects. There is no disk quota in this release.

Two trees are shared, with different mount permissions:

| Setting | Backend-visible path example | Sandbox-host path example | Execution container |
|---|---|---|---|
| `PAPERAGENT_WORKSPACE_ROOT` / `PAPERAGENT_SANDBOX_HOST_WORKSPACE_ROOT` | `/srv/paperagent-shared/files` | `/home/operator/paperagent-sandbox/volumes/files` | only the current execution workspace, read/write at `/workspace` |
| `PAPERAGENT_EVIDENCE_ROOT` / `PAPERAGENT_SANDBOX_HOST_EVIDENCE_ROOT` | `/srv/paperagent-shared/evidence` | `/home/operator/paperagent-sandbox/volumes/evidence` | only that workspace's `runs` subtree, **read-only** at `/evidence` |

These examples are mappings to configure after mounting the actual shared filesystem, not directories to create independently on both machines. On a single host the two columns may contain the same paths. Each new sandbox reads a random probe written by the backend and refuses execution when the mounted file tree differs. Evidence uses a separate tree; the backend alone stores ResearchStore, chat, owner/metadata, manifest, checkpoints and durable container leases under `PA_DATA_PATH/project-state`. Never share or mount that private tree into execution containers.

Keep the backend-visible workspace path stable across restarts: its absolute path determines the private state/evidence key. Preserve all three trees and the application database in backups. Container destruction and TTL expiry do not delete project files or evidence.

## Configuration (run only when authorized on the server)

Build the image in this directory as `paperagent-research:workspace-v2`; it includes Python, Pandoc, XeLaTeX and BibTeX. Install `opensandbox-server==1.1.1`. `setup_remote.py` configures the loopback service and server-side allowed host paths; it **does not** establish shared storage. Its private `client.env` contains the API key. Fill the two required backend mount paths before using it; never commit that file.

Backend: `uv sync --extra sandbox`, then select the private env file with `PAPERAGENT_SANDBOX_CONFIG`. Environment variables override the file, including file-storage path settings. Copy the field names from `client.env.example`. Non-loopback API endpoints require HTTPS; the optional SSH tunnel only carries the authenticated API. Do not expose the API publicly or mount Docker sockets/backend environment variables into execution containers.

The backend Docker image uses Python 3.14 and the sandbox SDK. Bind the *already provisioned* backend-visible shared mounts into that container and set their backend-visible paths consistently. `docker-compose.yml` requires those mount sources explicitly; it cannot provision sharing.

## Runtime and recovery contract

- Alembic assigns architecture 1 to pre-existing projects, with no file migration/deletion. New ORM-created projects use architecture 2. Historical project pages expose one download action. Backend guards reject their research, uploads, edits and compilation. Old files stay in `PA_DATA_PATH/workspaces`; export traverses allowed user files and renders readable research/chat records without copying databases, credentials, checkpoints or raw model traces. Create a new project and upload exported material to research again.
- New templates are parsed and copied during project creation after permission validation. Template metadata updates/deletion cannot change an existing project's files, change server paths, or delete referencing projects. Parser failures roll back creation; source hash, format and parser version identify cached analysis. Compressed documents have member-count, expanded-size, ratio, link and traversal checks before parsing.
- Python cells access persistent files directly. There is no per-cell file mirror/upload/download path. Each attempt has its own task-selected inputs and temporary products in the same project volume. Project locking serializes active kernel sessions and compilation, and blocks API file changes. Healthy sessions retain Python memory only until the solver releases them, then destroy their containers. There is no idle container cache.
- Code, selected input snapshots, changed outputs, logs, hashes, runtime image and real completion status are retained outside writable mounts. A sealed receipt is required before results become registerable. Repeated sealing is idempotent; altered receipts/files fail verification. The existing scientific/semantic review and research acceptance logic remain separate from file integrity. A result binding records the existing result ID and run ID in the private manifest.
- Timeout/cancel/transport loss never replay code or invoke a backend Python/Pandoc/TeX fallback. Unknown execution remains a failure. A persistent container lease prevents new writes/execution after a backend crash or unconfirmed destroy until the server TTL plus startup allowance expires. This wait is conservative and visible. A new process reconstructs files, ResearchStore and checkpoints; it does not reconstruct Python memory or unsaved figures. Completed durable run receipts can be reused; unfinished tools are reported as interrupted.
- Compiler commands also run inside scoped sandboxes, with timeouts, network deny and exit-code receipts. LaTeX uses `-no-shell-escape`; failed builds retain the last published PDF. Logs live in `.build` and are read-only through the public file gateway. The backend does not require local Pandoc/TeX installations.

## Checks and user acceptance

Local automated checks must not contact a real model, sandbox or production database. The focused contract suite is:

```sh
cd backend
# Set DATABASE_URL to a temporary local SQLite file and SECRET_KEY to a test-only value.
uv run --extra sandbox python -m pytest tests/test_workspace_contract.py tests/test_workspace_routes.py tests/test_template_contract.py
cd ../frontend
npm ci
npm run test:workspace
npm run build
```

The historical transfer-provider tests describe the removed mirror protocol and are not evidence for v2. Do not run the existing `smoke_remote_sandbox.py` / `smoke_work_sandbox.py` as v2 acceptance; they are blocked with a migration explanation to prevent accidental paid/remote work.

User-run acceptance on the actual server (not run by the implementer):

1. Verify both shared trees map to the **same bytes** from each machine, the v2 image has Pandoc/XeLaTeX/BibTeX/fonts, server allowed paths cover only the two shared trees, and the backend private tree is absent from container mounts.
2. Create one project, upload source data and a template, then run research/compile. Inspect actual mounts: current execution directory writable, evidence read-only, other project/backend state inaccessible, network denied and no backend/model keys in env.
3. While executing, try editing/uploading/compiling from another tab/process; verify serialization/rejection. Check stdout/stderr, timeout, cancel and true compiler failures. Verify `/evidence` cannot be modified, and that an altered mutable result does not change archived hashes.
4. Stop the backend/container during a step. Check the durable lease/TTL behavior, retained files and real completed receipts. Restart/resume without replaying an uncertain cell or claiming memory restoration. Test an unsaved-figure interruption explicitly.
5. Export a historical project; inspect material completeness and absence of databases/checkpoints/credentials. Verify all mutation/research routes are denied. Create a new project from exported material without old execution state.
6. Delete/update a template library entry and verify another user's project files survive. Try path traversal, nested symlinks and oversized compressed documents. Back up before the Alembic cutover; rollback requires restoring the complete backup, not reverse-migrating new projects.
