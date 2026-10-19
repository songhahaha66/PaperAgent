<template>
  <section class="paper-reader" :class="{ 'context-open': contextOpen }">
    <header v-if="document && (document.sections.length || pdfAvailable)" class="paper-toolbar">
      <button
        v-if="view !== 'pdf'"
        class="toc-toggle"
        :aria-pressed="tocOpen"
        @click="tocOpen = !tocOpen"
      >
        <t-icon name="view-list" />目录
      </button>
      <nav aria-label="论文阅读方式">
        <button :aria-pressed="view === 'body'" @click="setView('body')">正文</button>
        <button :aria-pressed="view === 'pdf'" @click="setView('pdf')">PDF</button>
        <button :aria-pressed="view === 'revisions'" @click="setView('revisions')">修订</button>
      </nav>
      <div class="paper-context-actions">
        <button
          :aria-pressed="contextOpen && contextTab === 'evidence'"
          @click="openContext('evidence')"
        >
          依据
        </button>
        <button
          :aria-pressed="contextOpen && contextTab === 'opinions'"
          @click="openContext('opinions')"
        >
          意见<span v-if="document?.feedback?.length"> {{ document.feedback.length }}</span>
        </button>
        <button
          :aria-pressed="contextOpen && contextTab === 'checks'"
          @click="openContext('checks')"
        >
          检查
        </button>
        <button :disabled="loading" aria-label="刷新论文" @click="load">
          <t-icon name="refresh" />
        </button>
      </div>
    </header>
    <p v-if="error" class="paper-error" role="alert">{{ error }}</p>
    <p v-if="document?.publication_notice" class="publication-note" role="status">
      {{ document.publication_notice }}
    </p>
    <div
      class="paper-reading"
      :class="{ 'without-toc': !tocOpen || view === 'pdf', 'with-context': contextOpen }"
    >
      <nav
        v-if="tocOpen && view !== 'pdf' && outline.length"
        class="paper-outline"
        aria-label="论文目录"
      >
        <strong>目录</strong>
        <button
          v-for="entry in outline"
          :key="entry.key"
          :class="{ nested: entry.level > 2, current: selected === entry.key }"
          :aria-current="selected === entry.key ? 'location' : undefined"
          :disabled="!outlineTarget(entry.key) || disabled"
          @click="navigate(entry.key)"
        >
          <span>{{ entry.title }}</span
          ><small v-if="!outlineTarget(entry.key)">待撰写</small>
        </button>
      </nav>
      <div class="paper-content">
        <div v-if="loading && !document" class="paper-empty">
          <t-loading text="正在读取论文…" />
        </div>
        <div v-else-if="!document?.sections.length && view !== 'pdf'" class="paper-empty">
          <t-icon name="file-1" />
          <h2>论文尚无正文</h2>
          <p>研究完成写作后，正文会按论文目录显示。</p>
        </div>
        <template v-else-if="view === 'pdf'">
          <WorkDocument
            v-if="pdfAvailable"
            :work-id="workId"
            path="paper.pdf"
            paper
            :available="true"
            :active="active !== false"
            :refresh-key="document?.commit_seq"
            :show-toolbar="false"
            @open-file="(path) => emit('open-file', path)"
          />
          <div v-else class="paper-empty">
            <t-icon name="file-pdf" />
            <h2>{{ pdfStatus.title }}</h2>
            <p>{{ pdfStatus.description }}</p>
            <button v-if="document?.sections.length" @click="setView('body')">阅读当前正文</button>
          </div>
        </template>
        <PaperRevision
          v-else-if="view === 'revisions'"
          :work-id="workId"
          :owner-id="ownerId"
          :latex="latex"
          :disabled="disabled"
          :section-key="selected"
          :fragment-request="fragmentRequest"
          :refresh-key="refreshKey"
          :citation-request="citationRequest"
          :issue-request="activeIssue"
          @open-file="(path) => emit('open-file', path)"
          @updated="updated"
          @ask="(message, task) => emit('ask', message, task)"
          @research="(message, task, issue) => emit('research', message, task, issue)"
          @section-change="(key) => (selected = key)"
          @read="setView('body')"
        />
        <div v-else ref="textScroller" class="paper-text" @scroll.passive="trackSection">
          <div v-if="document?.title || title" class="paper-document-title">
            <h1>{{ document?.title || title }}</h1>
          </div>
          <template v-for="entry in outline" :key="entry.key">
            <h2 v-if="!entry.available && outlineTarget(entry.key)" class="paper-group-heading">
              {{ entry.title }}
            </h2>
            <article
              v-if="sectionsByKey.get(entry.key)"
              class="paper-section"
              :data-paper-key="entry.key"
              @mouseup="capture(entry.key)"
              @click="selected = entry.key"
            >
              <header>
                <component :is="entry.level > 2 ? 'h3' : 'h2'">{{ entry.title }}</component
                ><span
                  v-if="sectionsByKey.get(entry.key)?.validity !== 'current'"
                  class="stale-note"
                  >依据已更新，等待重新核实</span
                >
              </header>
              <MarkdownRenderer
                :content="
                  sectionsByKey.get(entry.key)?.readable || sectionsByKey.get(entry.key)?.text || ''
                "
                :work-id="workId"
              />
              <div class="section-actions">
                <button @click.stop="revise(entry.key)">修改本节</button
                ><button @click.stop="showEvidence(entry.key)">依据与来源</button>
              </div>
            </article>
          </template>
        </div>
      </div>
      <aside v-if="contextOpen" class="paper-context" aria-label="论文相关内容">
        <header>
          <strong>{{
            contextTab === 'checks'
              ? '当前版本的检查'
              : contextTab === 'opinions'
                ? '正文意见'
                : '本节关联成果'
          }}</strong
          ><button aria-label="关闭相关内容" @click="contextOpen = false">
            <t-icon name="close" />
          </button>
        </header>
        <div class="context-content">
          <PaperChecks
            v-if="contextTab === 'checks'"
            :work-id="workId"
            :refresh-key="refreshKey"
            :section-titles="sectionTitles"
            @changed="updated"
          />
          <template v-else-if="contextTab === 'opinions'">
            <label class="all-feedback"
              ><input v-model="allFeedback" type="checkbox" />查看全文意见</label
            >
            <p class="context-note" v-if="!allFeedback">{{ selectedSection?.title }}</p>
            <article v-for="issue in feedback" :key="issue.id" class="context-item">
              <p>{{ issue.message }}</p>
              <small>{{
                issue.section ? sectionTitles[issue.section] || '相关章节' : '相关研究内容'
              }}</small
              ><button :disabled="disabled" @click="openIssue(issue.id)">定位并处理</button>
            </article>
            <p v-if="!feedback.length" class="context-note">
              {{ allFeedback ? '当前没有待处理的正文意见。' : '当前章节没有待处理意见。' }}
            </p>
          </template>
          <template v-else>
            <p class="context-note">{{ selectedSection?.title }} · 与当前章节关联的研究记录</p>
            <article v-for="result in evidence" :key="result.id" class="context-item">
              <h3>{{ result.name }}</h3>
              <p>{{ result.value }} {{ result.unit }}</p>
              <button v-if="result.figure_path" @click="emit('open-file', result.figure_path)">
                打开图表</button
              ><button
                v-if="resultOutput(result.run_id)"
                @click="emit('open-file', resultOutput(result.run_id)!)"
              >
                查看计算记录
              </button>
            </article>
            <p v-if="!evidence.length" class="context-note">
              本节尚未关联可查看的登记成果。文献引用可从正文链接查看实际来源。
            </p>
            <button v-if="selectedSection" class="context-ask" @click="ask">
              追问本节依据与局限
            </button>
            <div v-if="document?.artifacts?.length" class="context-item">
              <h3>论文文件</h3>
              <button v-for="file in sourceArtifacts" :key="file" @click="emit('open-file', file)">
                {{
                  file.endsWith('.tex')
                    ? 'LaTeX 源文件'
                    : file.endsWith('.docx')
                      ? 'Word 文稿'
                      : 'Markdown 正文'
                }}
              </button>
            </div>
          </template>
        </div>
      </aside>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { paperAPI, type PaperDocument } from '@/api/paper'
