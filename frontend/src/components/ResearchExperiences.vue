<template>
  <section class="experience-panel">
    <header><h2>{{ isZh ? '研究经验' : 'Research experience' }}</h2><button :disabled="busy" @click="load">{{ isZh?'刷新':'Refresh' }}</button></header>
    <p>{{ isZh?'仅复用你有权访问的研究。历史经验是方法提醒，本研究仍须独立求解并登记新证据。它与 25 条静态方法、6 个详细技能分开。':'Private historical advice, separate from static methods and skills. Each study must produce its own evidence.' }}</p>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <label><input type="checkbox" :checked="state?.enabled" :disabled="busy" @change="setPolicy(($event.target as HTMLInputElement).checked)" />{{ isZh?'复用本人研究经验':'Reuse my research experience' }}</label>
    <button :disabled="busy" @click="rebuild">{{ isZh?'补建本人经验索引':'Rebuild my index' }}</button>
    <p class="note">{{ isZh?'禁用或删除会阻止后续注入；在途提交也会重新检查。删除后补建、恢复研究不会自动找回该经验。':'Disabled/deleted entries cannot be reused; active submissions recheck availability. Rebuild does not restore deleted cards.' }}</p>
    <h3>{{ isZh?'当前研究读取的经验':'Experience read by this study' }}</h3>
    <p v-if="state?.usage_error" role="alert">{{ state.usage_error }}</p>
    <p v-if="!state?.usage.length">{{ isZh?'尚未读取跨研究经验。':'No history used yet.' }}</p>
    <article v-for="u in state?.usage" :key="u.attempt_id" class="usage">
      <strong>{{ u.task_id }} · v{{ u.revision }}</strong> · {{ u.published?'已提交':'在途或未发布' }} · {{ u.available?'来源有效':'来源已失效或复用关闭' }}
      <p v-for="r in u.refs" :key="r.id+r.stage"><a :href="'#experience-'+r.id">{{ r.id.slice(0,13) }} · v{{ r.version }}</a> · {{ r.stage }} · {{ r.reason }} <code>{{ r.hash.slice(0,12) }}</code></p>
    </article>
    <h3>{{ isZh?'本人经验卡':'My experience cards' }} ({{ state?.cards.length||0 }})</h3>
    <p v-if="!state?.cards.length">{{ isZh?'完成研究尝试后会自动生成；旧记录可补建。':'Cards are generated after attempts commit; older records can be indexed.' }}</p>
    <article v-for="e in state?.cards" :id="'experience-'+e.id" :key="e.id" class="card">
      <header><strong>{{ levelLabel(e.card.level) }}</strong><span>v{{e.version}} · {{e.enabled?'启用':'禁用'}} · {{e.available?'来源有效':'来源不可用'}}</span></header>
      <p>{{e.card.question}}</p><p>{{e.card.problem_type}} · {{e.card.method_keys.join(', ')||'方法未标注'}}</p>
      <p>{{isZh?'已知条件':'Known conditions'}}：{{e.card.data_conditions.data_mode}} · {{e.card.data_conditions.structure}}<span v-if="e.card.data_conditions.columns.length"> · {{e.card.data_conditions.columns.join(', ')}}</span></p>
      <p v-for="c in e.card.unknown_conditions" :key="c" class="note">{{c}}</p>
      <p v-for="c in e.card.applicable" :key="c">{{c}}</p>
      <details><summary>{{isZh?'步骤、观察与检查':'Steps, observations and checks'}}</summary>
        <ol><li v-for="(s,i) in e.card.steps" :key="i">{{s}}</li></ol>
        <p v-for="(o,i) in e.card.observations" :key="i">{{o}}</p><p v-if="e.card.error">{{e.card.error}}</p>
        <p v-for="c in e.card.checks" :key="c.check_id">{{c.check_id}} · {{c.outcome}} · {{c.message}} · {{c.evidence_refs.join(', ')}}</p>
        <p v-for="c in e.card.not_applicable" :key="c">{{c}}</p>
        <p v-if="e.card.repairs.length">修复原失败 attempt：{{e.card.repairs.join(', ')}}</p><p v-if="e.card.repaired_by.length">后续修复 attempt：{{e.card.repaired_by.join(', ')}}</p>
        <p>input {{e.card.source.input_digest}} · protocol {{e.card.source.protocol_hash}}</p>
      </details>
      <RouterLink :to="'/work/'+encodeURIComponent(e.card.source.work_id)">{{isZh?'打开来源研究':'Open source study'}} · {{e.card.source.task_id}} v{{e.card.source.revision}}</RouterLink>
      <p class="note">{{e.card.source.research_id}} / {{e.card.source.attempt_id}}</p>
      <button :disabled="busy" @click="change(e)">{{e.enabled?(isZh?'禁用':'Disable'):(isZh?'启用':'Enable')}}</button>
      <button :disabled="busy" @click="remove(e)">{{isZh?'删除（不再自动恢复）':'Delete permanently from index'}}</button>
    </article>
  </section>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import { experienceAPI,type ExperienceState,type ExperienceCard } from '@/api/experiences'
import { useStudioCopy } from '@/lang/studio'
const props=defineProps<{workId:string;refreshKey?:number}>()
const {isZh}=useStudioCopy()
const state=ref<ExperienceState|null>(null),busy=ref(false),error=ref(''),notice=ref('')
const levelLabel=(level:string)=>({verified:'有契约检查证据的成功',counterexample:'检查反例',execution_error:'运行错误（非方法无效）',unconfirmed:'未确认观察'}[level]||level)
async function action(fn:()=>Promise<unknown>){busy.value=true;error.value='';try{await fn()}catch(e){error.value=e instanceof Error?e.message:'经验服务暂不可用'}finally{busy.value=false}}
async function load(){await action(async()=>{state.value=await experienceAPI.read(props.workId)})}
async function setPolicy(enabled:boolean){await action(async()=>{await experienceAPI.policy(props.workId,enabled);state.value=await experienceAPI.read(props.workId)})}
async function rebuild(){await action(async()=>{const r=await experienceAPI.rebuild(props.workId);notice.value=`已更新 ${r.updated} 条；暂不可用来源 ${r.unavailable_sources} 个`;state.value=await experienceAPI.read(props.workId)})}
async function change(e:ExperienceCard){await action(async()=>{await experienceAPI.change(props.workId,e.id,!e.enabled);state.value=await experienceAPI.read(props.workId)})}
async function remove(e:ExperienceCard){await action(async()=>{await experienceAPI.delete(props.workId,e.id);state.value=await experienceAPI.read(props.workId)})}
watch(()=>props.workId,()=>{state.value=null;notice.value='';load()},{immediate:true})
watch(()=>props.refreshKey,()=>load())
</script>
<style scoped>
.experience-panel{padding:24px;overflow:auto;flex:1;color:var(--pa-ink)}header{display:flex;gap:12px;align-items:center;flex-wrap:wrap}h2{font-size:20px}.card,.usage{border:1px solid var(--pa-line);border-radius:var(--pa-radius-card);padding:16px;margin:12px 0;overflow-wrap:anywhere}.note{font-size:12px;color:var(--pa-muted)}button{cursor:pointer;margin:6px;padding:6px 10px}button:disabled{cursor:wait}a{color:var(--pa-info)}p{margin:8px 0}
</style>
