# Workspace interaction refactor

This batch retains the existing tab workspace, optional splits, file viewers and paper editing APIs. It changes the entry points and their responsibilities:

- `Work.vue` opens files, paper, literature and typed results through the workspace store. Reopening an object activates its existing tab. `overview` is a compatibility alias for the single run-status tab.
- `ResearchOverview` now composes current run state, generated results, task progress, review recovery and expandable run records. It no longer repeats literature, skills, experience and comparison configuration.
- `ResearchResultViewer` dispatches registered results and existing experiment records to their content viewers. Historical experiment records remain reachable from run status. The workspace does not assume CSV inputs, a specific metric or two result candidates.
- References open the same literature tab at the acquired passage. Registered results open only source links actually supplied by the snapshot. Experiment data links require a matching current workspace file.
- New work starts with task text and attachments. Budget and capability controls are in settings; existing defaults remain unchanged. Existing experiment configuration is available in project settings.
- Paper editing controls expand on demand. Tab-scoped, user-scoped `sessionStorage` retains tabs, split geometry, chat drafts, reading positions, citation location, selected paper section and section drafts across refresh. Drafts retain their original base commit; server conflict checks still control publication. This is browser-session recovery, not cross-device synchronization.
- Confirmation rechecks the current research snapshot and status, rejects changed or unavailable snapshots, and prevents duplicate submissions while checking.
- Shared `t-icon` controls resolve to installed local SVG components, so icon-only actions are visible without a runtime icon CDN.

## Validation performed

On the cloud environment, based on `origin/dev` at `0c2f8fedf3b7fe6735d8a7c339fe7a16bb2e86a1`:

- `npm run build` (includes Vue/TypeScript checks).
- `npm run test:panels`, including new canonical-object, refresh, citation-location and per-section draft tests.
- `npm run test:paper`, `test:messages`, `test:research`, `test:resources`, `test:home`.
- New historical-result test verifies ID-based access, three candidates, and no policy mutation while viewing.
- Chromium with the real local API, PostgreSQL, workspace files, research SQLite and graph checkpoint: open material from chat and file navigation; switch tabs; view run status; open a result, its code and output; follow a citation to a retrieved NumPy documentation passage; refresh at that passage; restore manual paper edits after refresh; preview, reject and apply an edit; inspect a mobile viewport.

The environment database was empty. The browser walkthrough therefore uses an explicitly labelled local demonstration project with constructed demand observations and actual Python calculations. It does not claim a live research run or model-generated findings. The model-request counter remained zero.

Screenshots and browser check output in the execution environment:
`/workspace/scratch/paperagent-review/` (`01-workspace.png` through `08-mobile.png`, `checks.txt`). These are captured application screens, not design mockups. The local walkthrough script and fixture setup are `/tmp/paperagent-browser.cjs` and `/tmp/seed-paperagent.py`; the temporary fixture credential file is not a deliverable and is not committed.

## Current boundaries

Registered-result snapshots provide code/output links but do not expose a separate, authoritative per-result dataset link. The viewer states this gap. Existing comparison computation and protocol constraints remain in their domain viewer. Request budget and timeout can be set before research; started projects display their existing budget because the existing setup API disallows overwriting a checkpoint. No backend agent/runtime rewrite, new comparison algorithm, paid model call or production deployment is part of this batch.
