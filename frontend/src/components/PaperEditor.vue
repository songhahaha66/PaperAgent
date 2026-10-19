<template>
  <section class="paper-editor">
    <header>
      <strong>{{ isZh ? '论文工作台' : 'Paper workbench' }}</strong>
      <button @click="load">{{ isZh ? '刷新正文' : 'Refresh' }}</button>
      <button @click="$emit('open-file', 'paper.tex')" v-if="latex">LaTeX</button>
      <button @click="$emit('open-file', 'paper.pdf')" v-if="latex">PDF</button>
      <button @click="$emit('artifact')">{{ isZh ? '排版预览' : 'Preview' }}</button>
    </header>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="!document && !error">{{ isZh ? '正在读取章节…' : 'Loading sections…' }}</p>
    <template v-if="document">
      <label
        >{{ isZh ? '选择章节' : 'Section' }}
        <select v-model="selected" :disabled="busy">
          <option v-for="s in document.sections" :key="s.key" :value="s.key">
            {{ s.title }} · v{{ s.revision }}
          </option>
        </select>
      </label>
      <template v-if="section">
        <p>
          {{
            section.validity === 'current'
              ? isZh
                ? '当前版本'
                : 'Current'
              : isZh
                ? '证据已失效，请先重算'
                : 'Evidence is stale'
          }}
          · {{ section.task_refs.join(', ') }}
        </p>
        <div class="section-body" @mouseup="captureSelection">
          <MarkdownRenderer :content="section.readable || section.text" :work-id="workId" />
        </div>
        <button @click="ask">
          {{ isZh ? '追问所选内容 / 本节依据' : 'Ask about selection / evidence' }}
        </button>
        <label
          >{{ isZh ? '修改类型' : 'Edit type' }}
          <select v-model="mode" :disabled="busy">
            <option value="wording">
              {{ isZh ? '措辞润色（仅本节）' : 'Wording (this section)' }}
            </option>
            <option value="research">
              {{ isZh ? '数据、方法或结论' : 'Data, method or conclusions' }}
            </option>
          </select>
        </label>
        <textarea
          v-model="instruction"
          :placeholder="isZh ? '描述修改要求…' : 'Describe the change…'"
          :disabled="busy"
        />
        <template v-if="mode === 'research'">
          <p>
            {{
              isZh
                ? '预计受影响任务（含下游依赖，实际范围由研究规划确认）：'
                : 'Expected impact, including downstream dependencies:'
            }}
          </p>
          <ul>
            <li v-for="t in affected" :key="t.id">{{ t.id }} — {{ t.title }}</li>
          </ul>
          <button :disabled="busy || !instruction.trim()" @click="research">
            {{ isZh ? '提交定向修改并局部重算' : 'Revise research and recompute' }}
          </button>
        </template>
        <template v-else>
          <label
            ><input type="checkbox" v-model="manual" :disabled="busy" />{{
              isZh ? '直接编辑候选正文' : 'Edit candidate manually'
            }}</label
          >
          <textarea v-if="manual" class="candidate" v-model="draft" :disabled="busy" />
          <button :disabled="busy || (!manual && !instruction.trim())" @click="propose">
            {{ busy ? (isZh ? '处理中…' : 'Working…') : isZh ? '生成修改对比' : 'Preview changes' }}
          </button>
        </template>
        <section v-if="proposal" class="proposal">
          <h3>{{ isZh ? '修改前后对比' : 'Review changes' }} · {{ proposal.status }}</h3>
          <p v-for="issue in proposal.citation_issues || []" :key="issue" role="alert">{{ issue }}</p>
          <p v-for="(check,i) in proposal.citation_checks || []" :key="i">{{ check.status }} · {{ check.reason }}</p>
          <div class="diff">
            <div>
              <h4>{{ isZh ? '修改前' : 'Before' }}</h4>
              <pre>{{ proposal.before }}</pre>
            </div>
            <div>
              <h4>{{ isZh ? '修改后' : 'After' }}</h4>
              <pre>{{ proposal.after }}</pre>
            </div>
          </div>
          <p>
            {{
              isZh
                ? '请核对研究事实和结论强度。应用后保存版本历史并重新导出。'
                : 'Check facts and conclusion strength. Applying saves history and rebuilds the export.'
            }}
          </p>
          <button v-if="proposal.status === 'pending'" :disabled="busy || !!proposal.citation_issues?.length" @click="decide('apply')">
            {{ isZh ? '应用并更新论文' : 'Apply and update paper' }}
          </button>
          <button v-if="proposal.status === 'pending'" :disabled="busy" @click="decide('reject')">
            {{ isZh ? '拒绝' : 'Reject' }}
          </button>
        </section>
      </template>
      <details>
        <summary>
          {{ isZh ? '版本与候选历史' : 'Version and candidate history' }} ({{
            document.history.length
          }})
        </summary>
        <button v-for="p in document.history" :key="p.id" @click="proposal = p">
          {{ p.section }} · v{{ p.section_revision }} · {{ p.status }}
        </button>
      </details>
    </template>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { paperAPI, type PaperDocument, type PaperProposal } from '@/api/paper'
