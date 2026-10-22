<template>
  <section class="paper-editor">
    <header class="toolbar">
      <nav aria-label="论文视图">
        <button :aria-pressed="view === 'read'" @click="setView('read')">
          {{ isLatex ? 'PDF 预览' : '阅读' }}</button
        ><button
          :aria-pressed="view === 'source'"
          :disabled="!project?.has_source"
          @click="setView('source')"
        >
          源码
        </button>
      </nav>
      <button v-if="isLatex && project?.has_source" :disabled="compiling" @click="compile">
        {{ compiling ? '正在编译…' : '编译 PDF' }}
      </button>
    </header>
    <p v-if="error && !side && !sourceCard" class="error" role="alert">{{ error }}</p>
    <p
      v-if="isLatex && project?.has_source"
      class="notice"
      :class="{ stale: project.compile.state !== 'current' }"
      role="status"
    >
      {{ project.compile.message }}
    </p>
    <div v-if="!project && loading" class="empty"><t-loading text="正在读取论文项目…" /></div>
    <div
      v-else-if="!project || (!project.has_source && (isLatex || !project.draft_sections?.length))"
      class="empty"
    >
      <h2>论文源码尚未生成</h2>
      <p>写作产生源码后，可在这里阅读、编辑和评论；无需等待整项研究审查完成。</p>
    </div>
    <div v-else class="workspace">
      <div ref="manuscript" class="manuscript">
        <template v-if="view === 'source'">
          <header class="file-toolbar">
            <span v-if="project.files.length === 1" class="source-label">论文正文</span>
            <select v-else v-model="file" :disabled="dirty" aria-label="项目文件">
              <option v-for="row in project.files" :key="row.path" :value="row.path">
                {{ row.path === project.main ? '论文正文' : row.path }}
              </option></select
            ><span v-if="dirty">有未保存的编辑</span
            ><button v-if="dirty" @click="discardDraft">放弃本地编辑</button
            ><button :disabled="!dirty || busy" @click="saveDraft">保存修改集</button
            ><button :disabled="!selection" @click="addToChat">评论选区</button
            ><button :disabled="!selectedFile" @click="$emit('open-file', file)">打开文件</button>
          </header>
          <div v-if="selectedFile?.text !== null" ref="sourceSurface" class="source-surface">
            <pre ref="sourceMirror" class="source-mirror" aria-hidden="true" :style="mirrorStyle">{{
              draft
            }}</pre>
            <textarea
              ref="sourceEditor"
              v-model="draft"
              class="source-editor"
              spellcheck="false"
              aria-label="论文源码"
              @mouseup="captureSource"
              @keyup="captureSource"
              @scroll="syncSourceScroll"
            ></textarea>
          </div>
          <p v-else class="empty">这是图片或其他二进制资源，可通过“打开文件”查看。</p>
          <p class="hint">选中文字可添加到对话。</p>
        </template>
        <div v-else-if="!isLatex && project.draft_sections?.length" class="read-text">
          <section
            v-for="section in project.draft_sections"
            :key="section.key"
            :data-section="section.key"
            :class="{
              'evidence-section':
                evidenceRequest?.section === section.key &&
                evidenceRequest?.section_version === section.content_hash,
            }"
          >
            <h2>{{ section.title }}</h2>
            <details
              v-for="issue in project.feedback?.filter((item) => item.section === section.key)"
              :key="issue.id"
              :data-issue="issue.id"
              :open="issue.id === issueRequest?.id"
              class="section-finding"
            >
              <summary>本节审查意见</summary>
              <p>{{ issue.message }}</p>
            </details>
            <p v-if="section.validity !== 'current'" class="notice">
              此节依据正在更新，当前文字仅供参考。
            </p>
            <MarkdownRenderer :content="section.readable" :work-id="workId" />
            <div v-if="section.evidence_refs.length" class="section-evidence">
              <span>本节实验依据</span
              ><button
                v-for="ref in section.evidence_refs.filter((id) =>
                  snapshot?.results.some((r) => r.id === id),
                )"
                :key="ref"
                @click="openResult(ref)"
              >
                {{ snapshot?.results.find((r) => r.id === ref)?.name || '查看实验' }} ↗
              </button>
            </div>
          </section>
        </div>
        <template v-else-if="isLatex">
          <PaperPdfReader
            v-if="project.pdf_available"
            :work-id="workId"
            :version="project.pdf_version || project.version"
            @selection="captureRead"
            @citation="openCitation"
            @rendered="refreshAnnotationRoot"
          />
          <div v-else class="empty">
            <h2>源码已保留，PDF 尚未生成</h2>
            <p>{{ project.compile.message }}</p>
            <button @click="setView('source')">查看和评论源码</button>
          </div>
        </template>
        <div
          v-else
          class="read-text"
          @mouseup="captureRendered"
          @keyup="captureRendered"
          @click="readClick"
        >
          <MarkdownRenderer
            :content="selectedFile?.text || ''"
            :work-id="workId"
            inline-sources
            @source="(id, target) => openCitation('#literature-' + id, target)"
          />
        </div>
      </div>
      <PaperTextAnnotations
        :root="annotationRoot"
        :clip="view === 'source' ? sourceSurface : manuscript"
        :annotations="dirty ? [] : textAnnotations"
        :selection-range="side === 'comments' ? activeRange : null"
        :selected-quote="side === 'comments' ? readQuote || selection?.quote : ''"
        @open="openAnnotation"
      />
      <PaperPopover
        :open="!!side"
        :title="side === 'comments' ? '评论与修改' : side === 'sources' ? '原文与来源' : '研究意见'"
        :anchor="popoverAnchor"
        :host="manuscript"
        :compact="quickComment"
        :width="quickComment ? 120 : 360"
        :tone="side === 'sources' ? 'source' : side === 'opinions' ? 'opinion' : activeMarkKind"
        @close="side = null"
      >
        <div class="annotation-content">
          <p v-if="error && !quickComment" class="error" role="alert">{{ error }}</p>
          <button
            v-if="quickComment"
            type="button"
            class="selection-action"
            @mousedown.prevent
            @click="addToChat"
          >
            添加到对话
          </button>
          <template v-else-if="side === 'comments'">
            <details class="comment-context" :open="!contextual">
              <summary>{{ selection || readQuote ? '所选原文' : '评论范围' }}</summary>
              <template v-if="selection"
                ><strong>{{ selectionLabel(selection) }}</strong>
                <blockquote>{{ selection.quote }}</blockquote>
                <button @click="selection = null">清除选区</button></template
              >
              <template v-else-if="readQuote"
                ><strong>阅读选区 · 待确认源码位置</strong>
                <blockquote>{{ readQuote }}</blockquote>
                <p>
                  解释或检索可直接发送。改稿前请选择下方源码候选；无法匹配时，请在源码中重新选择。
                </p></template
              >
              <p v-else>
                选择论文中的文字，或直接提问。Agent 会按评论意图解释、检索、计算或提出改稿补丁。
              </p>
              <div v-if="locating">正在查找源码位置…</div>
              <button
                v-for="(candidate, index) in candidates"
                :key="`${candidate.file}:${candidate.start}`"
                class="candidate"
                @click="confirmCandidate(candidate)"
              >
                <strong>{{ selectionLabel(candidate) }} · 候选 {{ index + 1 }}</strong>
                <pre>{{ candidate.context }}</pre>
                <span>确认此位置</span>
              </button>
              <p v-if="readQuote && !locating && !candidates.length && !selection" class="hint">
                未找到逐字对应的源码范围。公式、宏或换行可能改变阅读文字；不能猜测修改位置。
              </p>
            </details>
            <select
              v-if="!contextual || relatedThreads.length > 1"
              v-model="threadId"
              aria-label="评论线程"
              @change="chooseThread"
            >
              <option value="">新评论</option>
              <option v-for="thread in project.threads" :key="thread.id" :value="thread.id">
                {{ thread.messages[0]?.text.slice(0, 50) }}
              </option>
            </select>
            <div class="thread" v-if="activeThread">
              <article
                v-for="(message, index) in activeThread.messages"
                :key="index"
                :class="message.role"
              >
                <strong>{{ message.role === 'user' ? '你' : 'Agent' }}</strong
                ><MarkdownRenderer
                  :content="message.text"
                  :work-id="workId"
                  inline-sources
                  @source="(id, target) => openCitation('#literature-' + id, target)"
                />
              </article>
            </div>
            <article v-for="execution in threadExecutions" :key="execution.id" class="execution">
              <p v-if="execution.state === 'running' || execution.state === 'pending'">
                {{ execution.phase === 'write' ? '正在组织回复或修改…' : '正在处理评论…' }}
              </p>
              <p
                v-if="execution.state === 'failed' || execution.state === 'interrupted'"
                class="error"
              >
                {{ execution.error || '上次处理被中断，已保存原稿与完成步骤。'
                }}<button :disabled="busy" @click="resume(execution.id)">继续未完成的步骤</button>
              </p>
              <details v-if="execution.steps?.length">
                <summary>核查记录 · {{ execution.steps.length }} 步</summary>
                <article v-for="step in execution.steps" :key="step.id">
                  <p>
                    {{ commentToolLabels[step.tool] || '处理评论' }} ·
                    {{
                      step.state === 'running'
                        ? '进行中'
                        : step.state === 'failed'
                          ? '未完成'
                          : '完成'
                    }}
                  </p>
                  <pre v-if="step.result?.stdout || step.output">{{
                    step.result?.stdout || step.output
                  }}</pre>
                  <button
                    v-if="step.result?.code_path"
                    @click="$emit('open-file', step.result.code_path)"
                  >
                    查看代码
                  </button>
                  <button
                    v-if="step.result?.stdout_path"
                    @click="$emit('open-file', step.result.stdout_path)"
                  >
                    查看输出
                  </button>
                </article>
              </details>
              <template v-else-if="execution.tool_result"
                ><p>检索 / 计算结果已保存</p>
                <pre v-if="execution.tool_result.stdout">{{ execution.tool_result.stdout }}</pre>
                <button
                  v-if="execution.tool_result.code_path"
                  @click="$emit('open-file', execution.tool_result.code_path)"
                >
                  查看实际代码</button
                ><button
                  v-if="execution.tool_result.stdout_path"
                  @click="$emit('open-file', execution.tool_result.stdout_path)"
                >
                  查看实际输出
                </button></template
              >
            </article>
            <button @click="addToChat">在主对话中回复</button>
            <p class="hint">提出补丁后由你接受或拒绝。</p>
            <details class="changes" :open="activeMarkKind === 'change'">
              <summary>
                修改集 <span v-if="visibleChanges.length">· {{ visibleChanges.length }}</span>
              </summary>
              <article v-for="change in visibleChanges" :key="change.id" class="change">
                <p>
                  {{
                    change.status === 'pending'
                      ? '待确认'
                      : change.status === 'applied'
                        ? '已接受'
                        : change.status === 'reverted'
                          ? '已回退'
                          : '已拒绝'
                  }}
                  · {{ [...new Set(change.patches.map((p) => p.file))].join('、') }}
                </p>
                <pre class="diff">{{ change.diff }}</pre>
                <template v-if="change.status === 'pending'"
                  ><button :disabled="busy" @click="decide(change.id, 'apply')">接受整组修改</button
                  ><button :disabled="busy" @click="decide(change.id, 'reject')">
                    拒绝
                  </button></template
                ><button
                  v-if="change.status === 'applied'"
                  :disabled="busy"
                  @click="decide(change.id, 'revert')"
                >
                  回退这组修改
                </button>
              </article>
              <p v-if="!visibleChanges.length" class="hint">还没有修改集。</p>
            </details>
          </template>
          <template v-else-if="side === 'sources'">
            <p class="hint">
              摘录来自实际获取的资料；只有摘要时明确标记。来源关联不代表结论已经证实。
            </p>
            <article v-for="source in project.sources" :key="source.key" class="source-row">
              <button @click="openSource(source, $event.currentTarget as Element)">
                <strong>{{ source.title }}</strong
                ><span
                  >{{ source.boundary }} ·
                  {{ source.paths?.length ? '代码与实际输出' : source.locator }}</span
                >
              </button>
            </article>
            <p v-if="!project.sources.length">尚未获取可定位的来源。可在评论中请求检索。</p>
          </template>
          <template v-else>
            <article v-for="issue in visibleFeedback" :key="issue.id" class="opinion">
              <p>{{ issue.message }}</p>
              <p v-if="isLatex && issue.paper_origin?.version !== project.version" class="hint">
                此意见尚未在当前源码版本核实，请重新检查。
              </p>
              <button
                v-if="issue.section && project.files.some((s) => s.path === issue.section)"
                @click="showIssueSection(issue.section)"
              >
                查看对应正文
              </button>
              <button @click="commentIssue(issue.message)">在评论中讨论</button>
            </article>
            <p v-if="!project.feedback?.length">当前没有待处理的正文意见。</p>
            <p class="hint">
              以下检查针对登记研究记录；源码修改后的主张仍需核对，编译成功只说明排版成功。
            </p>
            <PaperChecks :work-id="workId" :refresh-key="refreshKey" @changed="updated" />
          </template>
        </div>
      </PaperPopover>
    </div>
    <PaperSourcePopover
      :source="sourceCard"
      :anchor="popoverAnchor"
      :host="manuscript"
      @close="sourceCard = null"
      @open-file="(path) => $emit('open-file', path)"
    />
  </section>
