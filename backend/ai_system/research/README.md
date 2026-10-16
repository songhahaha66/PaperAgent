# Versioned research control

`PaperGraphAgent` uses one engine, `research-v2`, with research snapshot schema 2.
Older protocol records cannot be resumed or revised; start a new research run.
Existing files remain available for read-only inspection. There is no migration
or legacy execution path.

## State and execution

`.system/research.sqlite` contains source-anchored requirements, versioned scoped
assumptions, task revisions, attempts, immutable evidence, claims, validation
records, typed issues, section versions, command receipts and an
append-only commit journal. JSON files and graph checkpoints are derived views;
recovery reads committed research state before scheduling more work. Reference
numbers are unique within a research, and a changed value gets a new reference.

The observation node validates the dependency DAG, refreshes input hashes and
schedules dependency-ready work. Every claim creates a new attempt with frozen
input hashes, predecessor attempt IDs, assumption revisions and a fixed acceptance
protocol hash. A worker runs the existing design,
exploration and CodeAct capabilities in `.system/attempts/<id>/workspace` using
copied data and predecessor handoffs. This changes the kernel's working directory;
it is not an OS security sandbox for arbitrary Python code.

A SQLite transaction checks the task revision, actual inputs, protocol and lease before
publishing evidence. A change to an unrelated task does not reject the result.
Repeated identical submissions return the original receipt; stale attempts remain
in history but cannot become active. Failed execution and failed acceptance are
separate. Only accepted current results reach the writing registry.

Requirements preserve exact user source quotes and task coverage. Hard constraints
must specify executable checks: existence, finite numbers, inequalities, equality
or absolute tolerance, optionally comparing an accepted dependency's result.
Percentage values use ratios. Each compute task must map requirements to actual
own/dependency evidence. The store evaluates checks again at commit; a worker's
accepted flag cannot override failure or a missing/ambiguous metric.

All findings use `IssueReport` / `Issue`, with evidence, task/assumption revisions,
check IDs, repair targets and lifecycle status. Deterministic acceptance failures
are confirmed; solver, consistency and final-section semantic findings start as
suspicions. The controller creates an independent CodeAct investigation against
the frozen checks. A reproduced failure confirms the issue and invalidates its
scope and descendants. Passing checks dismiss the suspicion; investigation evidence
stays in history rather than the paper registry. Missing checks or metrics remain
inconclusive and block delivery. Failed investigations can retry on explicit resume.

Confirmed failures can trigger at most two Planner calls. The Planner may revise
causal upstream objectives/dependencies, add work bound to existing requirements,
or version a refuted assumption (including explicit retirement). It cannot remove
original tasks or weaken requirement checks. Refuted assumptions block consumers
until revised/retired. A repair issue resolves only after affected tasks produce
fresh accepted results; unrelated accepted work is preserved. Infrastructure and
audit failures stop for explicit retries. Attempts have per-task-revision and
total-run budgets, including after recovery or replanning.

## Commands and delivery

`ask` is read-only. Existing `revise` still edits a plan awaiting confirmation.
The new `revise_research` action requires a `revision` object containing
`command_id`, `research_id`, `base_revision` and optional task `scope`. The message
is the requested change. The UI exposes this action and task-specific revision
buttons after the active request finishes. Running requests keep the existing
pause/resume lifecycle; live command injection is not introduced in this version.

An incremental revision preserves the original requirements and parses the user's
amendment into additional source-anchored requirements before augmenting selected
task objectives. Idempotent retries reuse the frozen command patch. It invalidates dependent tasks and their sections, preserves
unrelated accepted work, then regenerates affected sections and global summaries.
Data-file dependencies are deliberately conservative: every worker records all
raw data and attachments. Assumption dependencies are explicit and scoped;
fine-grained dataset dependencies are not implemented.

Generated paper versions are retained. If `paper.md` changed manually since its
last publication, the human edit is preserved and the generated revision is saved
separately for merging; exporting the conflicting active paper is skipped.

One work coordinator owns a research run, as enforced by the existing TaskManager.
Business commands and attempt claims are additionally serialized by SQLite.
Cross-process coordinator failover/heartbeat leases, Jev decision calibration,
cross-research memory and policy evolution are
outside this implementation. Existing Jev scoring and validation remain reusable.

## Validation

`test_research_control.py` covers idempotence, late submissions, dependency changes,
concurrent claims, acceptance, budgets, data corrections and manual edits.
`test_research_engine.py` exercises real checkpoints and Python kernels for
selective recomputation, restart and rejection of unsupported checkpoints using
offline model replies. Frontend tests cover revision transport and explicit progress statuses.

`test_research_p2.py` covers immutable source/check contracts, numeric and cross-task
acceptance, missing metrics, false suspicions, verification recovery, evidence
scope and causal repairs. Real-kernel graph fixtures reproduce an incorrect mean
estimator, independently discover a counterexample before or after writing, change
the upstream plan/assumption, recompute its consumer and deliver with new evidence
while retaining an unrelated task. These are deterministic offline integration
tests, not live-model research-quality benchmarks.

The evaluation CLI defaults to `--pipeline research`. Its `research-no-explore`,
`research-no-review`, `research-no-consistency` and `research-no-planner` variants
all use the same versioned engine.
No paid model benchmark is required for the offline regression suite; quality
or cost improvements must be established separately on held-out research tasks.
