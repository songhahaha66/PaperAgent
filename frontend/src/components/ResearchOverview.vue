<template>
  <section class="research-overview">
    <header><h2>研究设置与状态</h2><button :disabled="loading" @click="load">刷新</button></header>
    <p v-if="error" role="alert">{{error}}</p>
    <template v-if="state">
      <p class="budget-state">模型请求 <strong>{{state.budget.used}} / {{state.budget.request_limit}}</strong> · {{state.budget.state}} · 每次最多 {{state.budget.timeout_seconds}} 秒</p>
      <p>跨标题、研究、追问及编辑累计，暂停/恢复不清零。比较另有独立子预算。</p>
      <p v-if="state.last_error" role="alert">最近停止原因：{{state.last_error}}</p>
      <p v-if="state.research.graph_status==='interrupted'">等待确认方案，可在对话中确认后继续；已消耗预算保留。</p>
      <p v-if="['paused','cancelled','failed'].includes(state.research.work_status)">研究已暂停或失败。原代码、检查点和有效产物保留；使用工作台恢复操作继续。</p>
      <ol>
        <li><strong>资料准备</strong><p>简短论文 {{state.settings.compact_paper?'开启':'关闭'}} · 设计确认 {{state.settings.confirm_models?'开启':'关闭'}}</p><button @click="emit('open','files')">数据与文件</button></li>
        <li><strong>公开文献与引用</strong><p>{{state.literature.sources}} 个来源 · {{state.literature.evidence}} 条实际摘录 · 正文引用 {{state.literature.citations.length}} 处</p><p v-for="(c,i) in state.literature.citations" :key="i">{{c.evidence_id}} · {{c.status}} · {{c.reason}}</p><p v-for="(o,i) in state.literature.operations.slice(0,2)" :key="i">{{o.kind}} · {{o.status}} {{o.error||''}}</p><button @click="emit('open','literature')">核对原文与支持状态</button></li>
        <li><strong>科研技能与历史经验</strong><p>{{state.experience.enabled?'本人经验复用已开启':'本人经验复用已关闭'}} · 设计实际读取 {{designCount}} 条 · 累计尝试冻结 {{frozenCount}} 条</p><p v-for="d in state.experience.design_usage" :key="d.task_id">{{d.task_id}} 设计：{{d.refs.map((r:any)=>r.id.slice(0,10)+' v'+r.version).join(', ')}}</p><p v-for="u in state.experience.usage" :key="u.attempt_id">{{u.task_id}} · {{u.refs.map((r:any)=>r.stage+': '+r.id.slice(0,10)+' v'+r.version).join(', ')}}</p><p v-for="s in state.skills" :key="s.task_id">{{s.task_id}} 静态技能：{{s.skills.map((v:any)=>v.key+' v'+v.version).join(', ')||'未定'}}</p><button @click="emit('open','experiences')">经验来源与条件</button><button @click="emit('open','skills')">静态技能</button></li>
        <li><strong>双方案比较</strong><p v-if="!state.comparisons.length">尚未进入比较阶段（需已启用且有适用 CSV）。</p><p v-for="c in state.comparisons" :key="c.id">{{c.current?'当前':'历史'}} {{c.task_id}} v{{c.revision}} · {{c.status}} · {{c.reason}}<br />{{c.branches.map((b:any)=>b.name+': '+b.status+(b.metric===undefined?'':' / '+b.metric.toPrecision(5))).join('；')}}</p><button @click="emit('open','comparisons')">冻结协议、指标与预算</button></li>
        <li><strong>实际求解与检查</strong><p>{{state.research.results.length}} 条当前登记结果 · {{state.research.validations.length}} 次验收</p><p v-for="t in state.research.tasks" :key="t.task_id">{{t.task_id}} · {{t.acceptance}} · {{t.validity}}</p><p v-for="i in state.research.issues.filter((v:any)=>!['resolved','dismissed'].includes(v.status)).slice(0,4)" :key="i.id">{{i.status}} · {{i.message}}</p><button @click="emit('open','progress')">研究任务与失败原因</button></li>
        <li><strong>论文与 PDF</strong><p>{{state.sections}} 个保存章节 · {{state.research.artifacts.join(', ')||'尚无通过发布检查的论文产物'}}</p><p v-for="(d,k) in state.research.delivery_stages" :key="k">{{k}} · {{(d as any).status}} · {{(d as any).note}}</p><button @click="emit('open','paper')">论文工作台与 PDF</button></li>
      </ol>
      <details><summary>实际模型请求记录（不含认证信息）</summary><p v-for="r in state.requests" :key="r.sequence">{{r.sequence}} · {{r.label||'研究调用'}} · {{r.http_status||r.state}}</p></details>
    </template>
  </section>
</template>
<script setup lang="ts">
import {ref,computed,watch,onMounted,onUnmounted} from 'vue'
import {researchSetupAPI} from '@/api/researchSetup'
const props=defineProps<{workId:string;running?:boolean;refreshKey?:number}>(),emit=defineEmits<{open:[kind:string]}>()
const state=ref<any>(null),error=ref(''),loading=ref(false)
const designCount=computed(()=>state.value?.experience.design_usage.reduce((n:number,u:any)=>n+u.refs.length,0)||0)
const frozenCount=computed(()=>state.value?.experience.usage.reduce((n:number,u:any)=>n+u.refs.length,0)||0)
let timer:ReturnType<typeof setInterval>|undefined,version=0
async function load(){const request=++version;loading.value=true;try{const result=await researchSetupAPI.overview(props.workId);if(request===version){state.value=result;error.value=''}}catch(e){if(request===version)error.value=e instanceof Error?e.message:'研究状态暂不可读'}finally{if(request===version)loading.value=false}}
watch(()=>props.workId,()=>{state.value=null;load()},{immediate:true});watch(()=>props.refreshKey,()=>load())
onMounted(()=>{timer=setInterval(()=>{if(props.running&&!loading.value)load()},5000)})
onUnmounted(()=>{version++;if(timer)clearInterval(timer)})
</script>
<style scoped>.research-overview{padding:24px;overflow:auto;flex:1;color:var(--pa-ink)}header{display:flex;justify-content:space-between;align-items:center}h2{font-size:20px}li{padding:14px;border:1px solid var(--pa-border);border-radius:12px;margin:12px 0;overflow-wrap:anywhere}ol{padding-left:18px}p{margin:8px 0;font-size:13px}.budget-state{padding:12px;background:var(--pa-surface-alt);border-radius:8px}button{margin:4px;padding:5px 10px;cursor:pointer}</style>