</template>
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import {
  paperProjectAPI,
  type PaperProject,
  type PaperSource,
  type SourceSelection,
  type PaperComposerContext,
} from '@/api/paper'
import type { ResearchSnapshot } from '@/types/research'
import { selectionLabel, sourceSelection, sourceKey } from '@/utils/paperSelection'
import {
  elementAnchor,
  uniqueTextSpan,
  type PaperAnchor,
  type TextAnnotation,
} from '@/utils/paperAnnotations'
import MarkdownRenderer from './MarkdownRenderer.vue'
import PaperPdfReader from './PaperPdfReader.vue'
import PaperSourcePopover from './PaperSourcePopover.vue'
import PaperChecks from './PaperChecks.vue'
import PaperPopover from './PaperPopover.vue'
import PaperTextAnnotations from './PaperTextAnnotations.vue'
const commentToolLabels: Record<string, string> = {
  read_file: '查看文件',
  run_python: '读取与计算',
  search_literature: '检索文献',
  propose_edits: '提出修改',
}
const props = defineProps<{
  workId: string
  ownerId?: number | string
  latex: boolean
  title?: string
  active?: boolean
  snapshot?: ResearchSnapshot | null
  refreshKey?: number
  disabled?: boolean
  evidenceRequest?: { section: string; section_version: string; at: number } | null
  citationRequest?: { id: string; at: number } | null
  issueRequest?: { id: string; at: number } | null
}>()
const emit = defineEmits<{
  'open-file': [path: string]
  updated: []
  artifact: []
  ask: [message: string, task?: string]
  research: [message: string, task?: string, issue?: { id: string; version: string }]
  quote: [context: PaperComposerContext]
}>()
const project = ref<PaperProject | null>(null)
const view = ref<'read' | 'source'>('read'),
  side = ref<'comments' | 'sources' | 'opinions' | null>(null)
