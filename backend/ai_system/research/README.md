# Versioned research control

`PaperGraphAgent` uses one engine, `research-v3`, with research snapshot schema 2.
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

Workers also receive the complete committed original question, including inline
data. Method selection, candidate design/critique, exploration protocols, CodeAct
and local review all read this same source. A restatement or a subset of requirement
quotes is insufficient to replace source data. Pre-confirmation design reads the
original graph task; resumed workers read the authoritative committed requirement.
Exploration promotion requires successful execution and a grounded comparison
metric, including its existing shuffle check. Self-reported success or a textual
critic score cannot replace that evidence. Without a valid candidate, all probe
kernels are released and formal solving starts with the selected design in a
fresh kernel. Failed variables and transcripts cannot be promoted.

A SQLite transaction checks the task revision, actual inputs, protocol and lease before
publishing evidence. A change to an unrelated task does not reject the result.
Repeated identical submissions return the original receipt; stale attempts remain
in history but cannot become active. Failed execution and failed acceptance are
separate. Only accepted current results reach the writing registry.

Local `ResultReview` returns typed `IssueReport` findings. Each audit records the
exact report hash and cited evidence; local repairs must receive a fresh final
audit. Open findings, missing/stale audits and audit service errors block delivery.
Historical audit evidence survives replacement in the worker registry but cannot
enter active results, claims or numeric acceptance checks. Closed findings retain
their resolution attempt. Resume freezes the old open findings into the next work
order and rechecks them; recovery never silently clears local audit failures.
Only findings naming applicable fixed check IDs can launch an independent
countercheck. Uncheckable findings remain inconclusive rather than being dismissed
by an unrelated passing numeric check.

Selected designs and solve reports declare new key premises as `AssumptionDraft`
(stable task-local key, statement, falsification checks). Only the selected route
is registered. The coordinator assigns task-scoped premise/check IDs, source attempt, version 1
and dependent scope atomically. Designing stops before formal solving when its
premises are new; premises discovered during solving make that attempt provisional.
Both paths resume with a new frozen work order; provisional results cannot be
published. Repeated declarations are idempotent, while silent replacement or
revival is rejected. Later consumers inherit premise versions through their
dependencies. Refutation and explicit plan revisions use the same version/history
and selective invalidation mechanisms as initial assumptions. Writing reads only
the registered current premises, including their design/solve provenance.

Requirements preserve exact user source quotes and task coverage. Hard constraints
must specify executable checks: existence, finite numbers, inequalities, equality
or absolute tolerance, optionally comparing an accepted dependency's result.
Percentage values use ratios. Each compute task must map requirements to actual
own/dependency evidence. The store evaluates checks again at commit; a worker's
accepted flag cannot override failure or a missing/ambiguous metric.
Numeric checks select scalar metrics; a sequence's length or minimum must be
computed and registered separately. Existence does not establish sequence length.

Analysis validates coverage, source quotes, check scopes and the dependency DAG
inside the existing bounded structured-output retry. Semantic feedback includes
all detected contract defects and the actual rejected candidate, so the model can
repair that plan without weakening requirements. Schema parsing failures retain
the raw tool arguments or JSON reply as the next repair candidate too; each retry
replaces the previous candidate rather than accumulating invalid plans. Analysis resolves
whitespace-only quote differences to the exact original source span before
validation; whole numeric and Latin-word tokens prevent merging distinct numbers
or words. Paraphrases and changed values still fail validation. Prediction methods
named linear do not imply a source-provided linear optimization problem. Exhausted analysis
marks its phase blocked and its thread failed; resuming before business-state
initialization retries the original checkpointed question rather than the word
"continue". The UI also stops displaying a failed work's active phase as running.

For explicit linear optimization problems, `linear_feasible` freezes source-quoted
variables, bounds, integrality, coefficient rows and objective. The coordinator
recomputes each constraint and the reported objective from separately registered
decision variables; a solver-reported zero residual is insufficient. Missing values
are inconclusive and violations enter the same causal repair lifecycle. This check
does not certify optimality or validate a model invented for an open-ended problem.

