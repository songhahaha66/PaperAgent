# Paper workbench

Desktop opens chat and the paper side by side. The paper tab reads authoritative
research-v3 sections; native TeX is converted to a readable view with the existing
Pandoc installation. Source and PDF remain separate tabs. Mobile keeps the existing
single-panel navigation; desktop splits stack when the viewport narrows.

Select a section and either request wording changes or edit a candidate directly.
Review the before/after text, then apply or reject. Candidates and decisions live
in the existing SQLite commands table, with section versions in the journal and
paper versions in `.system/research/<id>/papers/`. Reloading recovers pending
candidates from history. Applying preserves unaffected sections and task attempts.
Numbers, equations and evidence references cannot change through the wording path.
Reviewers must also check meaning and conclusion strength before applying.

Data, method and conclusion changes show the selected section's task dependencies
and their downstream closure, then use the existing `revise_research` command and
selective resume. The displayed scope is an impact estimate; the existing planner
is authoritative. Questions about selected prose or figures use the existing `ask`
path. No AG-UI dependency was added: candidates have explicit pending/applied/
rejected states, and publication refreshes the existing commit-sequenced snapshot.

Publication checks the active source hash and research commit, refreshes input
fingerprints, compiles exports in an isolated temporary directory, then rechecks
before publishing. Manual source edits, stale evidence, concurrent changes and
failed exports preserve the active paper and candidate. Successful Word edits
reuse the existing template. No model is needed for manually authored candidates.

Reference sections quote bibliography lines explicitly supplied in the task, URL/
DOI-bearing source lines, and dedicated `.bib`/reference text attachments. They do
not fill metadata from model memory. Unverified or missing information is labeled
as pending, not represented as a verified bibliography. This is not literature
retrieval or independent bibliographic verification.

## Validation in this development environment

- Backend: 103 related tests passed, followed by 9 focused editor tests after adding
  the input-file-change conflict check (104 distinct cases across those runs).
- Frontend: workspace suites, including panel/session behavior, preview, history,
  evidence/progress, resources, theme, home and messages; type check and Vite build.
- Real Chromium against local FastAPI routes and an isolated fixture research:
  section selection, manual candidate, side-by-side review, apply, reload, reject,
  and downstream Q1 → Q2 impact display. Authentication/ownership were fixture
  dependencies; API ownership is covered separately. No live model calls.
- Real XeLaTeX compilation and PDF text extraction confirmed the applied wording
  and reference-gap section. Chinese TeX packages/fonts were installed only in the
  workspace from CTAN/TeX Live's official 2025 archive to match the system kernel.
- Automated editor regressions cover idempotence, unaffected tasks/sections,
  version history, manual edits, competing candidates, changed input data,
  numerical/equation/reference guards, failed compilation and owner checks.

The specified gateway currently has no HTTPS entry point. No credentials were
sent and no live-model/browser research recomputation is claimed. Model-generated
wording and real research calls still require a safely configured model provider.

Publication now writes a recovery marker and artifact backups before replacement.
A failed file replacement, SQLite write/commit, or interrupted process is resolved
against the committed candidate decision: pending restores every old artifact;
applied retains the committed files. Recovery runs when opening the paper and
before another decision. Backups remain available if recovery itself fails.

Gateway preparation (2026-10-18): TLS verification succeeded for
`https://aigw.saurlax.com/v1/models`; the unauthenticated request returned HTTP 401
with `missing api key`. Use `https://aigw.saurlax.com/v1` as the OpenAI-compatible
base candidate. The model list and the exact Step 5 Preview model ID still need
an authenticated models response through supported secure configuration. No
credential was supplied, and no live-model success is claimed.
