# Versioned research control

`PaperGraphAgent` uses one engine, `research-v1`. Unversioned research records
cannot be resumed or revised; start a new research run. Existing files remain
available for read-only inspection.

## State and execution

`.system/research.sqlite` contains the original requirement, task revisions,
attempts, immutable evidence, claims, section versions, command receipts and an
append-only commit journal. JSON files and graph checkpoints are derived views;
recovery reads committed research state before scheduling more work. Reference
numbers are unique within a research, and a changed value gets a new reference.

The observation node validates the dependency DAG, refreshes input hashes and
schedules dependency-ready work. Every claim creates a new attempt with frozen
input hashes and predecessor attempt IDs. A worker runs the existing design,
exploration and CodeAct capabilities in `.system/attempts/<id>/workspace` using
copied data and predecessor handoffs. This changes the kernel's working directory;
it is not an OS security sandbox for arbitrary Python code.

A SQLite transaction checks the task revision, actual inputs and lease before
publishing evidence. A change to an unrelated task does not reject the result.
Repeated identical submissions return the original receipt; stale attempts remain
in history but cannot become active. Failed execution and failed acceptance are
separate. Only accepted current results reach the writing registry.

Confirmed deterministic validation failures can trigger at most two Planner calls.
The Planner may revise objectives/dependencies or add verification tasks; it cannot
remove original tasks, alter acceptance checks or publish a successful result.
Suspected semantic conflicts retain the existing consistency review. Infrastructure
failures stop for explicit retries. Attempts have per-task-revision and total-run
budgets, including after recovery or replanning.

## Commands and delivery

`ask` is read-only. Existing `revise` still edits a plan awaiting confirmation.
The new `revise_research` action requires a `revision` object containing
`command_id`, `research_id`, `base_revision` and optional task `scope`. The message
is the requested change. The UI exposes this action and task-specific revision
buttons after the active request finishes. Running requests keep the existing
pause/resume lifecycle; live command injection is not introduced in this version.

An incremental revision preserves the original requirement and augments selected
task objectives. It invalidates dependent tasks and their sections, preserves
unrelated accepted work, then regenerates affected sections and global summaries.
Data-file dependencies are deliberately conservative: every worker records all
raw data and attachments. Fine-grained dataset/assumption dependencies can be
added later without changing the publication protocol.

Generated paper versions are retained. If `paper.md` changed manually since its
last publication, the human edit is preserved and the generated revision is saved
separately for merging; exporting the conflicting active paper is skipped.

One work coordinator owns a research run, as enforced by the existing TaskManager.
Business commands and attempt claims are additionally serialized by SQLite.
Cross-process coordinator failover/heartbeat leases, fine-grained assumption
objects, Jev decision calibration, cross-research memory and policy evolution are
outside this implementation. Existing Jev scoring and validation remain reusable.

## Validation

`test_research_control.py` covers idempotence, late submissions, dependency changes,
concurrent claims, acceptance, budgets, data corrections and manual edits.
`test_research_engine.py` exercises real checkpoints and Python kernels for
selective recomputation, restart and rejection of unsupported checkpoints using
offline model replies. Frontend tests cover revision transport and explicit progress statuses.

The evaluation CLI defaults to `--pipeline research`. Its `research-no-explore`,
`research-no-review`, `research-no-consistency` and `research-no-planner` variants
all use the same versioned engine.
No paid model benchmark is required for the offline regression suite; quality
or cost improvements must be established separately on held-out research tasks.
