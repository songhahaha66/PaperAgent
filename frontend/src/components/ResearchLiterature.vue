<template>
  <section class="literature-panel">
    <header>
      <h2>{{ isZh ? '文献与原文证据' : 'Literature and source evidence' }}</h2>
      <button @click="load">{{ isZh ? '刷新' : 'Refresh' }}</button>
    </header>
    <p class="note">
      {{
        isZh
          ? '文献不等于本研究实验结果。引用句必须经原文支持核查；未获取的全文和无法支持的结论会保留缺口。'
          : 'Literature is separate from your experimental results. Claims need source support; missing full text and unsupported claims stay explicit.'
      }}
    </p>
    <p v-if="error" role="alert">{{ error }}</p>
    <form @submit.prevent="runSearch">
      <input
        v-model="query"
        :placeholder="isZh ? '研究问题或学术检索词…' : 'Research question or scholarly keywords…'"
        :disabled="busy"
      /><button :disabled="busy || query.trim().length < 2">
        {{
          busy
            ? isZh
              ? '处理中…'
              : 'Working…'
            : isZh
              ? '检索公开文献'
              : 'Search public literature'
        }}
      </button>
    </form>
    <label
      >{{ isZh ? '研究前检索' : 'Search before design' }}
      <select
        :value="state?.mode || 'on_request'"
        :disabled="busy"
        @change="setMode(($event.target as HTMLSelectElement).value)"
      >
        <option value="on_request">
          {{ isZh ? '题目明确要求检索时' : 'When explicitly requested' }}
        </option>
        <option value="auto">
          {{ isZh ? '自动发送研究标题并检索' : 'Automatically search the research title' }}
        </option>
        <option value="off">{{ isZh ? '仅手动检索' : 'Manual search only' }}</option>
      </select></label
    >
    <details>
      <summary>{{ isZh ? '导入公开原文 URL' : 'Import a public source URL' }}</summary>
      <form @submit.prevent="importURL">
        <input v-model="url" placeholder="https://…" /><button :disabled="busy || !url.trim()">
          {{ isZh ? '读取公开来源' : 'Read public source' }}
        </button>
      </form>
    </details>
    <details v-if="state?.operations.length">
      <summary>{{ isZh ? '检索与获取记录' : 'Search and access log' }}</summary>
      <p v-for="(o, i) in state.operations" :key="i">
        {{ o.query }} · {{ o.status }}<span v-if="o.error"> · {{ o.error }}</span
        ><span v-for="e in o.errors || []" :key="e.provider">
          · {{ e.provider }}: {{ e.message }}</span
        >
      </p>
    </details>
    <div class="literature-grid">
      <aside>
        <p v-if="!state?.sources.length">
          {{
            isZh
              ? '尚未登记文献。可检索题目、导入公开 URL，或在研究问题中明确提出文献检索。'
              : 'No literature yet. Search a topic, import a public URL, or request literature search in the research question.'
          }}
        </p>
        <article
          v-for="s in state?.sources || []"
          :key="s.id"
          :class="{ selected: selectedSource === s.id }"
        >
          <button class="source-title" @click="showSource(s.id)">{{ s.id }} · {{ s.title }}</button>
          <p>
            {{ s.authors?.join(', ') || (isZh ? '作者未提供' : 'Authors unavailable') }} ·
            {{ s.year || (isZh ? '年份未提供' : 'Year unavailable') }}
          </p>
          <a :href="s.url" target="_blank" rel="noopener noreferrer">{{
            s.doi || (isZh ? '来源链接' : 'Source')
          }}</a>
          <p>
            {{ statusLabel(s.status) }} · {{ s.evidence_count || 0 }}
            {{ isZh ? '项摘录' : 'passages' }}
          </p>
          <p v-if="s.error" role="alert">{{ s.error }}</p>
          <button :disabled="busy" @click="fetchSource(s.id)">
            {{ isZh ? '获取原文 / 重试' : 'Fetch source / retry' }}
          </button>
        </article>
      </aside>
      <div class="evidence-pane">
        <form @submit.prevent="retrieve">
          <input
            v-model="localQuery"
            :placeholder="isZh ? '在已获取摘录中检索…' : 'Find acquired evidence…'"
          /><button :disabled="busy || localQuery.length < 2">
            {{ isZh ? '检索摘录' : 'Find evidence' }}
          </button>
        </form>
        <p v-if="showEmpty">
          {{
            isZh
              ? '没有匹配的原文摘录，不能据此支持结论。'
              : 'No matching acquired passage; it cannot support a claim.'
          }}
        </p>
        <article v-for="p in passages" :key="p.id">
          <button @click="selectEvidence(p)">
            {{ p.id }} · {{ boundaryLabel(p.boundary) }} · {{ p.locator }}
          </button>
          <blockquote>{{ p.text }}</blockquote>
          <p v-if="p.truncated" class="note">
            {{
              isZh
                ? '已按预算截取，不能视为完整全文。'
                : 'Budget-limited extract; not the complete full text.'
            }}
          </p>
          <a :href="p.source_url" target="_blank" rel="noopener noreferrer">{{
            isZh ? '核对原始来源' : 'Verify original source'
          }}</a>
          <p class="note">{{ p.license }}</p>
          <button :disabled="busy" @click="$emit('cite', p.id)">
            {{ isZh ? '在论文中引用' : 'Cite in paper' }}
          </button>
        </article>
        <section v-if="selected" class="support-check">
          <h3>{{ selected.id }} · {{ isZh ? '检查结论是否获支持' : 'Check claim support' }}</h3>
          <p>
            {{ boundaryLabel(selected.boundary) }} · {{ selected.locator }} ·
            {{ selected.retrieved_at }}
          </p>
          <blockquote>{{ selected.text }}</blockquote>
          <textarea
            v-model="claim"
            :placeholder="
              isZh ? '输入需要由这段原文支持的结论…' : 'Enter a claim for this passage to support…'
            "
          /><button :disabled="busy || claim.trim().length < 10" @click="checkClaim">
            {{ isZh ? '核查支持范围' : 'Check support' }}
          </button>
          <p v-if="check" :class="check.status">{{ check.status }} · {{ check.reason }}</p>
          <blockquote v-if="check?.supporting_quote">{{ check.supporting_quote }}</blockquote>
          <p class="note">
            {{
              isZh
                ? '这是辅助支持核查，不把摘要扩充为全文证据；信息不足时禁止当作已验证引用应用。'
                : 'Assisted support check. Abstracts remain abstracts; insufficient evidence cannot be applied as a verified citation.'
            }}
          </p>
          <h4>{{ isZh ? '正文引用回溯' : 'Citations in the paper' }}</h4>
          <p v-for="(c, i) in currentUsage" :key="i">
            {{ c.section }} · {{ c.status }} · {{ c.claim }} <span>{{ c.reason }}</span>
          </p>
          <p v-if="!currentUsage.length">
            {{ isZh ? '正文尚未采用这条证据。' : 'Not yet cited in the paper.' }}
          </p>
        </section>
      </div>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  literatureAPI,
  type LiteratureState,
  type LiteratureEvidence,
  type CitationCheck,
} from '@/api/literature'
import { useStudioCopy } from '@/lang/studio'
const props = defineProps<{ workId: string; disabled?: boolean; evidenceId?: string; refreshKey?: number }>()
const emit = defineEmits<{ updated: []; cite: [id: string] }>()
const { isZh } = useStudioCopy()
const state = ref<LiteratureState | null>(null),
  query = ref(''),
  url = ref(''),
  localQuery = ref(''),
  error = ref(''),
  working = ref(false)
