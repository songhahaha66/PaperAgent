<template>
  <section class="review-tasks" aria-label="当前版本审查">
    <header><strong>当前版本审查<span v-if="state"> · v{{state.research_revision}}</span></strong><button :disabled="loading" @click="load">刷新审查</button></header>
    <p v-if="error" role="alert">{{error}}</p>
    <p v-if="!state && !error">正在读取审查状态…</p>
    <template v-if="state">
      <div v-for="task in state.tasks" :key="task.stage" class="review-row">
        <div><strong>{{task.label}}</strong> · <span :data-state="task.state">{{labels[task.state]||task.state}}{{task.outcome==='findings'?'，仍有实质意见':''}}</span><small>版本 {{task.version.slice(0,8)}} · {{task.coverage}}</small><small v-if="task.reason">{{task.reason}}</small></div>
        <button v-if="!['completed','not_required'].includes(task.state)" :disabled="busy===task.stage || !task.can_resume" @click="recover(task)">{{task.state==='running'?'审查进行中':'恢复这项审查'}}</button>
      </div>
      <p v-if="state.pending_candidates.length">{{state.pending_candidates.length}} 个编辑候选仍待处理。<button @click="$emit('paper')">到论文工作台核对候选</button></p>
      <details v-if="state.history.length"><summary>近期审查与替代关系</summary><p v-for="record in state.history.slice(0,8)" :key="record.id">{{stageLabels[record.stage]||record.stage}}{{record.source==='recovery-intent'?'恢复任务':'审查结果'}} · {{labels[record.state]||record.state}} · {{record.version.slice(0,8)}}<span v-if="record.replacement_review_id"> → {{record.replacement_review_id.slice(0,8)}}（原记录保留）</span><span v-if="record.recovered_by_review_id"> → {{record.recovered_by_review_id.slice(0,8)}}（同版本恢复，原失败保留）</span><span v-if="record.error_code"> · {{record.error_code}}</span></p></details>
    </template>
  </section>
</template>
<script setup lang="ts">
import {ref,onMounted,onUnmounted,watch} from 'vue'
import {reviewTasksAPI,type ReviewTaskState,type ReviewTaskItem} from '@/api/researchSetup'
const props=defineProps<{workId:string;refreshKey?:number}>(),emit=defineEmits<{changed:[];paper:[]}>()
const state=ref<ReviewTaskState|null>(null),error=ref(''),loading=ref(false),busy=ref('')
const labels:Record<string,string>={pending:'待审查',running:'运行中',completed:'已完成',failed:'失败，可恢复',superseded:'被新版本替代',not_required:'当前无需执行'}
const stageLabels:Record<string,string>={reconcile:'结论口径',sections:'章节一致性',citations:'文献支持',fragment:'局部等义'}
let version=0,operation=0,timer:ReturnType<typeof setInterval>|undefined
async function load(){const seq=++version;loading.value=true;try{const value=await reviewTasksAPI.read(props.workId);if(seq===version){state.value=value;error.value=''}}catch(e){if(seq===version)error.value=e instanceof Error?e.message:'审查状态暂不可读'}finally{if(seq===version)loading.value=false}}
async function recover(task:ReviewTaskItem){if(busy.value||!state.value||!task.can_resume)return;const op=++operation,work=props.workId;busy.value=task.stage;error.value='';try{await reviewTasksAPI.recover(work,{research_id:state.value.research_id,stage:task.stage,version:task.version,command_id:crypto.randomUUID()});if(op===operation&&work===props.workId){await load();emit('changed')}}catch(e){if(op===operation&&work===props.workId)error.value=e instanceof Error?e.message:'恢复未提交，进度保留'}finally{if(op===operation)busy.value=''}}
watch(()=>props.workId,()=>{operation++;busy.value='';state.value=null;load()},{immediate:true});watch(()=>props.refreshKey,load)
onMounted(()=>{timer=setInterval(()=>{if(state.value?.tasks.some(t=>t.state==='running')&&!loading.value)load()},1500)})
onUnmounted(()=>{version++;operation++;if(timer)clearInterval(timer)})
</script>
<style scoped>
.review-tasks{border:1px solid var(--pa-line);border-radius:var(--pa-radius-card);padding:12px;margin:12px 0}header,.review-row{display:flex;gap:10px;justify-content:space-between;align-items:center}.review-row{padding:8px 0;border-top:1px solid var(--pa-line)}small{display:block;color:var(--pa-muted);font-size:12px;overflow-wrap:anywhere}button{font:inherit;padding:5px 8px;cursor:pointer}p,details{font-size:12px;overflow-wrap:anywhere}[data-state=failed],[role=alert]{color:var(--pa-danger)}
</style>