import type { ResearchSnapshot } from '@/types/research'
import PaperRevision from './PaperRevision.vue'
import PaperChecks from './PaperChecks.vue'
import MarkdownRenderer from './MarkdownRenderer.vue'
import WorkDocument from './WorkDocument.vue'
const props = defineProps<{
  workId: string
  ownerId?: number | string
  latex: boolean
  title?: string
  active?: boolean
  snapshot?: ResearchSnapshot | null
  refreshKey?: number
  disabled?: boolean
  citationRequest?: { id: string; at: number } | null
  issueRequest?: { id: string; at: number } | null
}>()
const emit = defineEmits<{
  'open-file': [path: string]
  updated: []
  artifact: []
  ask: [message: string, task?: string]
  research: [message: string, task?: string, sourceIssue?: { id: string; version: string }]
}>()
type View = 'body' | 'pdf' | 'revisions'
const document = ref<PaperDocument | null>(null),
  loading = ref(false),
  error = ref(''),
  selected = ref('')
const view = ref<View>('body'),
  tocOpen = ref(true),
  contextOpen = ref(false),
  allFeedback = ref(false)
const contextTab = ref<'evidence' | 'opinions' | 'checks'>('evidence')
const activeIssue = ref<{ id: string; at: number } | null>(null)
const fragmentRequest = ref<{ section: string; text: string; at: number } | null>(null)
const textScroller = ref<HTMLElement | null>(null)
let generation = 0,
  viewChosen = false,
  pendingScroll = 0
