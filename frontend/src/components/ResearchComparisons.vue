<template>
  <section class="comparison-panel">
    <header><h2>{{isZh?'双方案实验比较':'Two-candidate experiments'}}</h2><button :disabled="busy" @click="load">{{isZh?'刷新':'Refresh'}}</button></header>
    <p>{{isZh?'首版支持 CSV 结构化监督学习。项目固定样本划分、基线与选择规则，再独立重算指标；critic 分与候选打印的分数不参与选择。':'For supervised CSV data. The project freezes splits and rules, then independently measures predictions. Critic and printed scores cannot choose a winner.'}}</p>
    <p v-if="error" role="alert">{{error}}</p><p v-if="notice" role="status">{{notice}}</p>
    <details><summary>{{isZh?'比较配置（下一次设计前设置）':'Configure before design'}}</summary>
      <form @submit.prevent="save">
        <label><input type="checkbox" v-model="enabled" :disabled="busy||disabled" />{{isZh?'启用双方案模式':'Enable two-candidate mode'}}</label>
        <label><input type="checkbox" v-model="manual" :disabled="busy||disabled" />{{isZh?'指定共同协议（留空时由模型按列类型建议）':'Specify shared protocol'}}</label>
        <template v-if="manual">
          <label>CSV <select v-model="dataset"><option value="">选择文件</option><option v-for="f in state?.files" :key="f.path" :value="f.path">{{f.path}}</option></select></label>
          <p>{{columns.join(', ')}}</p>
          <label>目标列 <input v-model="target" required /></label><label>特征列（逗号分隔）<input v-model="features" required /></label><label>样本 ID 列（可空）<input v-model="idColumn" /></label>
          <label>任务 <select v-model="taskType" @change="metric=taskType==='binary'?'AUC':'RMSE'"><option value="regression">回归</option><option value="binary">二分类</option></select></label>
          <label>指标 <select v-model="metric"><option v-if="taskType==='binary'" value="AUC">AUC（越大越好）</option><template v-else><option value="RMSE">RMSE（越小越好）</option><option value="MAE">MAE（越小越好）</option></template></select></label>
          <label>划分 <select v-model="split"><option value="random">固定随机留出</option><option value="group">按组留出</option><option value="time">按时间留出</option></select></label><label v-if="split!=='random'">分组/时间列 <input v-model="splitColumn" required /></label>
          <label>seed <input type="number" v-model.number="seed" min="0" /></label><label>验证比例 <input type="number" v-model.number="fraction" min="0.1" max="0.4" step="0.05" /></label>
          <label>最低改善量（指标原单位）<input type="number" v-model.number="minimum" min="0" step="0.001" /></label><label>平局绝对容差 <input type="number" v-model.number="tie" min="0" step="0.000001" /></label>
        </template>
        <button :disabled="busy||disabled">保存配置</button>
      </form>
      <p class="note">执行后协议不可修改；配置变化不会清空预算或重跑完成的比较。新协议需明确修订任务或发起新研究。平局按候选顺序继续代表方案，不宣称实测优胜。</p>
    </details>
    <p v-if="!state?.comparisons.length">尚无实验比较。启用后，在有 CSV 的预测/回归/分类任务中生成两项不同方案。</p>
    <article v-for="c in state?.comparisons" :key="c.id" class="record">
      <header><strong>{{c.task_id}} v{{c.revision}} · {{c.status}}</strong><span>{{c.input_current?'冻结输入仍匹配':'输入已变化，不得晋升'}}</span></header>
      <p>{{c.reason}}</p><p>{{c.chosen_index===null||c.chosen_index===undefined?'没有选择候选':(c.input_current?'继续候选 ':'历史选择（已失效）候选 ')+(c.chosen_index+1)}}</p>
      <div class="budget"><span v-for="(limit,key) in c.budget.limits" :key="key">{{budgetLabel(String(key))}} {{c.budget.used[key]}} / {{limit}}</span></div>
      <details v-if="c.protocol"><summary>冻结协议 · {{c.protocol.metric}} · {{c.protocol_hash?.slice(0,12)}}</summary>
        <p>{{c.protocol.dataset}} · 目标 {{c.protocol.target}} · 特征 {{c.protocol.features.join(', ')}}</p>
        <p>seed {{c.protocol.seed}} · {{c.protocol.split}} · 基线 {{c.protocol.baseline}} = {{number(c.protocol.baseline_value)}}</p>
        <p>最低改善 {{c.protocol.minimum_improvement}} · 平局容差 {{c.protocol.tie_tolerance}} · {{c.protocol.negative_control}}</p>
        <p>数据 SHA256 {{c.protocol.data_hash}}</p><p>训练 {{c.protocol.train_ids.length}} / 验证 {{c.protocol.validation_ids.length}} 个实际样本</p>
        <details><summary>划分样本 ID</summary><pre>训练：{{c.protocol.train_ids.join(', ')}}