const selectedSource = ref(''),
  passages = ref<LiteratureEvidence[]>([]),
  selected = ref<LiteratureEvidence | null>(null),
  claim = ref(''),
  check = ref<CitationCheck | null>(null),
  showEmpty = ref(false)
const busy = computed(() => working.value || props.disabled)
const currentUsage = computed(() =>
  (state.value?.citations || []).filter((c) => c.evidence_id === selected.value?.id),
)
const boundaryLabel = (b: string) =>
  ({
    abstract: isZh.value ? '摘要' : 'Abstract',
    fulltext_xml: isZh.value ? '开放全文' : 'Open full text',
    fulltext_pdf: isZh.value ? '公开 PDF' : 'Public PDF',
    webpage: isZh.value
      ? '公开网页（不保证论文全文）'
      : 'Public page (not necessarily paper full text)',
  })[b] || b
const statusLabel = (s: string) =>
  s === 'metadata_only'
    ? isZh.value
      ? '仅元数据，暂无可引用摘录'
      : 'Metadata only; no citable passage'
    : s
async function action(fn: () => Promise<void>) {
  working.value = true
  error.value = ''
  try {
    await fn()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    working.value = false
  }
}
async function load() {
  await action(async () => {
    state.value = await literatureAPI.read(props.workId)
    if (!query.value) query.value = state.value.query_suggestion || ''
  })
}
async function runSearch() {
  await action(async () => {
    await literatureAPI.search(props.workId, query.value)
    state.value = await literatureAPI.read(props.workId)
    emit('updated')
  })
}
async function setMode(mode: string) {
  await action(async () => {
    await literatureAPI.policy(props.workId, mode)
    if (state.value) state.value.mode = mode
  })
}
async function importURL() {
  await action(async () => {
    await literatureAPI.import(props.workId, url.value)
    state.value = await literatureAPI.read(props.workId)
    emit('updated')
  })
}
async function showSource(id: string) {
  await action(async () => {
    const s = await literatureAPI.source(props.workId, id)
    selectedSource.value = id
    passages.value = s.evidence
    showEmpty.value = !s.evidence.length
  })
}
async function fetchSource(id: string) {
  await action(async () => {
    await literatureAPI.fetch(props.workId, id)
    state.value = await literatureAPI.read(props.workId)
    const s = await literatureAPI.source(props.workId, id)
    selectedSource.value = id
    passages.value = s.evidence
    showEmpty.value = !s.evidence.length
    emit('updated')
  })
}
async function retrieve() {
  await action(async () => {
    passages.value = (await literatureAPI.retrieve(props.workId, localQuery.value)).evidence
    showEmpty.value = !passages.value.length
  })
}
function selectEvidence(p: LiteratureEvidence) {
  selected.value = p
  check.value = null
}
async function checkClaim() {
  if (selected.value)
    await action(async () => {
      check.value = await literatureAPI.check(props.workId, selected.value!.id, claim.value)
    })
}
watch(
  () => props.workId,
  () => {
    state.value = null
    query.value = ''
    passages.value = []
    selected.value = null
    load()
  },
  { immediate: true },
)
watch(
  () => props.refreshKey,
  () => load(),
)
watch(
  () => props.evidenceId,
  async (id) => {
    if (id)
      await action(async () => {
        state.value = await literatureAPI.read(props.workId)
        const p = await literatureAPI.evidence(props.workId, id)
        selected.value = p
        selectedSource.value = p.source_id
        passages.value = [p]
      })
  },
  { immediate: true },
)
</script>
<style scoped>
.literature-panel {
  padding: 24px;
  overflow: auto;
  flex: 1;
  color: var(--pa-ink);
}
header,
form {
  display: flex;
  gap: 10px;
  align-items: center;
}
h2 {
  font-size: 20px;
}
input,
textarea,
select,
button {
  font: inherit;
  color: inherit;
  background: var(--pa-surface);
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-control);
  padding: 9px;
}
input {
  flex: 1;
  min-width: 0;
}
textarea {
  width: 100%;
  min-height: 90px;
  box-sizing: border-box;
}
form,
label,
details {
  margin: 14px 0;
}
button {
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: wait;
}
.literature-grid {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(280px, 1.5fr);
  gap: 18px;
}
article,
.support-check {
  padding: 14px;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-card);
  margin: 12px 0;
  overflow-wrap: anywhere;
}
.source-title {
  text-align: left;
  font-weight: 600;
  border: 0;
}
.selected {
  border-color: var(--pa-accent);
}
blockquote {
  margin: 12px 0;
  padding-left: 12px;
  border-left: 3px solid var(--pa-line);
  white-space: pre-wrap;
  line-height: 1.7;
}
.note {
  color: var(--pa-muted);
  font-size: 12px;
  line-height: 1.6;
}
[role='alert'],
.unsupported,
.insufficient {
  color: var(--pa-danger);
}
.supported {
  color: var(--pa-accent);
}
@media (max-width: 900px) {
  .literature-grid {
    grid-template-columns: 1fr;
  }
  form {
    flex-wrap: wrap;
  }
}
</style>
