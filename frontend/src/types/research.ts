export interface ResearchQuestion {
  id: string
  title: string
  question: string
  experiment_ids: string[]
  sources: string[]
  judgment: string
  next_step: string
  issues: { id: string; message: string; next_step: string }[]
  artifact_refs: string[]
  limitations: string[]
  judgments: {
    question_id: string
    statement: string
    artifact_refs: string[]
    limitations: string[]
    next_step: string
  }[]
}
export interface EvidenceUse {
  ref: string
  section: string
  section_version: string
  title: string
  task_ids: string[]
  quote: string
}
export interface ResearchActivity {
  id: string
  task_id?: string | null
  label: string
  capability?: string
  status: string
  started_at: number
  finished_at?: number
  run_id?: string
  input_tokens?: number
  output_tokens?: number
}
export interface ReadableResult {
  id: string
  name: string
  description: string
  unit: string
  kind: string
  value: string
  run_id: string
  figure_path: string | null
  series: number[] | null
}
export interface ResearchExperiment {
  question_id: string
  kind: string
  dependencies: string[]
  execution_status: string
  acceptance: string
  validity: string
  reason?: string
  progress: {
    state: string
    waiting_for: string[]
    issue_ids: string[]
    executions: number
    attempt_limit: number
    rework: boolean
    next_step?: string
  }
  validation?: {
    outcome: string
    checks: { check_id: string; outcome: string; message: string }[]
  } | null
  artifacts: { id: string; kind: string; path: string | null; run_id: string }[]
  id: string
  task_id: string
  title: string
  objective: string
  revision: number | null
  attempt_id: string | null
  started_at?: number | string | null
  computation: string
  availability: string
  checking: string
  method: {
    name?: string
    idea?: string
    formulation?: string
    steps?: string[]
    validation?: string
  } | null
  experiment?: {
    conditions: string
    evaluation: string
    candidates: { name: string; instruction: string }[]
  } | null
  results: ReadableResult[]
  claims: { id: string; statement: string; strength: string; evidence: string[] }[]
  issues: { id: string; message: string; category: string; status: string; action: string }[]
  sources: { path: string; hash: string; available: boolean }[]
  next_step: string | null
  approach?: string
  summary?: string
  history: {
    attempt_id: string
    revision: number
    execution_status: string
    acceptance: string
    results: ReadableResult[]
  }[]
}
export type ResearchAction =
  | 'auto'
  | 'ask'
  | 'run'
  | 'resume'
  | 'confirm'
  | 'revise'
  | 'revise_research'
export interface ResearchResult {
  id: string
  subproblem_id: string
  name: string
  value: string
  unit: string
  kind: 'number' | 'series' | 'text' | 'figure'
  run_id: string
  figure_path: string | null
}
export interface ResearchSnapshot {
  inputs?: string[]
  questions?: ResearchQuestion[]
  evidence_uses?: EvidenceUse[]
  runtime?: {
    actions: ResearchActivity[]
    models?: ResearchActivity[]
    decisions: {
      id: string
      action: string
      reason: string
      question_id?: string | null
      artifact_refs: string[]
      issue_ids: string[]
    }[]
  }
  experiments?: ResearchExperiment[]
  faults?: {
    id: string
    task_id: string
    message: string
    status: string
    action: string
    category: string
  }[]
  execution_reason?: { kind: string; message: string } | null
  schema_version: 4
  engine_version?: string | null
  research_revision?: number
  commit_seq?: number
  outcome?: string | null
  delivery_stages?: Record<string, { status: string; note: string }>
  delivery?: {
    ready: boolean
    blockers: { task_id: string; message: string; action: string }[]
  } | null
  requirements?: {
    id: string
    statement: string
    source_quote: string
    kind: string
    task_ids: string[]
    status: string
  }[]
  assumptions?: {
    id: string
    statement: string
    revision: number
    status: string
    task_ids: string[]
    source?: string
  }[]
  validations?: {
    attempt_id: string
    outcome: string
    checks: { check_id: string; outcome: string; message: string }[]
  }[]
  feedback?: {
    id: string
    task_id: string
    task_revision: number
    section?: string | null
    message: string
    blocking: boolean
    status: string
    action: string
    source?: string
    evidence_refs: string[]
    assumption_id: string | null
  }[]
  issue_evidence?: ResearchResult[]
  work_id: string
  work_status: string
  research_id: string | null
  graph_status: string | null
  has_research: boolean
  results: ResearchResult[]
  runs: { id: string; status: string; input_path: string | null; stdout_path: string | null }[]
  claims: {
    id: string
    subproblem_id: string
    statement: string
    strength: string
    evidence: string[]
  }[]
  artifacts: string[]
  validation_count: number
  truncated: boolean
}

export interface ResearchRevisionCommand {
  command_id: string
  research_id: string
  base_revision: number
  source_issue?: { id: string; version: string }
  scope?: string[]
}
