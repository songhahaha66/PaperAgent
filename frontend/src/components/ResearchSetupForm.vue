<template>
  <details class="research-setup"><summary>研究设置 · 文献、经验与方案比较</summary>
    <div class="setup-grid">
      <label><input type="checkbox" v-model="value.reuse_experience" :disabled="disabled" />复用本人真实研究经验</label>
      <label><input type="checkbox" v-model="value.confirm_models" :disabled="disabled" />设计后由我确认</label>
      <label><input type="checkbox" v-model="value.compact_paper" :disabled="disabled" />简短论文</label>
      <label>模型请求上限 <input type="number" v-model.number="value.request_limit" min="1" max="40" :disabled="disabled" /></label>
      <label>每次请求超时（秒）<input type="number" v-model.number="value.timeout_seconds" min="10" max="120" :disabled="disabled" /></label>
      <label>文献检索 <select v-model="value.literature_mode" :disabled="disabled"><option value="on_request">题目明确要求时</option><option value="auto">自动检索研究标题</option><option value="off">仅手动登记和检索</option></select></label>
      <label>开始前检索词（可空）<input v-model="value.literature_query" :disabled="disabled" placeholder="例如 Ridge regression" /></label>
      <label>公开资料 URL（可空，每行一个）<textarea v-model="urls" :disabled="disabled" @input="value.source_urls=urls.split('\n').map(s=>s.trim()).filter(Boolean)" /></label>
      <label>详细科研技能 <select v-model="skill" :disabled="disabled" @change="value.skill_keys=skill?[skill]:[]"><option value="">自动选择全部方法</option><option value="regression">仅回归建模</option><option value="linear_programming">仅线性规划</option><option value="arima">仅 ARIMA</option><option value="pca">仅 PCA</option><option value="clustering">仅聚类</option><option value="monte_carlo">仅蒙特卡洛</option></select></label>
      <label><input type="checkbox" v-model="value.compare" :disabled="disabled" />双方案比较（CSV 监督学习）</label>
      <template v-if="value.compare">
        <label>CSV 文件 <select v-model="dataset" :disabled="disabled"><option value="">自动按列类型建议协议</option><option v-for="f in csvFiles" :key="f" :value="f">{{f}}</option></select></label>
        <template v-if="dataset">
          <label>目标列 <input v-model="target" :disabled="disabled" required /></label><label>特征列（逗号分隔）<input v-model="features" :disabled="disabled" required /></label><label>样本 ID 列（可空）<input v-model="idColumn" :disabled="disabled" /></label>
          <label>任务 <select v-model="kind" :disabled="disabled"><option value="regression">回归 RMSE</option><option value="binary">二分类 AUC</option></select></label>
          <label>seed <input type="number" v-model.number="seed" :disabled="disabled" /></label>
        </template>
      </template>
    </div><p>配置在发起前保存；请求预算跨暂停/恢复累计，不因重试清零。比较和引用都要经过实际证据检查。已上传 CSV 可直接用于比较。</p>
  </details>
</template>
<script setup lang="ts">
import {ref,watch} from 'vue'
import type {ResearchSetup} from '@/api/researchSetup'
const props=defineProps<{csvFiles:string[];disabled?:boolean}>()
const value=defineModel<ResearchSetup>({required:true})
const urls=ref(''),skill=ref(''),dataset=ref(''),target=ref('target'),features=ref(''),idColumn=ref(''),kind=ref<'regression'|'binary'>('regression'),seed=ref(42)
watch([dataset,target,features,idColumn,kind,seed,()=>value.value.compare],()=>{value.value.comparison_spec=dataset.value&&value.value.compare?{dataset:dataset.value,target:target.value,features:features.value.split(',').map(s=>s.trim()).filter(Boolean),id_column:idColumn.value||null,task_type:kind.value,metric:kind.value==='binary'?'AUC':'RMSE',seed:seed.value,split:'random',validation_fraction:.2,minimum_improvement:0,tie_tolerance:.000001}:null})
watch(()=>props.csvFiles,files=>{if(dataset.value&&!files.includes(dataset.value))dataset.value=''}, {deep:true})
</script>
<style scoped>.research-setup{padding:12px 16px;border-top:1px solid var(--pa-line);font-size:13px}.setup-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:12px}label{display:flex;gap:8px;align-items:center;flex-wrap:wrap}input:not([type=checkbox]),select,textarea{max-width:100%;padding:5px;border:1px solid var(--pa-line);border-radius:var(--pa-radius-control)}textarea{width:100%;min-height:55px}p{margin-top:10px;color:var(--pa-muted)}</style>
