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
  citation_checks?: {status:string;reason:string}[]
}
export interface PaperDocument {
  research_id: string
  commit_seq: number
  sections: PaperSection[]
  history: PaperProposal[]
  tasks: { id: string; title: string; dependencies: string[] }[]
}
export const paperAPI = {
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
