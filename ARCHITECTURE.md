# PaperAgent architecture

`Project.architecture_version` defines the workspace contract independently of the research engine version. Existing rows are version 1 and export-only. New projects are version 2. No project files or execution state are automatically converted.

## Storage and trust boundaries

- `config.paths.get_workspace_path(project_id)` selects one persistent public project tree. The backend must explicitly access the same filesystem as the sandbox host; API tunnelling alone is insufficient. The configured backend-visible path is stable identity, not a client input.
- `services/file_services/workspace_layout.py` resolves internal paths. `.system` and manifest paths go to backend-only `PA_DATA_PATH/project-state/<key>`; `runs` paths go to the separately shared evidence tree. WorkspaceFS owns atomic manifests, run identities and private/public path translation. Corrupt manifests fail closed.
- `WorkspaceFileService` is the public file gateway. Routes centrally check project ownership/version. It validates project-relative paths, blocks traversal/symlinks/private paths, uses atomic replacement, pins download file descriptors, and exposes only allowlisted run diagnostics. Export never copies private databases, checkpoints or credentials.
- `research/store.py` owns schema 5 business state, chats and durable operation records. LangGraph owns scheduling checkpoints; neither is mounted into execution containers. Result/figure registration requires a successful sealed run receipt and retains the existing run/result identities. Integrity is separate from scientific review and acceptance.

## Execution and compilation

`KernelPool -> sandbox.factory -> RemoteKernel -> OpenSandboxSession` is the application execution path. The factory refuses local execution. The old local kernel module remains an explicit isolated test utility, not an application provider.

OpenSandbox 1.1.1 mounts only the current execution tree at `/workspace` and that tree's run archive read-only at `/evidence`. Random probes verify both backend/server path mappings. No whole-project file synchronization or in-memory container cache controls file survival. Backend environment variables/model credentials are never forwarded.

Experiments and paper-comment calculations use scoped persistent directories under the project's `experiments/` tree. Task-selected inputs and dependency handoffs are copied there; run input snapshots, code, changed products, logs and hashes are archived outside writable mounts. Temporary products remain available independently of the container.

A per-project OS lock covers a kernel session through release and all compiler operations. Public file mutations fail while busy. Container leases are persisted before creation; crashes and unconfirmed cleanup conservatively block writes until TTL expiry. Recovery uses files, ResearchStore and confirmed receipts, not Python memory. Uncertain computations and unsaved figures are never reported as completed or silently replayed.

`ai_system/export` dispatches Pandoc, XeLaTeX and BibTeX through the same scoped sandbox boundary. Failed compilation preserves prior delivered files. The backend has no compiler fallback.

## Templates and delivery

Template permission and format checks precede project initialization. A transaction creates the project only after parsing and copying the template. Source hash, format and parser version identify analysis caches; parsing failures are not ready. Existing projects own their copies. Template metadata cannot choose server file paths; deleting a library entry detaches references without deleting projects.

The Vue workspace preserves its existing panes/tabs. Historical project views load metadata only and show the export action. Both client gates and server gates apply.

See [deployment and user acceptance](deploy/sandbox/README.md) for the explicit shared-storage mappings and checks that must be run on the actual server. No remote acceptance or deployment is implied by static checks.
