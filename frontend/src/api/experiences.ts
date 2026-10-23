import { apiClient } from '@/utils/apiClient'
export interface ExperienceRef {
  id: string
  version: number
  hash: string
  reason: string
  stage: string
}
export interface ExperienceCard {
  id: string
  version: number
  hash: string
  enabled: boolean
  available: boolean
  summary?: { problem: string; methods: string[]; data: string; structure: string }
  status: 'candidate' | 'validated' | 'adopted' | 'withdrawn'
  validation?: unknown
  history: { version: number; event: string; at?: number }[]
  card: {
    question: string
    problem_type: string
    method_keys: string[]
    level: string
    data_conditions: { data_mode: string; structure: string; columns: string[] }
    steps: string[]
    observations: string[]
    error: string
    applicable: string[]
    not_applicable: string[]
    unknown_conditions: string[]
    repairs: string[]
    repaired_by: string[]
    evidence_refs: string[]
    checks: { check_id: string; outcome: string; message: string; evidence_refs: string[] }[]
    source: {
      work_id: string
      research_id: string
      task_id: string
      attempt_id: string
      revision: number
      input_digest: string
      protocol_hash: string
    }
  }
}
export interface ExperienceState {
  enabled: boolean
  cards: ExperienceCard[]
  usage_error?: string
  usage?: {
    attempt_id: string
    task_id: string
    revision: number
    published: boolean
    available: boolean
    refs: ExperienceRef[]
    sources?: { id: string; version: number; title: string; source_work_id: string | null }[]
  }[]
}
const base = '/api/experiences'
export const experienceAPI = {
  read: (query = '') =>
    apiClient.request<ExperienceState>(base + '?q=' + encodeURIComponent(query)),
  usage: (work: string) =>
    apiClient.request<Pick<ExperienceState, 'usage' | 'usage_error'>>(
      `/api/workspace/${work}/experience-usage`,
    ),
  policy: (enabled: boolean) =>
    apiClient.request(`${base}/policy`, { method: 'PUT', body: JSON.stringify({ enabled }) }),
  rebuild: () =>
    apiClient.request<{ updated: number; unavailable_sources: number }>(`${base}/rebuild`, {
      method: 'POST',
    }),
  change: (id: string, enabled: boolean) =>
    apiClient.request(`${base}/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) }),
  transition: (id: string, action: 'validate' | 'adopt' | 'withdraw', base_version: number) =>
    apiClient.request(`${base}/${id}/transitions`, {
      method: 'POST',
      body: JSON.stringify({ action, base_version }),
    }),
  delete: (id: string) => apiClient.request(`${base}/${id}`, { method: 'DELETE' }),
}
