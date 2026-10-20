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
export interface ResearchOutcome {
  id: string
  task_id: string
  title: string
  objective: string
  revision: number | null
  attempt_id: string | null
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
  plans?: {
    id: string
    version: number
    objective: string
    dependencies: string[]
    methods: { name: string; idea?: string }[]
    selected: number | null
    status: string
    current: boolean
  }[]
  experiments?: {
    id: string
    plan_id: string
    plan_version: number
    status: string
    acceptance: string
    experience_versions: { id: string; version: number }[]
  }[]
  faults?: {
    id: string
    task_id: string
    message: string
    status: string
    action: string
    category: string
  }[]
  legacy_records?: { source: string; raw: unknown }[]
  outcomes?: ResearchOutcome[]
  execution_reason?: { kind: string; message: string } | null
  schema_version: 3
  engine_version?: string | null
  research_revision?: number
  commit_seq?: number
  outcome?: string | null
  delivery_stages?: Record<string, { status: string; note: string }>
  delivery?: {
    ready: boolean
    blockers: { task_id: string; message: string; action: string }[]
  } | null
  tasks?: {
    task_id: string
    kind: string
    revision: number
    objective: string
    dependencies: string[]
    execution_status: string
    acceptance: string
    validity: string
    reason?: string
    progress: {
      state:
        | 'accepted'
        | 'running'
        | 'interrupted'
        | 'needs_plan'
        | 'failed'
        | 'waiting'
        | 'blocked'
        | 'rework'
        | 'ready'
        | 'review'
        | 'pending'
        | 'writing'
      waiting_for: string[]
      issue_ids: string[]
      attempts: number
      attempt_limit: number
      rework: boolean
      next_step?: string
    }
    validation?: {
      outcome: string
      checks: { check_id: string; outcome: string; message: string }[]
    } | null
  }[]
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
