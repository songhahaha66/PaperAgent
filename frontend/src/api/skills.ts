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
  read: (work: string) => apiClient.request<SkillState>(`/api/workspace/${work}/skills`),
  detail: (work: string, key: string) =>
    apiClient.request<ResearchSkill>(`/api/workspace/${work}/skills/${encodeURIComponent(key)}`),
  save: (work: string, base_version: number, policy: SkillPolicy) =>
    apiClient.request<{ version: number; policy: SkillPolicy; affected: string[] }>(
      `/api/workspace/${work}/skills`,
      { method: 'PUT', body: JSON.stringify({ base_version, policy }) },
    ),
}