`SolveReport` has a task-scoped submission validator inside the existing CodeAct
tool loop. It checks mandatory metric names from the frozen protocol, requirement
evidence bindings, all report references, successful current-attempt execution
sources and required figure files. Invalid submissions return the specific gaps,
actual result IDs and successful run IDs as tool feedback; the solver can register
existing output or compute a missing metric in the same kernel before resubmitting.
No extra model reviewer or independent repair loop is added. The submission and
central commit share requirement-evidence checks; numeric threshold failures stay
with central domain acceptance and causal replanning, so a counterexample can still
be submitted truthfully. Newly declared premises still stage before frozen reruns.
Invalid submissions count against the existing hard tool-step budget. Once it is
exhausted, missing evidence stops the run instead of requesting a tool-free report
that cannot fill those gaps; transcripts and execution output are retained.

All findings use `IssueReport` / `Issue`, with evidence, task/assumption revisions,
check IDs, repair targets and lifecycle status. Deterministic acceptance failures
are confirmed; solver, consistency and final-section semantic findings start as
suspicions. The controller creates an independent CodeAct investigation against
the frozen checks. A reproduced failure confirms the issue and invalidates its
scope and descendants. Checks must be explicitly named and applicable; for a
cited result, their operands must match the disputed metrics unless a named
assumption links them. Passing checks dismiss the suspicion only when the
independently recomputed values also agree with the original evidence within
the check tolerance. Investigation evidence
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

Section reference and consistency failures block publication too. Repairs stay
within the writing loop; exhausting its budget preserves versioned drafts and
stops the run. Explicit resume can retry those repairs without rerunning accepted
computations. Structured-output retries include the concrete validation errors
and keep a bounded request count instead of repeating an invalid request blindly.

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

`test_review_lifecycle.py` reproduces unresolved local audit findings that used to
allow delivery, verifies fresh re-audits, audit-failure recovery, independent
countercheck confirmation/dismissal, retained historical evidence, adopted design
and solve premises, idempotence, inherited consumers and rejection of stale work.
The former `sound`/string `must_fix` output protocol has been removed, with no
adapter or old-checkpoint migration.

`test_live_regressions.py` covers structured retry feedback, unrelated/unknown
check rejection, independent metric disagreement, publication gates after writing
repair exhaustion, frontend state events, and draft-preserving resume without
recomputing accepted tasks.

`test_report_submission.py` exercises premature submission, missing metric names,
requirement bindings, out-of-scope references, invalid execution sources, figure
files, independent countercheck bindings, task-local validator isolation, bounded
failure and tool-driven gap repair without rerunning the completed computation.

The evaluation CLI defaults to `--pipeline research`. Its `research-no-explore`,
`research-no-review`, `research-no-consistency` and `research-no-planner` variants
all use the same versioned engine.
No paid model benchmark is required for the offline regression suite; quality
or cost improvements must be established separately on held-out research tasks.

Numeric traceability reads the authoritative current accepted registry and rechecks
each value against its exact successful execution stdout. Historical/failed outputs,
unregistered numbers and stale registry projections do not count. Missing research
state makes the metric unavailable and is reported explicitly in the summary.
This metric establishes numeric provenance, not semantic or methodological correctness.

Writing receives actual section-scoped reference tables and only current adopted
premises, including when a saved design contains older premise drafts. No fixed
figure ID or minimum assumption count is suggested. The API and research-state
events expose a delivery explanation derived from the existing gate, and the UI
shows its concrete blockers and repair steps without implementing another scheduler.
Each task exposes only its current revision's acceptance record; previous checks
remain history rather than being displayed as validation of a new revision.
Semantic section audits retain raw references and receive their registered values,
including auxiliary results absent from the key-claim table. Writer-only reference
instructions are not reused as audit criteria for rendered text.
Final numeric checks also cover standalone math scalars and escaped percentages
such as `$23.1\%$`, while preserving formula coefficients and code examples.