const locationKey = () =>
  `paperagent:paper-drafts:${props.ownerId || 'session'}:${props.workId}:location`
const sectionsByKey = computed(() => new Map(document.value?.sections.map((s) => [s.key, s]) || []))
const selectedSection = computed(() => sectionsByKey.value.get(selected.value))
const outline = computed(() => {
  const declared =
    document.value?.outline ||
    document.value?.sections.map((s) => ({
      key: s.key,
      title: s.title,
      level: s.level || 2,
      available: true,
    })) ||
    []
  const known = new Set(declared.map((entry) => entry.key))
  return [
    ...declared,
    ...(document.value?.sections || [])
      .filter((s) => !known.has(s.key))
      .map((s) => ({ key: s.key, title: s.title, level: s.level || 2, available: true })),
  ]
})
const sectionTitles = computed(() =>
  Object.fromEntries(outline.value.map((entry) => [entry.key, entry.title])),
)
const pdfAvailable = computed(() => !!document.value?.artifacts?.includes('paper.pdf'))
const sourceArtifacts = computed(
  () => document.value?.artifacts?.filter((name) => name !== 'paper.pdf') || [],
)
const pdfStatus = computed(() => {
  const stage = props.snapshot?.delivery_stages?.export
  if (stage?.status === 'running' || stage?.status === 'in_progress')
    return {
      title: 'PDF 正在排版',
      description: '可以先阅读当前正文；排版成功后在这里查看正式文件。',
    }
  if (stage?.status === 'failed' || stage?.status === 'blocked')
    return {
      title: 'PDF 排版尚未完成',
      description: stage.note || '查看研究进度中的运行记录，处理后可继续。正文仍然保留。',
    }
  return {
    title: 'PDF 尚未生成',
    description: props.latex
      ? '完成论文写作与排版后，这里显示正式 PDF。'
      : '本研究当前未生成 PDF 文件。下次新建研究可选择 LaTeX / PDF 交付。',
  }
})
const evidence = computed(
  () =>
    props.snapshot?.results.filter(
      (result) =>
        selectedSection.value?.evidence_refs.includes(result.id) ||
        selectedSection.value?.task_refs.includes(result.subproblem_id),
    ) || [],
)
const feedback = computed(() =>
  (document.value?.feedback || []).filter(
    (issue) =>
      allFeedback.value ||
      issue.section === selected.value ||
      issue.section === 'paper' ||
      (!issue.section && selectedSection.value?.task_refs.includes(issue.task_id)),
  ),
)
function resultOutput(id: string) {
  return props.snapshot?.runs.find((run) => run.id === id)?.stdout_path
}
function outlineTarget(key: string): string | undefined {
  if (sectionsByKey.value.has(key)) return key
  const index = outline.value.findIndex((entry) => entry.key === key),
    level = outline.value[index]?.level || 2
  for (let i = index + 1; i < outline.value.length; i++) {
    const entry = outline.value[i]!
    if (entry.level <= level) break
    if (sectionsByKey.value.has(entry.key)) return entry.key
  }
}
function setView(next: View) {
  view.value = next
  viewChosen = true
  try {
    sessionStorage.setItem(locationKey() + ':view', next)
  } catch {
    /* Storage is optional. */
  }
  if (next === 'body') void scrollToSelected()
}
async function navigate(key: string) {
  const target = outlineTarget(key)
  if (!target) return
  selected.value = target
  if (view.value === 'body') await scrollToSelected()
}
async function scrollToSelected() {
  await nextTick()
  const scroller = textScroller.value
  const element = Array.from(
    scroller?.querySelectorAll<HTMLElement>('[data-paper-key]') || [],
  ).find((item) => item.dataset.paperKey === selected.value)
  if (scroller && element)
    scroller.scrollTo({
      top:
        scroller.scrollTop +
        element.getBoundingClientRect().top -
        scroller.getBoundingClientRect().top -
        20,
    })
}
function trackSection() {
  if (pendingScroll || !textScroller.value) return
  pendingScroll = requestAnimationFrame(() => {
    pendingScroll = 0
    const scroller = textScroller.value
    if (!scroller) return
    const top = scroller.getBoundingClientRect().top + 90
    const entries = Array.from(scroller.querySelectorAll<HTMLElement>('[data-paper-key]'))
    const current =
      entries.filter((item) => item.getBoundingClientRect().top <= top).at(-1) || entries[0]
    if (current?.dataset.paperKey) selected.value = current.dataset.paperKey
  })
}
function capture(key: string) {
  selected.value = key
  const text = window.getSelection()?.toString() || ''
  fragmentRequest.value = text && text.length <= 600 ? { section: key, text, at: Date.now() } : null
}
function revise(key: string) {
  selected.value = key
  activeIssue.value = null
  setView('revisions')
}
function showEvidence(key: string) {
  selected.value = key
  contextTab.value = 'evidence'
  contextOpen.value = true
}
function openContext(tab: typeof contextTab.value) {
  contextOpen.value = contextTab.value === tab ? !contextOpen.value : true
  contextTab.value = tab
}
function openIssue(id: string) {
  activeIssue.value = { id, at: Date.now() }
  contextOpen.value = false
  setView('revisions')
}
function ask() {
  emit(
    'ask',
    `请解释「${selectedSection.value?.title}」的依据、计算记录与局限。`,
    selectedSection.value?.task_refs.length === 1 ? selectedSection.value.task_refs[0] : undefined,
  )
}
async function updated() {
  await load()
  emit('updated')
}
async function load() {
  const seq = ++generation,
    work = props.workId,
    initial = !document.value
  loading.value = true
  error.value = ''
  try {
    const value = await paperAPI.read(work)
    if (seq !== generation || props.workId !== work) return
    document.value = value
    if (!value.sections.some((section) => section.key === selected.value)) {
      let saved = ''
      try {
        saved = sessionStorage.getItem(locationKey()) || ''
      } catch {
        /* Storage is optional. */
      }
      selected.value =
        value.sections.find((section) => section.key === saved)?.key || value.sections[0]?.key || ''
    }
    if (!viewChosen && !activeIssue.value && !props.citationRequest) {
      let savedView = ''
      try {
        savedView = sessionStorage.getItem(locationKey() + ':view') || ''
      } catch {
        /* Storage is optional. */
      }
      view.value = ['body', 'pdf', 'revisions'].includes(savedView)
        ? (savedView as View)
        : value.artifacts?.includes('paper.pdf')
          ? 'pdf'
          : 'body'
    }
    if (initial && view.value === 'body' && selected.value !== value.sections[0]?.key)
      await scrollToSelected()
  } catch (e) {
    if (seq === generation) error.value = e instanceof Error ? e.message : '论文暂不可读，请重试。'
  } finally {
    if (seq === generation) loading.value = false
  }
}
watch(selected, (key) => {
  try {
    sessionStorage.setItem(locationKey(), key)
  } catch {
    /* Storage is optional. */
  }
})
watch(
  () => props.issueRequest,
  (request) => {
    if (request) openIssue(request.id)
  },
  { immediate: true },
)
watch(
  () => props.citationRequest,
  (request) => {
    if (request) setView('revisions')
  },
  { immediate: true },
)
watch(
  () => props.workId,
  () => {
    document.value = null
    selected.value = ''
    viewChosen = false
    void load()
  },
  { immediate: true },
)
watch(() => props.refreshKey, load)
onUnmounted(() => {
  generation++
  if (pendingScroll) cancelAnimationFrame(pendingScroll)
})
</script>
<style scoped>
.paper-reader {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
  color: var(--pa-ink);
  background: var(--pa-surface);
  container-type: inline-size;
  container-name: paper;
}
.paper-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--pa-line);
  flex-wrap: wrap;
  flex-shrink: 0;
}
.paper-toolbar nav,
.paper-context-actions {
  display: flex;
  align-items: center;
  gap: 3px;
}
.paper-context-actions {
  margin-left: auto;
}
button {
  font: inherit;
  color: var(--pa-muted);
  background: transparent;
  border: 0;
  border-radius: var(--pa-radius-control);
  padding: 7px 9px;
  cursor: pointer;
  font-size: 12px;
}
button:hover {
  background: var(--pa-surface-hover);
  color: var(--pa-ink);
}
button[aria-pressed='true'] {
  color: var(--pa-accent);
  background: var(--pa-surface-muted);
}
button:disabled {
  cursor: default;
  opacity: 0.55;
}
.toc-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
}
.paper-reading {
  display: grid;
  grid-template-columns: 160px minmax(0, 1fr);
  flex: 1;
  min-height: 0;
}
.paper-reading.without-toc {
  grid-template-columns: minmax(0, 1fr);
}
.paper-outline {
  border-right: 1px solid var(--pa-line);
  padding: 20px 10px;
  overflow: auto;
}
.paper-outline > strong {
  display: block;
  padding: 0 9px 12px;
  font-size: 12px;
  font-weight: 500;
  color: var(--pa-muted);
}
.paper-outline button {
  display: block;
  width: 100%;
  text-align: left;
  padding: 8px 10px;
  line-height: 1.65;
}
.paper-outline button.nested {
  padding-left: 22px;
}
.paper-outline button.current {
  color: var(--pa-accent);
  background: var(--pa-surface-muted);
}
.paper-outline small {
  display: block;
  font-size: 11px;
}
.paper-content {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.paper-text {
  padding: 28px 32px 80px;
  overflow: auto;
  scroll-behavior: auto;
}
.paper-section,
.paper-group-heading,
.paper-document-title {
  max-width: 740px;
  margin-inline: auto;
}
.paper-section {
  padding: 0 0 24px;
  margin-bottom: 28px;
  border-bottom: 1px solid var(--pa-line);
}
.paper-document-title {
  text-align: center;
  margin-bottom: 40px;
}
h1 {
  font-size: 24px;
  line-height: 1.6;
  font-weight: 500;
  margin: 0;
}
.paper-section h2,
.paper-group-heading {
  font-size: 20px;
  font-weight: 500;
  margin-top: 0;
  line-height: 1.6;
}
.paper-section h3 {
  font-size: 17px;
  font-weight: 500;
  margin-top: 0;
}
.paper-group-heading {
  margin-bottom: 28px;
}
.paper-section :deep(.markdown-body) {
  font-size: 15px;
  line-height: 1.95;
}
.section-actions {
  display: flex;
  gap: 10px;
  margin-top: 14px;
  opacity: 0.8;
}
.section-actions button {
  padding-left: 0;
  color: var(--pa-accent);
}
.stale-note {
  display: block;
  font-size: 12px;
  color: var(--pa-warning);
  padding-bottom: 12px;
}
.paper-empty {
  flex: 1;
  min-height: 280px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 28px;
  text-align: center;
  color: var(--pa-muted);
}
.paper-empty > .t-icon {
  font-size: 32px;
}
.paper-empty h2 {
  color: var(--pa-ink);
  font-weight: 500;
  font-size: 18px;
  margin: 0;
}
.paper-empty p {
  max-width: 380px;
  line-height: 1.8;
  margin: 0;
}
.paper-empty button {
  border: 1px solid var(--pa-line);
}
.paper-context {
  position: absolute;
  top: 56px;
  bottom: 0;
  right: 0;
  width: 310px;
  max-width: 100%;
  z-index: 10;
  background: var(--pa-surface);
  border-left: 1px solid var(--pa-line);
  box-shadow: var(--pa-shadow-overlay);
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.paper-context > header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid var(--pa-line);
  font-size: 13px;
}
.context-content {
  padding: 20px;
  overflow: auto;
}
.context-item {
  border-bottom: 1px solid var(--pa-line);
  padding: 18px 0;
  overflow-wrap: anywhere;
}
.context-item:first-child {
  padding-top: 0;
}
.context-item h3 {
  font-size: 14px;
  font-weight: 500;
  margin: 0;
}
.context-item p {
  font-size: 13px;
  line-height: 1.8;
}
.context-item small,
.context-note,
.all-feedback {
  font-size: 12px;
  color: var(--pa-muted);
  line-height: 1.8;
}
.context-item small {
  display: block;
}
.context-item button,
.context-ask {
  color: var(--pa-accent);
  padding-left: 0;
}
.all-feedback {
  display: flex;
  gap: 7px;
  align-items: center;
}
.paper-error {
  color: var(--pa-danger);
  font-size: 13px;
  padding: 0 18px;
}
.publication-note {
  color: var(--pa-warning);
  font-size: 12px;
  padding: 0 18px;
}
@container paper (min-width:1000px) {
  .paper-reading.with-context {
    grid-template-columns: 160px minmax(0, 1fr) 280px;
  }
  .paper-reading.with-context.without-toc {
    grid-template-columns: minmax(0, 1fr) 280px;
  }
  .paper-context {
    position: static;
    width: auto;
    box-shadow: none;
  }
}
@container paper (max-width:650px) {
  .paper-reading {
    grid-template-columns: 128px minmax(0, 1fr);
  }
  .paper-text {
    padding: 24px 20px 60px;
  }
  .paper-outline {
    padding-inline: 6px;
  }
  .paper-outline button {
    padding: 7px;
  }
  .paper-outline button.nested {
    padding-left: 16px;
  }
  h1 {
    font-size: 21px;
  }
}
@container paper (max-width:420px) {
  .paper-toolbar {
    gap: 3px;
    padding: 8px;
  }
  .paper-toolbar button {
    padding: 6px;
  }
  .paper-outline {
    position: absolute;
    left: 0;
    top: 91px;
    bottom: 0;
    width: 190px;
    z-index: 9;
    background: var(--pa-surface);
    box-shadow: var(--pa-shadow-overlay);
  }
  .paper-reading {
    grid-template-columns: minmax(0, 1fr);
  }
  .paper-context {
    top: 91px;
  }
}
</style>
