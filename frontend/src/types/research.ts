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
  kind: string
  run_id: string
  figure_path: string | null
}
export interface ResearchSnapshot {
  schema_version: 1
  engine_version?: string
  research_revision?: number
  commit_seq?: number
  outcome?: string | null
  tasks?: {
    task_id: string
    revision: number
    objective: string
    dependencies: string[]
    execution_status: string
    acceptance: string
    validity: string
    reason?: string
  }[]
  blocking_issues?: { task_id: string; message: string; blocking: boolean }[]
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
  scope?: string[]
}