type Panel = 'comments' | 'sources' | 'opinions'
const popoverAnchor = ref<PaperAnchor | null>(null),
  activeMarkKind = ref<'comment' | 'change'>('comment')
const manuscript = ref<HTMLElement | null>(null),
  sourceMirror = ref<HTMLElement | null>(null),
  sourceSurface = ref<HTMLElement | null>(null),
  annotationRoot = ref<HTMLElement | null>(null)
const mirrorStyle = ref<Record<string, string>>({})
const activeRange = shallowRef<Range | null>(null)
const contextual = ref(false)
const file = ref(''),
  draft = ref(''),
  draftBase = ref(''),
  draftVersion = ref(''),
  selection = ref<SourceSelection | null>(null)
const sourceEditor = ref<HTMLTextAreaElement | null>(null),
  readQuote = ref(''),
  candidates = ref<SourceSelection[]>([]),
  locating = ref(false)
const loading = ref(false),
  busy = ref(false),
  compiling = ref(false),
  error = ref(''),
  threadId = ref(''),
  sourceCard = ref<PaperSource | null>(null)
let generation = 0,
  locateGeneration = 0,
  timer: ReturnType<typeof setTimeout> | undefined
const selectedFile = computed(() => project.value?.files.find((row) => row.path === file.value))
const dirty = computed(() => draft.value !== draftBase.value)
const isLatex = computed(() => project.value?.main.endsWith('.tex') ?? props.latex)
const activeThread = computed(() => project.value?.threads.find((row) => row.id === threadId.value))
const relatedThreads = computed(
  () =>
    project.value?.threads.filter(
      (row) =>
        row.selection?.file === selection.value?.file &&
        row.selection?.quote === selection.value?.quote,
    ) || [],
)
const quickComment = computed(
  () =>
    contextual.value &&
    side.value === 'comments' &&
    !threadId.value &&
    activeMarkKind.value === 'comment',
)
const threadExecutions = computed(
  () => project.value?.executions.filter((row) => row.thread_id === threadId.value) || [],
)
const visibleChanges = computed(() =>
  (project.value?.changes || [])
    .filter((row) => !threadId.value || row.thread_id === threadId.value || !row.thread_id)
    .slice()
    .reverse(),
)
const textAnnotations = computed<TextAnnotation[]>(() => {
  const doc = project.value
  if (!doc) return []
  const valid = (bound?: SourceSelection | null) => {
    if (!bound || (view.value === 'source' && bound.file !== file.value)) return false
    const text = doc.files.find((row) => row.path === bound.file)?.text
    if (typeof text !== 'string') return false
    const exact = Array.from(text).slice(bound.start, bound.end).join('') === bound.quote
    return bound.version === doc.version ? exact : !!uniqueTextSpan(text, bound.quote)
  }
  return [
    ...doc.threads.flatMap((thread) => {
      const selections = thread.references?.length
        ? thread.references.map((reference) => reference.selection).filter(valid)
        : valid(thread.selection)
          ? [thread.selection]
          : []
      return selections.map((bound, index) => ({
        id: `thread:${thread.id}:${index}`,
        kind: 'comment' as const,
        quote: bound!.quote,
        label: '展开评论：' + (thread.messages[0]?.text || bound!.quote).slice(0, 70),
        threadId: thread.id,
        selection: bound!,
      }))
    }),
    ...doc.changes
      .filter((change) => change.status === 'pending')
      .flatMap((change) =>
        change.patches
          .filter((bound) => valid(bound))
          .map((bound, index) => ({
            id: `change:${change.id}:${index}`,
            kind: 'change' as const,
            quote: bound.quote,
            label: '查看待确认修改：' + bound.quote.slice(0, 70),
            threadId: change.thread_id,
            selection: bound,
          })),
      ),
  ]
})
function openPanel(panel: Panel, anchor: PaperAnchor | null = popoverAnchor.value) {
  sourceCard.value = null
  popoverAnchor.value = anchor
  side.value = panel
}
function selectedRangeAnchor(): PaperAnchor | null {
  if (typeof window === 'undefined') return null
  const selected = window.getSelection?.()
  if (
    !selected?.rangeCount ||
    !manuscript.value?.contains(selected.anchorNode) ||
    !manuscript.value.contains(selected.focusNode)
  )
    return null
  const range = selected.getRangeAt(0).cloneRange()
  activeRange.value = range.collapsed ? null : range
  return () => (range.startContainer.isConnected ? range.getBoundingClientRect() : null)
}
function sourceRangeAnchor(): PaperAnchor | null {
  const node = sourceEditor.value,
    text = sourceMirror.value?.firstChild
  if (!node || !text) return elementAnchor(node)
  const range = document.createRange()
  range.setStart(text, Math.min(node.selectionStart, text.textContent?.length || 0))
  range.setEnd(text, Math.min(node.selectionEnd, text.textContent?.length || 0))
  return () => (text.isConnected ? range.getBoundingClientRect() : null)
}
function syncSourceScroll() {
  const node = sourceEditor.value
  if (!node) return
  mirrorStyle.value = {
    width: node.clientWidth + 'px',
    transform: `translate(${-node.scrollLeft}px, ${-node.scrollTop}px)`,
  }
}
async function refreshAnnotationRoot() {
  await nextTick()
  annotationRoot.value =
    view.value === 'source'
      ? sourceMirror.value
      : manuscript.value?.querySelector<HTMLElement>(
          isLatex.value ? '.textLayer' : '.read-text .markdown-content',
        ) || null
  syncSourceScroll()
}
watch(
  () => [view.value, file.value, project.value?.version, sourceMirror.value, dirty.value],
  refreshAnnotationRoot,
)
function openAnnotation(annotation: TextAnnotation, anchor: PaperAnchor) {
  activeRange.value = null
  contextual.value = true
  locateGeneration++
  selection.value = annotation.selection
  readQuote.value = ''
  candidates.value = []
  threadId.value = annotation.threadId || ''
  activeMarkKind.value = annotation.kind
  openPanel('comments', anchor)
}
function chooseThread() {
  locateGeneration++
  selection.value = activeThread.value?.selection || null
  readQuote.value = ''
  candidates.value = []
}
const locationKey = () => `paperagent:paper:${props.ownerId || 'session'}:${props.workId}`
function setView(next: typeof view.value) {
  activeRange.value = null
  side.value = null
  sourceCard.value = null
  view.value = next
  try {
    sessionStorage.setItem(locationKey(), next)
  } catch {
    /* optional */
  }
}
function resetDraft() {
  draft.value = selectedFile.value?.text || ''
  draftBase.value = draft.value
  draftVersion.value = project.value?.version || ''
}
function loadDraft() {
  try {
    const saved = JSON.parse(
      sessionStorage.getItem(locationKey() + ':edit:' + file.value) || 'null',
    )
    if (saved && saved.draft !== saved.base) {
      draft.value = saved.draft
      draftBase.value = saved.base
      draftVersion.value = saved.version
      return
    }
  } catch {
    /* Storage is optional. */
  }
  resetDraft()
}
function discardDraft() {
  try {
    sessionStorage.removeItem(locationKey() + ':edit:' + file.value)
  } catch {
    /* optional */
  }
  resetDraft()
}
watch(draft, () => {
  if (!file.value || !project.value) return
  try {
    sessionStorage.setItem(
      locationKey() + ':edit:' + file.value,
      JSON.stringify({ draft: draft.value, base: draftBase.value, version: draftVersion.value }),
    )
  } catch {
    /* Keep the in-memory draft. */
  }
})
watch(file, () => {
  loadDraft()
  selection.value = null
  side.value = null
  sourceCard.value = null
})
watch(
  () => project.value?.version,
  (version, previous) => {
    if (previous && version !== previous) {
      side.value = null
      sourceCard.value = null
      popoverAnchor.value = null
    }
  },
)
function captureSource(event?: MouseEvent | KeyboardEvent) {
  if (event && 'key' in event && event.key === 'Escape') return
  if (!sourceEditor.value || dirty.value || !project.value) {
    selection.value = null
    return
  }
  const node = sourceEditor.value
  selection.value = sourceSelection(
    file.value,
    draftVersion.value,
    draft.value,
    node.selectionStart,
    node.selectionEnd,
  )
  if (selection.value) {
    activeRange.value = null
    contextual.value = true
    locateGeneration++
    readQuote.value = ''
    candidates.value = []
    threadId.value = ''
    activeMarkKind.value = 'comment'
    openPanel('comments', sourceRangeAnchor())
  }
}
function captureRendered(event?: MouseEvent | KeyboardEvent) {
  if (event && 'key' in event && event.key === 'Escape') return
  const anchor = selectedRangeAnchor()
  const quote = anchor ? window.getSelection()?.toString().trim() || '' : ''
  if (quote) void captureRead(quote, anchor)
}
async function captureRead(quote: string, anchor: PaperAnchor | null = selectedRangeAnchor()) {
  if (!quote.trim()) return
  contextual.value = true
  readQuote.value = quote
  selection.value = null
  candidates.value = []
  threadId.value = ''
  activeMarkKind.value = 'comment'
  openPanel('comments', anchor)
  locating.value = true
  const seq = ++locateGeneration,
    work = props.workId
  try {
    const result = await paperProjectAPI.locate(work, quote)
    if (seq === locateGeneration && work === props.workId) candidates.value = result.candidates
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '未能定位源码，请在源码视图选择。'
  } finally {
    if (seq === locateGeneration) locating.value = false
  }
}
async function confirmCandidate(candidate: SourceSelection) {
  selection.value = candidate
  candidates.value = []
  file.value = candidate.file
  setView('source')
  await nextTick()
  selection.value = candidate
  const text = selectedFile.value?.text || ''
  sourceEditor.value?.setSelectionRange(
    Array.from(text).slice(0, candidate.start).join('').length,
    Array.from(text).slice(0, candidate.end).join('').length,
  )
  syncSourceScroll()
  openPanel('comments', sourceRangeAnchor())
}
function openCitation(url: string, target?: Element) {
  if (target) popoverAnchor.value = elementAnchor(target)
  side.value = null
  const sources = project.value?.sources || [],
    key = sourceKey(url, sources)
  sourceCard.value = sources.find((row) => row.key === key) || null
  if (!sourceCard.value) {
    side.value = 'sources'
    error.value = '该引用没有唯一可定位摘录。请从来源列表核对；未补写原文。'
  }
}
function openSource(source: PaperSource, target: Element) {
  // A source-list button disappears when its list closes. Keep its visible
  // position instead of retaining a detached DOM element as the anchor.
  const rect = target.getBoundingClientRect()
  const point = { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }
  popoverAnchor.value = () => point
  side.value = null
  sourceCard.value = source
}
function readClick(event: MouseEvent) {
  if (event.defaultPrevented) return
  const link = (event.target as Element).closest?.('a')
  if (link?.getAttribute('href')?.startsWith('#literature-')) {
    event.preventDefault()
    event.stopPropagation()
    openCitation(link.getAttribute('href')!, link)
  }
}
const visibleFeedback = computed(() => {
  const rows = project.value?.feedback || []
  const selected = rows.find((issue) => issue.id === props.issueRequest?.id)
  return selected ? [selected] : rows
})
async function showIssueSection(key: string, issueId?: string) {
  const issue = project.value?.feedback?.find((row) => row.id === issueId)
  const origin = issue?.paper_origin
  if (!isLatex.value) {
    setView('read')
    await nextTick()
    const node = Array.from(
      manuscript.value?.querySelectorAll<HTMLElement>('[data-section]') || [],
    ).find((node) => node.dataset.section === key)
    node?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    return
  }
  const row = project.value?.files.find((row) => row.path === key)
  if (!row) {
    openPanel('opinions')
    return
  }
  file.value = key
  setView('source')
  await nextTick()
  if (origin && origin.version === project.value?.version && origin.selection) {
    selection.value = origin.selection
    const text = row.text || ''
    sourceEditor.value?.setSelectionRange(
      Array.from(text).slice(0, origin.selection.start).join('').length,
      Array.from(text).slice(0, origin.selection.end).join('').length,
    )
  }
  openPanel('opinions', elementAnchor(manuscript.value))
}
watch(
  () => [props.evidenceRequest, !!project.value] as const,
  ([request, ready]) => {
    if (!request || !ready) return
    const row = project.value?.files.find((row) => row.path === request.section)
    const legacy =
      !isLatex.value && project.value?.draft_sections?.find((row) => row.key === request.section)
    if (
      legacy
        ? legacy.content_hash !== request.section_version
        : !row || row.version !== request.section_version
    ) {
      error.value = '正文已更新，原引用对应旧版本。请从当前源码查看依据。'
      return
    }
    void showIssueSection(request.section)
  },
)
function openResult(resultId: string) {
  window.dispatchEvent(
    new CustomEvent('paper-result-open', { detail: { workId: props.workId, resultId } }),
  )
}
function addToChat(quote?: string | Event) {
  if (!project.value) return
  emit('quote', {
    work_id: props.workId,
    research_id: project.value.research_id,
    selection: selection.value,
    read_quote: readQuote.value || null,
    read_version: view.value === 'read' && isLatex.value ? project.value.pdf_version || null : null,
    thread_id: threadId.value || null,
    quote:
      (typeof quote === 'string' ? quote : '') ||
      selection.value?.quote ||
      readQuote.value ||
      activeThread.value?.messages[0]?.text ||
      '当前论文',
  })
  side.value = null
}
function commentIssue(text: string) {
  readQuote.value = ''
  selection.value = null
  addToChat('研究意见：' + text)
}
async function saveDraft() {
  if (!project.value || draft.value === draftBase.value) return
  busy.value = true
  error.value = ''
  try {
    // Persist a minimal patch, so unrelated concurrent edits in the same file survive.
    let start = 0,
      end = draftBase.value.length,
      replacementEnd = draft.value.length
    while (start < end && start < replacementEnd && draftBase.value[start] === draft.value[start])
      start++
    while (
      end > start &&
      replacementEnd > start &&
      draftBase.value[end - 1] === draft.value[replacementEnd - 1]
    ) {
      end--
      replacementEnd--
    }
    // Insertion-only changes carry one exact neighbouring character as an anchor.
    if (start === end && draftBase.value.length) {
      if (start > 0) start--
      else {
        end++
        replacementEnd++
      }
    }
    const bound = sourceSelection(file.value, draftVersion.value, draftBase.value, start, end)
    if (!bound) throw new Error('无法绑定此编辑范围，请保留文件骨架。')
    await paperProjectAPI.propose(props.workId, {
      research_id: project.value.research_id,
      thread_id: threadId.value || null,
      patches: [{ ...bound, replacement: draft.value.slice(start, replacementEnd) }],
    })
    openPanel('comments', sourceRangeAnchor())
    error.value = '修改集已保存；请查看差异并接受。'
    // Keep the draft until the user accepts; reading refresh cannot discard it.
    await load()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '修改集保存失败，编辑仍保留。'
  } finally {
    busy.value = false
  }
}

