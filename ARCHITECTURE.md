# PaperAgent architecture

`Project.architecture_version` defines the workspace contract independently of the research engine version. Existing rows are version 1 and export-only. New projects are version 2. No project files or execution state are automatically converted.

## Storage and trust boundaries

- `config.paths.get_workspace_path(project_id)` selects one persistent public project tree. User files are authoritative on the backend local filesystem. The sandbox server has a separate filesystem and is accessed only through the OpenSandbox API; no NFS or shared mount is required.
- `services/file_services/workspace_layout.py` resolves internal paths. `.system` and manifest paths go to backend-only `PA_DATA_PATH/project-state/<key>`; `runs` paths go to the backend-local evidence tree. WorkspaceFS owns atomic manifests, run identities and private/public path translation. Corrupt manifests fail closed.
- `WorkspaceFileService` is the public file gateway. Routes centrally check project ownership/version. It validates project-relative paths, blocks traversal/symlinks/private paths, uses atomic replacement, pins download file descriptors, and exposes only allowlisted run diagnostics. Export never copies private databases, checkpoints or credentials.
- `research/store.py` owns schema 5 business state, chats and durable operation records. LangGraph owns scheduling checkpoints; neither is mounted into execution containers. Result/figure registration requires a successful sealed run receipt and retains the existing run/result identities. Integrity is separate from scientific review and acceptance.

## Execution and compilation

`KernelPool -> sandbox.factory -> RemoteKernel -> OpenSandboxSession` is the application execution path. The factory refuses local execution. The old local kernel module remains an explicit isolated test utility, not an application provider.

OpenSandbox 1.1.1 mounts a server-local, scope-keyed persistent execution cache at `/workspace`. It never mounts backend evidence or business state. `sandbox/transfer.py` moves explicitly versioned inputs and changed outputs through the file API, using actual cache hashes and frozen per-run output manifests. Continuous cells retain remote memory/files without whole-workspace transfer. Backend environment variables/model credentials are never forwarded.

Experiments and paper-comment calculations use scoped persistent directories under the project's `experiments/` tree. Task-selected backend inputs and dependency handoffs are sent to the scoped remote cache. Necessary changed outputs are downloaded, verified and atomically archived in backend-only evidence before registration. Persisted completion/inventory records allow delivery retry without replaying code. A replacement container can reuse confirmed cache files, but does not restore interpreter memory.

A KernelPool owns the per-project OS lock through the lifetime of its retained kernels; their actual computations are serialized with bounded lock waits. Candidate containers retain separate Python memory and identical individual CPU/memory limits. Only a live winning kernel can be promoted for estimator handoff; lost memory never triggers an implicit fit. Observation publication uses frozen predictions without fitting. Every live or unconfirmed container has its own entry in the durable project lease ledger. The same project lock covers all compiler operations. Public file mutations fail while busy. Container leases are persisted before creation; crashes and unconfirmed cleanup conservatively block writes until TTL expiry. Recovery uses files, ResearchStore and confirmed receipts, not Python memory. Uncertain computations and unsaved figures are never reported as completed or silently replayed.

`ai_system/export` uploads source snapshots/dependencies and dispatches Pandoc, XeLaTeX and BibTeX through the same API transfer boundary; outputs/logs return by API. Default Word references are downloaded for styling and uploaded again. Stable command receipts and frozen output manifests support retrying delivery without recompilation. Failed compilation preserves prior delivered files. The backend has no compiler fallback. Source discovery excludes internal build/experiment trees. Final source-version validation and PDF replacement share a short edit-lock critical section after the compiler releases its execution lock. Hashing, input snapshots and artifact copies enforce the configured single-file limit during streaming reads, independently of log limits; there is no aggregate project quota.

## Templates and delivery

Template permission and format checks precede project initialization. A transaction creates the project only after parsing and copying the template. Source hash, format and parser version identify analysis caches; parsing failures are not ready. Existing projects own their copies. Template metadata cannot choose server file paths; deleting a library entry detaches references without deleting projects.

The Vue workspace preserves its existing panes/tabs. Historical project views load metadata only and show the export action. Both client gates and server gates apply.

See [deployment and user acceptance](deploy/sandbox/README.md) for server-local cache configuration and checks that must be run on the actual server. No remote acceptance or deployment is implied by static checks.

Explicit project deletion currently removes its public file tree and database project row; the separately stored private state and evidence remain retained. This change does not automatically clean up those retained trees.

Formal presentation files under `outputs/research` are API-read-only. Preview, export and compiler snapshots resolve verified backend archive bytes, so editing a working presentation copy cannot silently change formal evidence. No automatic merge or remote overwrite of sealed evidence is allowed.
