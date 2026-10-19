import { apiClient } from '@/utils/apiClient'
import type { ComparisonSpec } from './comparisons'
export interface ResearchSetup {request_limit:number;timeout_seconds:number;compact_paper:boolean;confirm_models:boolean;reuse_experience:boolean;literature_mode:'off'|'on_request'|'auto';literature_query:string;source_urls:string[];skill_keys:string[];compare:boolean;comparison_spec:ComparisonSpec|null}
export const defaultResearchSetup=():ResearchSetup=>({request_limit:40,timeout_seconds:90,compact_paper:true,confirm_models:true,reuse_experience:true,literature_mode:'on_request',literature_query:'',source_urls:[],skill_keys:[],compare:false,comparison_spec:null})
export const researchSetupAPI={save:(work:string,body:ResearchSetup)=>apiClient.request<{configured:boolean;literature:{sources:{error?:string}[]}}>(`/api/workspace/${work}/research-setup`,{method:'PUT',body:JSON.stringify(body)}),overview:(work:string)=>apiClient.request<any>(`/api/workspace/${work}/research-overview`)}
export interface ReviewTaskItem {stage:'reconcile'|'sections'|'citations';label:string;version:string;state:string;coverage:string;can_resume:boolean;reason?:string;outcome?:string;record_id?:string}
export interface ReviewTaskState {research_id:string;research_revision:number;tasks:ReviewTaskItem[];history:{id:string;stage:string;state:string;original_state:string;source?:string;version:string;replacement_review_id?:string;recovered_by_review_id?:string;error_code?:string}[];pending_candidates:{id:string;section:string;state:string}[]}
export const reviewTasksAPI={read:(work:string)=>apiClient.request<ReviewTaskState>(`/api/workspace/${work}/review-tasks`),recover:(work:string,body:{research_id:string;stage:string;version:string;command_id:string})=>apiClient.request<{id:string;state:string;deduplicated:boolean}>(`/api/workspace/${work}/review-tasks`,{method:'POST',body:JSON.stringify(body)})}
export interface ExecutionRecoveryProof {
  proof_hash:string;issue_id:string;task_id:string;revision:number;attempt_id:string;policy:string
  refusal:{tool_call_id:string;kernel_executed:boolean}
  successful_computation:{run_id:string;metric_outputs:Record<string,string>}
  validation:{checks:{check_id:string;outcome:string}[]}
  registered_evidence:Record<string,{ref:string;run_id:string;value:string}>
}
export const executionRecoveryAPI={
  preview:(work:string,issue:string)=>apiClient.request<ExecutionRecoveryProof>(`/api/workspace/${work}/research/issues/${issue}/execution-recovery`),
  resolve:(work:string,issue:string,proof_hash:string)=>apiClient.request<ExecutionRecoveryProof>(`/api/workspace/${work}/research/issues/${issue}/execution-recovery`,{method:'POST',body:JSON.stringify({proof_hash})}),
}
