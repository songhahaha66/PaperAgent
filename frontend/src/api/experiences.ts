import { apiClient } from '@/utils/apiClient'
export interface ExperienceRef { id:string; version:number; hash:string; reason:string; stage:string }
export interface ExperienceCard {
  id:string; version:number; hash:string; enabled:boolean; available:boolean;
  card:{ question:string; problem_type:string; method_keys:string[]; level:string;
    data_conditions:{data_mode:string; structure:string; columns:string[]}; steps:string[];
    observations:string[]; error:string; applicable:string[]; not_applicable:string[];
    unknown_conditions:string[]; repairs:string[]; repaired_by:string[];
    evidence_refs:string[]; checks:{check_id:string;outcome:string;message:string;evidence_refs:string[]}[];
    source:{work_id:string;research_id:string;task_id:string;attempt_id:string;revision:number;input_digest:string;protocol_hash:string}}
}
export interface ExperienceState {
  enabled:boolean; cards:ExperienceCard[]; usage_error?:string;
  usage:{attempt_id:string;task_id:string;revision:number;published:boolean;available:boolean;refs:ExperienceRef[]}[]
}
const base=(work:string)=>`/api/workspace/${work}/experiences`
export const experienceAPI={
  read:(work:string)=>apiClient.request<ExperienceState>(base(work)),
  policy:(work:string,enabled:boolean)=>apiClient.request(`${base(work)}/policy`,{method:'PUT',body:JSON.stringify({enabled})}),
  rebuild:(work:string)=>apiClient.request<{updated:number;unavailable_sources:number}>(`${base(work)}/rebuild`,{method:'POST'}),
  change:(work:string,id:string,enabled:boolean)=>apiClient.request(`${base(work)}/${id}`,{method:'PATCH',body:JSON.stringify({enabled})}),
  delete:(work:string,id:string)=>apiClient.request(`${base(work)}/${id}`,{method:'DELETE'}),
}