async function decide(id: string, decision: string) {
  if (!project.value) return
  busy.value = true
  error.value = ''
  try {
    await paperProjectAPI.decide(props.workId, id, project.value.research_id, decision)
    if (decision === 'apply' || decision === 'revert') selection.value = null
    await load()
    if (!dirty.value || selectedFile.value?.text === draft.value) resetDraft()
    emit('updated')
    emit('artifact')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '修改未应用，源码已保留。'
    await load()
  } finally {
    busy.value = false
  }
}
async function resume(id: string) {
  if (!project.value) return
  busy.value = true
  try {
    await paperProjectAPI.resume(props.workId, id, project.value.research_id)
    await load()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '暂时无法继续。'
  } finally {
    busy.value = false
  }
}
async function compile() {
  if (!project.value) return
  compiling.value = true
  error.value = ''
  try {
    await paperProjectAPI.compile(props.workId, project.value.research_id)
    await load()
    emit('artifact')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '编译未完成，上次成功预览保留。'
  } finally {
    compiling.value = false
  }
}
async function updated() {
  await load()
  emit('updated')
}
async function load() {
  const seq = ++generation,
    work = props.workId
  loading.value = true
  if (timer) clearTimeout(timer)
  try {
    const value = await paperProjectAPI.read(work)
    if (seq !== generation || work !== props.workId) return
    const wasDirty = dirty.value
    project.value = value
    if (!value.has_source) view.value = 'read'
    if (!value.files.some((row) => row.path === file.value)) {
      file.value = value.main
      loadDraft()
    } else if (!wasDirty) resetDraft()
    if (value.executions.some((row) => row.state === 'pending' || row.state === 'running'))
      timer = setTimeout(load, 2000)
  } catch (cause) {
    if (seq === generation)
      error.value = cause instanceof Error ? cause.message : '暂时无法读取论文。'
  } finally {
    if (seq === generation) loading.value = false
  }
}
watch(
  () => props.workId,
  () => {
    generation++
    locateGeneration++
    project.value = null
    file.value = ''
    draft.value = ''
    draftBase.value = ''
    selection.value = null
    threadId.value = ''
    side.value = null
    sourceCard.value = null
    popoverAnchor.value = null
    error.value = ''
    try {
      view.value = sessionStorage.getItem(locationKey()) === 'source' ? 'source' : 'read'
    } catch {
      view.value = 'read'
    }
    void load()
  },
  { immediate: true },
)
watch(() => props.refreshKey, load)
watch(
  () => props.citationRequest,
  (request) => {
    if (request) {
      openPanel('sources', elementAnchor(manuscript.value))
      sourceCard.value = project.value?.sources.find((row) => row.key === request.id) || null
    }
  },
)
watch(
  () => [props.issueRequest, !!project.value] as const,
  ([request, ready]) => {
    if (request && ready) {
      const section = project.value?.feedback?.find((issue) => issue.id === request.id)?.section
      if (section && project.value?.files.some((row) => row.path === section))
        void showIssueSection(section, request.id)
      else if (section)
        void showIssueSection(section).then(() =>
          openPanel('opinions', elementAnchor(manuscript.value)),
        )
      else openPanel('opinions', elementAnchor(manuscript.value))
    }
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  generation++
  locateGeneration++
  if (timer) clearTimeout(timer)
})
</script>
<style scoped>
.evidence-section {
  outline: 2px solid var(--pa-accent);
  outline-offset: 8px;
}
.section-evidence {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 12px 0;
  border-top: 1px solid var(--pa-line);
  font-size: 12px;
  color: var(--pa-muted);
}
.section-evidence button {
  border: 0;
  background: var(--pa-surface-muted);
  color: var(--pa-accent);
  border-radius: var(--pa-radius-control);
  padding: 5px 8px;
  cursor: pointer;
}

