import { apiClient } from '@/utils/apiClient'
export interface ResearchSetup {
  request_limit: number | null
  output_token_limit?: number | null
  total_token_limit?: number | null
  cost_limit_usd?: number | null
  timeout_seconds?: number | null
  compact_paper: boolean
  reuse_experience: boolean
  literature_mode: 'off' | 'on_request' | 'auto'
  literature_query: string
  source_urls: string[]
  skill_keys: string[]
}
export const defaultResearchSetup = (): ResearchSetup => ({
  request_limit: null,
  compact_paper: true,
  reuse_experience: true,
  literature_mode: 'on_request',
  literature_query: '',
  source_urls: [],
  skill_keys: [],
})
export const researchSetupAPI = {
  save: (work: string, body: ResearchSetup) =>
    apiClient.request<{ configured: boolean; literature: { sources: { error?: string }[] } }>(
      `/api/workspace/${work}/research-setup`,
      { method: 'PUT', body: JSON.stringify(body) },
    ),
}
export interface ReviewTaskItem {
  stage: 'reconcile' | 'sections' | 'citations'
  label: string
  version: string
  state: string
  coverage: string
  can_resume: boolean
  reason?: string
  outcome?: string
  record_id?: string
  coverage_detail?: {
    policy: string
    complete: boolean
    sections: Record<
      string,
      { total_chars: number; included_chars: number; local_complete?: boolean }
    >
  }
  segmented?: {
    sections?: {
      section: string
      revision: number
      completed_blocks: number
      total_blocks: number
      covered_chars: number
      total_chars: number
      local_complete: boolean
    }[]
    total_blocks: number
    completed_blocks: number
    covered_chars: number
    total_chars: number
    remaining_requests_upper_bound: number
    global: { state: string; scope: string; reason?: string }
    blocks: {
      id: string
      section: string
      start: number
      end: number
      window_start: number
      window_end: number
      revision: number
      state: string
      review_id?: string
    }[]
  }
}
export interface ReviewTaskState {
  research_id: string
  research_revision: number
  tasks: ReviewTaskItem[]
  history: {
    id: string
    stage: string
    state: string
    original_state: string
    source?: string
    coverage_note?: string
    version: string
    replacement_review_id?: string
    recovered_by_review_id?: string
    error_code?: string
  }[]
  pending_candidates: { id: string; section: string; state: string }[]
  issues?: { id: string; category: string; message: string; status: string; section?: string }[]
}
export const reviewTasksAPI = {
  read: (work: string) => apiClient.request<ReviewTaskState>(`/api/workspace/${work}/review-tasks`),
  recover: (
    work: string,
    body: { research_id: string; stage: string; version: string; command_id: string },
  ) =>
    apiClient.request<{ id: string; state: string; deduplicated: boolean }>(
      `/api/workspace/${work}/review-tasks`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
}