import { useStudioCopy } from '@/lang/studio'
import MarkdownRenderer from './MarkdownRenderer.vue'
const props = defineProps<{
  workId: string
  latex: boolean
  refreshKey?: number
  disabled?: boolean
  citationRequest?: {id:string;at:number}|null
}>()
const emit = defineEmits<{
  'open-file': [path: string]
  artifact: []
  updated: []
  ask: [message: string, task?: string]
  research: [message: string, task?: string]
}>()
const { isZh } = useStudioCopy()
const document = ref<PaperDocument | null>(null),
  selected = ref(''),
  instruction = ref(''),
  mode = ref('wording'),
  manual = ref(false),
  draft = ref(''),
  error = ref(''),
  working = ref(false),
  proposal = ref<PaperProposal | null>(null),
  selection = ref('')
const draftCommit = ref(0)
watch(()=>props.citationRequest,request=>{
  if(!request)return
  mode.value='wording';manual.value=false
  instruction.value=`判断文献证据 {{C:${request.id}}} 是否支持本节的背景或方法。支持时仅补充有来源的定性观点并引用此标记，保留全部实验结果、数字、公式和原有引用；不支持时明确说明缺口，不添加引用。`
},{immediate:true})
const busy = computed(() => working.value || props.disabled)
const section = computed(() => document.value?.sections.find((s) => s.key === selected.value))
const affected = computed(() => {
  const tasks = document.value?.tasks || [],
    ids = new Set(section.value?.task_refs || [])
  let changed = true
  while (changed) {
    changed = false
    for (const task of tasks)
      if (!ids.has(task.id) && task.dependencies.some((id) => ids.has(id))) {
        ids.add(task.id)
        changed = true
      }
  }
  return tasks.filter((t) => ids.has(t.id))
})
watch(section, (s, previous) => {
  if (manual.value && s?.key === previous?.key) return
  draftCommit.value = document.value?.commit_seq || 0
  draft.value = s?.text || ''
  selection.value = ''
})
function captureSelection() {
  selection.value = window.getSelection()?.toString().slice(0, 4000) || ''
}
function ask() {
  emit(
    'ask',
    `请解释「${section.value?.title}」中${selection.value || '主要结论和图表'}的依据，列出证据、计算记录及局限。`,
    section.value?.task_refs.length === 1 ? section.value.task_refs[0] : undefined,
  )
}
function research() {
  emit(
    'research',
    `针对章节「${section.value?.title}」：${instruction.value}`,
    section.value?.task_refs.length === 1 ? section.value.task_refs[0] : undefined,
  )
}
function failure(e: unknown) {
  error.value = e instanceof Error ? e.message : String(e)
}
async function load() {
  try {
    error.value = ''
    document.value = await paperAPI.read(props.workId)
    if (!document.value.sections.some((s) => s.key === selected.value))
      selected.value = document.value.sections[0]?.key || ''
  } catch (e) {
    failure(e)
  }
}
async function propose() {
  if (!document.value || !section.value || busy.value) return
  working.value = true
  error.value = ''
  try {
    proposal.value = await paperAPI.propose(props.workId, {
      research_id: document.value.research_id,
      base_commit: manual.value ? draftCommit.value : document.value.commit_seq,
      section: selected.value,
      instruction: instruction.value,
      ...(manual.value ? { content: draft.value } : {}),
    })
    await load()
  } catch (e) {
    failure(e)
  } finally {
    working.value = false
  }
}
async function decide(decision: string) {
  if (!document.value || !proposal.value || busy.value) return
  working.value = true
  error.value = ''
  try {
    proposal.value = await paperAPI.decide(
      props.workId,
      proposal.value.id,
      document.value.research_id,
      decision,
    )
    await load()
    if (proposal.value.status === 'applied' && proposal.value.section === selected.value) {
      draft.value = section.value?.text || ''
      draftCommit.value = document.value?.commit_seq || 0
    }
    emit('updated')
  } catch (e) {
    failure(e)
  } finally {
    working.value = false
  }
}
watch(() => props.workId, load, { immediate: true })
watch(
  () => props.refreshKey,
  () => {
    if (!working.value) load()
  },
)
</script>
<style scoped>
.paper-editor {
  padding: 24px;
  overflow: auto;
  flex: 1;
  color: var(--pa-ink);
}
header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 20px;
}
label {
  display: block;
  margin: 16px 0 8px;
}
select,
textarea,
button {
  font: inherit;
  color: inherit;
  border: 1px solid var(--pa-line);
  background: var(--pa-surface);
  border-radius: var(--pa-radius-control);
  padding: 8px;
}
button {
  cursor: pointer;
  margin: 4px;
}
button:disabled {
  opacity: 0.5;
  cursor: wait;
}
textarea {
  display: block;
  width: 100%;
  min-height: 90px;
  box-sizing: border-box;
}
.candidate {
  min-height: 260px;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.8;
}
.section-body {
  max-height: 420px;
  overflow: auto;
  border-bottom: 1px solid var(--pa-line);
}
.diff {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.diff > div {
  min-width: 0;
  padding: 12px;
  background: var(--pa-surface);
  border: 1px solid var(--pa-line);
}
[role='alert'] {
  color: var(--pa-danger);
}
details {
  margin-top: 24px;
}
@media (max-width: 768px) {
  .diff {
    grid-template-columns: 1fr;
  }
}
</style>