验证：{{c.protocol.validation_ids.join(', ')}}</pre></details>
      </details>
      <div class="branches"><article v-for="b in c.branches" :key="b.index" class="branch">
        <h3>候选 {{b.index+1}} · {{b.name}}</h3><strong>{{b.status}}</strong><p>实测 {{number(b.metric)}} · 共同基线 {{number(b.baseline)}} · 改善 {{number(b.improvement)}}</p>
        <p>置换指标 {{number(b.negative_metric)}} · 置换基线 {{number(b.negative_baseline)}}</p><p v-if="b.error" role="alert">{{b.error}}</p><p>{{b.selection_reason}}</p>
        <p>执行 {{number(b.elapsed_seconds)}} 秒 · 预留超时 {{b.timeout_reserved||0}} 秒</p><p class="note">代码 {{b.code_hash||'未生成'}} · runs {{b.run_ids.join(', ')||'无'}}</p>
        <details><summary>预测与冻结代码</summary><pre v-if="b.error_detail">{{b.error_detail}}</pre><table v-if="b.predictions?.length"><thead><tr><th>ID</th><th>实测标签</th><th>预测</th></tr></thead><tbody><tr v-for="p in b.predictions.slice(0,20)" :key="p.sample_id"><td>{{p.sample_id}}</td><td>{{number(p.observed)}}</td><td>{{number(p.prediction)}}</td></tr></tbody></table><p class="note">界面最多展示前 20 个预测；完整记录保留在工作区数据库。</p><pre>{{b.code}}</pre></details>
      </article></div>
    </article>
    <p class="note">这是遵循 fit/predict 契约的实验执行器，不声称防御任意恶意 Python 偷读数据。负对照异常需复核；低分或超时不证明方法无效。</p>
  </section>
</template>
<script setup lang="ts">
import {ref,computed,watch} from 'vue'
import {comparisonAPI,type ComparisonState,type ComparisonSpec} from '@/api/comparisons'
import {useStudioCopy} from '@/lang/studio'
const props=defineProps<{workId:string;disabled?:boolean;refreshKey?:number}>(),{isZh}=useStudioCopy()
const state=ref<ComparisonState|null>(null),busy=ref(false),error=ref(''),notice=ref(''),enabled=ref(false),manual=ref(false)
const dataset=ref(''),target=ref(''),features=ref(''),idColumn=ref(''),splitColumn=ref(''),taskType=ref<'regression'|'binary'>('regression'),metric=ref<'MAE'|'RMSE'|'AUC'>('RMSE'),split=ref<'random'|'group'|'time'>('random'),seed=ref(42),fraction=ref(.2),minimum=ref(0),tie=ref(.000001)
const columns=computed(()=>Object.keys(state.value?.files.find(f=>f.path===dataset.value)?.columns||{}))
const number=(v:unknown)=>typeof v==='number'?v.toPrecision(6):'—'
const budgetLabel=(v:string)=>({model_calls:'比较模型调用',executions:'候选执行',candidates:'候选',timeout_seconds:'预留超时秒数'}[v]||v)
async function load(){busy.value=true;error.value='';try{state.value=await comparisonAPI.read(props.workId);enabled.value=state.value.policy.enabled;const p=state.value.policy.spec;manual.value=!!p;if(p){dataset.value=p.dataset;target.value=p.target;features.value=p.features.join(', ');idColumn.value=p.id_column||'';taskType.value=p.task_type;metric.value=p.metric;split.value=p.split;splitColumn.value=p.split_column||'';seed.value=p.seed;fraction.value=p.validation_fraction;minimum.value=p.minimum_improvement;tie.value=p.tie_tolerance}}catch(e){error.value=e instanceof Error?e.message:'比较暂不可用'}finally{busy.value=false}}
async function save(){busy.value=true;error.value='';try{const spec:ComparisonSpec|null=manual.value?{dataset:dataset.value,target:target.value,features:features.value.split(',').map(v=>v.trim()).filter(Boolean),id_column:idColumn.value||null,task_type:taskType.value,metric:metric.value,split:split.value,split_column:splitColumn.value||null,seed:seed.value,validation_fraction:fraction.value,minimum_improvement:minimum.value,tie_tolerance:tie.value}:null;await comparisonAPI.policy(props.workId,enabled.value,spec);notice.value='配置已保存；已完成的比较不会重跑，预算不会清零。';await load()}catch(e){error.value=e instanceof Error?e.message:'保存失败'}finally{busy.value=false}}
watch(()=>props.workId,()=>{state.value=null;notice.value='';load()},{immediate:true});watch(()=>props.refreshKey,()=>load())
</script>
<style scoped>
.comparison-panel{padding:24px;overflow:auto;flex:1;color:var(--pa-ink)}header,.budget{display:flex;gap:12px;align-items:center;flex-wrap:wrap}h2{font-size:20px}.record,.branch{padding:16px;border:1px solid var(--pa-border);border-radius:12px;margin:12px 0;overflow-wrap:anywhere}.branches{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr));gap:12px}.note{font-size:12px;color:var(--pa-ink-muted)}form{display:flex;flex-direction:column;gap:10px;padding:12px 0}button{padding:6px 10px;cursor:pointer;align-self:start}input,select{padding:5px;max-width:100%}pre{white-space:pre-wrap;max-height:320px;overflow:auto;font-size:12px}p{margin:8px 0}table{font-size:12px;width:100%;text-align:left}
</style>
