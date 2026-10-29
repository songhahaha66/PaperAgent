import { apiClient } from '@/utils/apiClient'
export interface SkillPolicy {
  mode: 'auto' | 'selected'
  selected: string[]
  disabled: string[]
}
export interface ResearchSkill {
  key: string
  name: string
  category: string
  use_when: string
  needs: string
  steps: string[]
  pitfalls: string
  version: number
  sources: { title: string; url: string }[]
  instructions?: string
}
export interface SkillState {
  version: number
  policy: SkillPolicy
  catalog: ResearchSkill[]
  has_research: boolean
  usage: { task_id: string; skills: { key: string; version: number }[]; validity: string }[]
}
export const skillsAPI = {
  catalog: () => apiClient.request<{ methods: ResearchSkill[] }>('/api/resources/methods'),
  method: (key: string) =>
    apiClient.request<ResearchSkill>(`/api/resources/methods/${encodeURIComponent(key)}`),
  read: (work: string) => apiClient.request<SkillState>(`/api/workspace/${work}/skills`),
}
