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
  execution_reason?: { kind: string; message: string } | null
  schema_version: 2
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
  issues?: {
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
