import { apiClient } from '@/utils/apiClient'
export interface LiteratureSource {
  id: string
  title: string
  doi?: string | null
  url: string
  authors: string[]
  year?: number | string | null
  status: string
  error?: string | null
  open_access?: boolean
  evidence_count?: number
}
export interface LiteratureEvidence {
  id: string
  source_id: string
  text: string
  boundary: string
  locator: string
  page?: number | null
  source_url: string
  retrieved_at: string
  license: string
  truncated: boolean
  source: LiteratureSource
}
export interface CitationCheck {
  status: string
  reason: string
  supporting_quote?: string
}
export interface CitationUsage {
  section: string
  claim: string
  evidence_id: string
  status: string
  reason: string
}
export interface LiteratureState {
  mode: string
  sources: LiteratureSource[]
  citations?: CitationUsage[]
  operations: {
    kind: string
    query: string
    status: string
    errors?: { provider: string; message: string }[]
    error?: string
  }[]
  query_suggestion?: string
}
const base = (work: string) => `/api/workspace/${work}/literature`
export const literatureAPI = {
  read: (work: string) => apiClient.request<LiteratureState>(base(work)),
  policy: (work: string, mode: string) =>
    apiClient.request(`${base(work)}/policy`, { method: 'PUT', body: JSON.stringify({ mode }) }),
  search: (work: string, query: string) =>
    apiClient.request<LiteratureState>(`${base(work)}/search`, {
      method: 'POST',
      body: JSON.stringify({ query, limit: 3 }),
    }),
  import: (work: string, url: string) =>
    apiClient.request(`${base(work)}/import`, { method: 'POST', body: JSON.stringify({ url }) }),
  source: (work: string, id: string) =>
    apiClient.request<LiteratureSource & { evidence: LiteratureEvidence[] }>(
      `${base(work)}/sources/${id}`,
    ),
  fetch: (work: string, id: string) =>
    apiClient.request(`${base(work)}/sources/${id}/fetch`, { method: 'POST' }),
  evidence: (work: string, id: string) =>
    apiClient.request<LiteratureEvidence>(`${base(work)}/evidence/${id}`),
  retrieve: (work: string, query: string) =>
    apiClient.request<{ evidence: LiteratureEvidence[] }>(`${base(work)}/retrieve`, {
      method: 'POST',
      body: JSON.stringify({ query, limit: 5 }),
    }),
  check: (work: string, id: string, claim: string) =>
    apiClient.request<CitationCheck>(`${base(work)}/evidence/${id}/check`, {
      method: 'POST',
      body: JSON.stringify({ claim }),
    }),
}
