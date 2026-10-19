import { apiClient } from '@/utils/apiClient'
export interface PaperSection {
  key: string
  title: string
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
