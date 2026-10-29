export interface ResearchQuestion {
  id: string
  title: string
  question: string
  version?: number
  retired?: boolean
  answer_status?: string
  answer_gaps?: string[]
  coverage?: 'answered' | 'partial' | 'unanswered' | null
  stop_reason?: string
  history?: { question: string; reason?: string }[]
  experiment_ids: string[]
  sources: string[]
  judgment: string
  next_step: string
  issues: { id: string; message: string; next_step: string }[]
  artifact_refs: string[]
  limitations: string[]
  judgments: {
    recorded_at?: number
    observation_id?: string
    action_id?: string
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
  action_id?: string | null
  observation_id?: string | null
  question_id?: string | null
  experiment_id?: string | null
  parent_call_id?: string | null
  subject?: string | null
  purpose?: string | null
  inputs?: Record<string, unknown>
  output?: Record<string, unknown>
  message?: string
  error_type?: string
  phase?: string
  kind?: string
  updated_at?: number
  task_id?: string | null
  label: string
  capability?: string
  status: string
  started_at?: number | null
  finished_at?: number
  run_id?: string
  input_tokens?: number
  output_tokens?: number
  cache_read_tokens?: number | null
  context_usage?: { messages_estimated_tokens: number; tools_estimated_tokens: number; original_messages_estimated_tokens: number; summarized_messages: number } | null
}
export interface ResearchHistory {
  id: string
  kind: string
  label: string
  action_id?: string | null
  observation_id?: string | null
  question_id?: string | null
  experiment_id?: string | null
  recorded_at?: number | null
  reason: string
  artifact_refs: string[]
  judgments: ResearchQuestion['judgments']
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
  meaning?: {
    object: string
    scope: string
    statistic: string
    conditions?: string
    lineage: { path: string; source_path: string; row_keys: string[]; transformation?: string }[]
  } | null
}
export interface ResearchExperiment {
  interpretation_version?: string
  primary_result_refs?: string[]
  current_actions?: ResearchActivity[]
  record?: {
    summary?: string
    approach?: string
    key_findings?: { statement: string; strength: string }[]
    sensitivity?: string
    limitations?: string[]
  }
  interpretation_history?: {
    report_ref: string
    status: string
    report: ResearchExperiment['record']
  }[]
  historical_claims?: {
    id: string
    statement: string
    strength: string
    status: string
    reason: string
  }[]
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
    attempt_limit: number | null
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
  snapshot_at?: number | null
  inputs?: string[]
  questions?: ResearchQuestion[]
  evidence_uses?: EvidenceUse[]
  runtime?: {
    current?: ResearchActivity | null
    updated_at?: number | null
    models_updated_at?: number
    history?: ResearchHistory[]
    usage?: {
      input_tokens: number | null
      output_tokens: number | null
      tps: number | null
      cache_hit_rate: number | null
    } | null
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
  execution_reason?: { kind: string; message: string; detail?: string } | null
  schema_version: 5
  engine_version?: string | null
  research_revision?: number
  commit_seq?: number
  outcome?: string | null
  delivery_summary?: { state: string; stage: string | null; label: string }
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
