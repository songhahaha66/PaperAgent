import { apiClient } from '@/utils/apiClient'
export interface PaperSection {
  key: string
  title: string
  level?: number
  text: string
  readable: string
  revision: number
  validity: string
  task_refs: string[]
  evidence_refs: string[]
  content_hash: string
}
export interface PaperProposal {
  id: string
  section: string
  instruction: string
  before: string
  after: string
  status: 'pending' | 'applied' | 'rejected'
  section_revision: number
  citation_issues?: string[]
  citation_checks?: { status: string; reason: string }[]
  section_keys?: string[]
  before_sections?: Record<string, string>
  after_sections?: Record<string, string>
  review_issues?: { section: string; message: string }[]
  review_id?: string
  edit_kind?: 'wording' | 'evidence'
  origin?: 'model' | 'manual'
  generation_contract?: string
  review_kind?: 'fragment_equivalence' | 'full_document' | 'issue_section' | 'issue_segmented'
  source_issue?: { id: string; version: string }
  review_progress?: {
    segmented: {
      completed_blocks: number
      total_blocks: number
      covered_chars: number
      total_chars: number
      global: { state: string }
    }
    target_state: string
    target_verdict?: string
  }
  review_progress_error?: string
  scope?: { id: string; old: string; start: number; end: number; section_hash: string }
  generation_edits?: { section: string; old: string; replacement: string }[]
}
export interface PaperIssueContext {
  issue: { id: string; task_id: string; status: string; message: string; category: string }
  route: 'writing' | 'research' | 'service'
  version: string
  section: string | null
  quote: string | null
  location: string
  origin_current: boolean
  active: boolean
  evidence: {
    id: string
    name: string
    value: string
    unit: string
    current: boolean
    stdout_path?: string
  }[]
  evidence_note: string
}
export interface PaperDocument {
  title?: string
  publication_notice?: string
  outline?: { key: string; title: string; level: number; available: boolean }[]
  feedback?: {
    id: string
    message: string
    category: string
    section: string | null
    task_id: string
    status: string
  }[]
  artifacts?: string[]
  research_id: string | null
  commit_seq: number
  source_hash?: string | null
  patch_attempts?: {
    id: string
    state: string
    recovery_of?: string | null
    recovery_id?: string
    scope: { section: string; section_hash: string; source_hash: string; old: string }
  }[]
  sections: PaperSection[]
  history: PaperProposal[]
  tasks: { id: string; title: string; dependencies: string[] }[]
  verified_methods?: { task_id: string; fit_intercept: boolean; standardize: boolean }[]
  verified_formula_blocks?: Record<string, string>
}
export const paperAPI = {
  issue: (work: string, id: string) =>
    apiClient.request<PaperIssueContext>(
      `/api/workspace/${work}/paper/issues/${encodeURIComponent(id)}`,
    ),
  read: (work: string) => apiClient.request<PaperDocument>(`/api/workspace/${work}/paper`),
  propose: (work: string, body: object) =>
    apiClient.request<PaperProposal>(`/api/workspace/${work}/paper/proposals`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  decide: (work: string, id: string, research_id: string, decision: string) =>
    apiClient.request<PaperProposal>(`/api/workspace/${work}/paper/proposals/${id}`, {
      method: 'POST',
      body: JSON.stringify({ research_id, decision }),
    }),
}

export interface SourceSelection {
  file: string
  version: string
  start: number
  end: number
  quote: string
  line?: number
  context?: string
}
export interface PaperSource {
  key: string
  title: string
  excerpt: string
  boundary: string
  abstract_only?: boolean
  locator: string
  page?: number | null
  url: string
  version: string
  paths?: string[]
  note: string
}
export interface PaperExecution {
  id: string
  thread_id: string
  message: string
  state: 'pending' | 'running' | 'failed' | 'interrupted' | 'completed'
  phase: string
  answer?: string
  error?: string
  change_id?: string
  selection?: SourceSelection
  references?: PaperReference[]
  steps?: {
    id: string
    tool: string
    state: 'running' | 'completed' | 'failed'
    output?: string
    result?: PaperExecution['tool_result']
  }[]
  tool_result?: {
    ok?: boolean
    stdout?: string
    error?: string
    code_path?: string
    stdout_path?: string
    inputs?: { path: string; hash: string }[]
  }
}
export interface PaperChange {
  id: string
  thread_id?: string
  execution_id?: string
  status: 'pending' | 'applied' | 'rejected' | 'reverted'
  diff: string
  patches: (SourceSelection & { replacement: string })[]
}
export interface PaperProject {
  research_id: string
  main: string
  version: string
  has_source: boolean
  draft_sections?: PaperSection[] // Word/Markdown only
  files: { path: string; version: string; text: string | null; size: number }[]
  compile: { state: string; message: string; warnings?: string[] }
  pdf_available: boolean
  pdf_version?: string
  migration_notice?: string
  feedback?: {
    id: string
    message: string
    section?: string
    task_id?: string
    status: string
    paper_origin?: { version?: string; selection?: SourceSelection }
  }[]
  sources: PaperSource[]
  threads: {
    id: string
    selection?: SourceSelection
    references?: PaperReference[]
    messages: { role: string; text: string; execution_id: string }[]
  }[]
  executions: PaperExecution[]
  changes: PaperChange[]
}
const projectRequest = <T>(work: string, path: string, body?: object) =>
  apiClient.request<T>(
    `/api/workspace/${work}/paper/${path}`,
    body ? { method: 'POST', body: JSON.stringify(body) } : undefined,
  )
export const paperProjectAPI = {
  read: (work: string) => projectRequest<PaperProject>(work, 'project'),
  locate: (work: string, quote: string) =>
    projectRequest<{ candidates: SourceSelection[] }>(work, 'locate', { quote }),
  comment: (work: string, body: object) => projectRequest<PaperExecution>(work, 'comments', body),
  resume: (work: string, id: string, research_id: string) =>
    projectRequest<PaperExecution>(work, `executions/${id}/resume`, { research_id }),
  propose: (work: string, body: object) => projectRequest<PaperChange>(work, 'changes', body),
  decide: (work: string, id: string, research_id: string, decision: string) =>
    projectRequest<PaperChange>(work, `changes/${id}`, { research_id, decision }),
  compile: (work: string, research_id: string) => projectRequest(work, 'compile', { research_id }),
}

export interface PaperReference {
  selection: SourceSelection | null
  read_quote: string | null
  read_version: string | null
  quote: string
}
export interface PaperComposerContext extends PaperReference {
  work_id: string
  research_id: string
  thread_id: string | null
}