.paper-editor {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
  position: relative;
  background: var(--pa-surface);
  color: var(--pa-ink);
  container-type: inline-size;
  container-name: paper;
}
.toolbar,
.file-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 10px 12px;
  border-bottom: 1px solid var(--pa-line);
  font-size: 12px;
}
.toolbar nav {
  display: flex;
  margin-right: auto;
}
button,
select {
  font: inherit;
  font-size: 12px;
  color: var(--pa-ink);
  background: var(--pa-surface);
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-control);
  padding: 7px 9px;
  cursor: pointer;
}
button:hover,
button[aria-pressed='true'] {
  background: var(--pa-surface-muted);
  color: var(--pa-accent);
}
button:disabled {
  opacity: 0.45;
  cursor: default;
}
.workspace {
  display: flex;
  flex: 1;
  min-height: 0;
}
.manuscript {
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.source-editor {
  flex: 1;
  min-height: 200px;
  width: 100%;
  resize: none;
  border: 0;
  outline: 0;
  padding: 20px;
  box-sizing: border-box;
  font:
    13px/1.8 ui-monospace,
    SFMono-Regular,
    monospace;
  background: var(--pa-surface);
  color: var(--pa-ink);
  tab-size: 2;
}
.source-surface {
  position: relative;
  flex: 1;
  min-height: 200px;
  display: flex;
  overflow: hidden;
}
.source-mirror {
  position: absolute;
  inset: 0 auto auto 0;
  margin: 0;
  padding: 20px;
  border: 0;
  box-sizing: border-box;
  white-space: pre-wrap;
  overflow-wrap: break-word;
  word-break: normal;
  font:
    13px/1.8 ui-monospace,
    SFMono-Regular,
    monospace;
  tab-size: 2;
  pointer-events: none;
  opacity: 0;
  max-height: none;
  overflow: visible;
}
.annotation-content > select {
  width: 100%;
  margin: 10px 0;
}
.comment-context {
  font-size: 12px;
  line-height: 1.7;
  background: var(--pa-surface-muted);
  border-radius: var(--pa-radius-card);
  padding: 12px;
}
blockquote {
  margin: 10px 0;
  border-left: 2px solid var(--pa-accent);
  padding-left: 10px;
  max-height: 140px;
  overflow: auto;
  white-space: pre-wrap;
}
.hint,
.notice,
details {
  font-size: 12px;
  color: var(--pa-muted);
  line-height: 1.7;
}
.hint {
  padding: 0 12px;
}
.notice {
  margin: 0;
  padding: 7px 14px;
  border-bottom: 1px solid var(--pa-line);
}
.stale {
  color: var(--pa-warning);
}
.error {
  color: var(--pa-danger);
  font-size: 13px;
  padding: 0 12px;
  overflow-wrap: anywhere;
}
.selection-action {
  width: 100%;
  padding: 7px 10px;
  border: 0;
  border-radius: 0;
  white-space: nowrap;
  font-size: 13px;
  cursor: pointer;
}
.selection-action:hover {
  background: var(--pa-surface-active);
}
.primary {
  background: var(--pa-accent);
  color: var(--pa-on-accent);
}
.thread article,
.execution,
.change,
.source-row,
.opinion {
  padding: 14px 0;
  border-bottom: 1px solid var(--pa-line);
  font-size: 13px;
  overflow-wrap: anywhere;
}
.thread .user {
  background: var(--pa-surface-muted);
  padding: 12px;
  border-radius: var(--pa-radius-card);
}
.thread strong {
  font-size: 12px;
  color: var(--pa-muted);
}
.changes h3 {
  font-size: 14px;
  font-weight: 500;
}
.diff,
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font:
    12px/1.7 ui-monospace,
    monospace;
  max-height: 360px;
  overflow: auto;
}
.diff {
  padding: 10px;
  background: var(--pa-surface-muted);
}
.source-row button,
.candidate {
  width: 100%;
  text-align: left;
}
.source-row span {
  display: block;
  padding-top: 5px;
  color: var(--pa-muted);
}
.candidate {
  margin-top: 10px;
}
.read-text {
  overflow: auto;
  flex: 1;
  padding: 30px;
  line-height: 1.9;
}
.section-finding {
  margin: 12px 0;
  padding: 8px 12px;
  border-left: 2px solid var(--pa-line);
  color: var(--pa-muted);
  font-size: 12px;
}
.section-finding summary {
  cursor: pointer;
}
.empty {
  padding: 36px;
  text-align: center;
  color: var(--pa-muted);
  margin: auto;
  line-height: 1.9;
}
.empty h2 {
  font-size: 18px;
  font-weight: 500;
}
:deep(.read-text a[href*='#literature-']),
:deep(.read-text a[href*='paperagent.local/citations/']) {
  color: var(--pa-info);
  text-decoration: underline;
  text-decoration-thickness: 2px;
  text-underline-offset: 4px;
  cursor: pointer;
}
:deep(.read-text a[href*='#literature-']:hover),
:deep(.read-text a[href*='paperagent.local/citations/']:hover) {
  background: var(--pa-info-soft);
}
@container paper (max-width:550px) {
  .read-text {
    padding: 20px;
  }
}
</style>
