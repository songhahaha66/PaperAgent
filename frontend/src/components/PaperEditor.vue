<template>
  <section class="paper-editor">
    <header>
      <strong>{{ isZh ? '论文' : 'Paper' }}</strong>
      <button @click="load">{{ isZh ? '刷新正文' : 'Refresh' }}</button>
      <button @click="$emit('open-file', 'paper.tex')" v-if="latex">LaTeX</button>
      <button @click="$emit('open-file', 'paper.pdf')" v-if="latex">PDF</button>
      <button @click="$emit('artifact')">{{ isZh ? '排版预览' : 'Preview' }}</button>
    </header>
    <p v-if="draftNotice" role="status">{{ draftNotice }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <section v-if="issueContext" class="issue-context" aria-label="源审查意见">
      <strong>{{ issueContext.issue.message }}</strong>
      <p>{{ issueContext.issue.id }} · {{ issueContext.issue.status }}</p>
      <p>{{ issueContext.location }}</p>
      <pre v-if="issueContext.quote" class="issue-quote">{{ issueContext.quote }}</pre>
      <p v-if="!issueContext.origin_current" role="alert">
        意见对应旧正文或旧任务版本，须重新核查，不能直接应用旧修改。
      </p>
      <p>{{ issueContext.evidence_note }}</p>
      <p v-for="e in issueContext.evidence" :key="e.id">
        {{ e.id }} · {{ e.name }}：{{ e.value }} {{ e.unit }} ·
        {{ e.current ? '当前证据' : '历史证据' }}
        <button v-if="e.stdout_path" @click="$emit('open-file', e.stdout_path)">
          查看计算输出
        </button>
      </p>
      <template v-if="issueContext.route === 'research'"
        ><p>这是实质科研问题，需核验数据、方法或假设；不能靠修改措辞关闭。</p>
        <textarea v-model="instruction" :disabled="busy" /><button
          :disabled="busy || !issueContext.active || !issueContext.origin_current"
          @click="research"
        >
          提交这条意见的定向研究修改
        </button></template
      >
      <p v-else-if="issueContext.route === 'service'">
        这是服务审查故障，请在运行状态中恢复对应审查。
      </p>
      <p v-else>候选通过源意见核查并应用成功后，才更新这条意见；其他意见继续保留。</p>
      <button @click="issueContext = null">退出这条意见</button>
    </section>
    <p v-if="!document && !error">{{ isZh ? '正在读取章节…' : 'Loading sections…' }}</p>
    <template v-if="document">
      <p v-if="!document.sections.length">
        {{
          isZh
            ? '论文将在研究通过验收并完成写作后显示。'
            : 'Sections will appear after research checks and writing complete.'
        }}
      </p>
      <label
        >{{ isZh ? '选择章节' : 'Section' }}
        <select v-model="selected" :disabled="busy || !!issueContext">
          <option v-for="s in document.sections" :key="s.key" :value="s.key">
            {{ s.title }}
          </option>
        </select>
      </label>
      <template v-if="section && (!issueContext || issueContext.route === 'writing')">
        <p v-if="section.validity !== 'current'" role="alert">
          {{
            isZh
              ? '本节依据已更新，请先重新计算。'
              : 'This section uses stale evidence. Recompute first.'
          }}
        </p>
        <div class="section-body" @mouseup="captureSelection">
          <MarkdownRenderer :content="section.readable || section.text" :work-id="workId" />
        </div>
        <button @click="ask">
          {{ isZh ? '追问所选内容 / 本节依据' : 'Ask about selection / evidence' }}
        </button>
        <details
          class="editor-tools"
          :open="manual || !!instruction || !!proposal || !!issueContext"
        >
          <summary>{{ isZh ? '修改本节' : 'Edit this section' }}</summary>
          <label
            >{{ isZh ? '修改类型' : 'Edit type' }}
            <select v-model="mode" :disabled="busy">
              <option value="wording">
                {{ isZh ? '措辞润色（仅本节）' : 'Wording (this section)' }}
              </option>
              <option value="research">
                {{ isZh ? '数据、方法或结论' : 'Data, method or conclusions' }}
              </option>
              <option value="evidence" :disabled="!document.verified_methods?.length">
                {{ isZh ? '按已核验证据修正文稿' : 'Correct text using verified evidence' }}
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
            <template v-if="mode === 'wording' && !manual">
              <label>{{
                isZh
                  ? '只修改这个原文片段（最多600字符，须唯一匹配）'
                  : 'Exact fragment to edit (up to 600 characters)'
              }}</label>
              <textarea class="edit-fragment" v-model="fragment" maxlength="600" :disabled="busy" />
              <details>
                <summary>
                  {{ isZh ? '查看本节原文并复制片段' : 'View source and copy a fragment' }}
                </summary>
                <pre>{{ section.text }}</pre>
              </details>
              <p v-if="patchFailure">
                {{
                  patchFailure.recovery_allowed
                    ? isZh
                      ? '仅可在原片段内缩小到一半以内，再恢复一次；不会原样重试。'
                      : 'One recovery requires a fragment less than half the original.'
                    : isZh
                      ? '本次请求已停止，请查看失败记录。'
                      : 'This request has stopped.'
                }}
              </p>
            </template>
            <fieldset v-if="mode === 'evidence'" class="correction-sections">
              <legend>{{ isZh ? '一起修正的章节' : 'Sections to correct together' }}</legend>
              <p>
                {{
                  isZh
                    ? '依据当前已核验方法和结果修正公式或结论措辞。实验设置和实测结果保持不变；候选通过引用与全文一致性核查后才能应用。'
                    : 'Correct explanations using verified methods and results. Experimental settings and results stay fixed. Source and consistency checks are required.'
                }}
              </p>
              <p>
                {{
                  isZh
                    ? '多节及公式修正请直接编辑候选。自动改写请选择“措辞润色”并指定一个短片段。'
                    : 'Use manual candidates for formulas or multiple sections. Automatic wording changes require one exact short fragment.'
                }}
              </p>
              <label
                v-for="s in document.sections.filter((s) => s.key !== 'references')"
                :key="s.key"
                ><input
                  type="checkbox"
                  :value="s.key"
                  v-model="correctionKeys"
                  :disabled="busy"
                />{{ s.title }}</label
              >
            </fieldset>
            <label
              ><input type="checkbox" v-model="manual" :disabled="busy" />{{
                isZh ? '直接编辑候选正文' : 'Edit candidate manually'
              }}</label
            >
            <textarea
              v-if="manual && mode === 'wording'"
              class="candidate"
              v-model="draft"
              :disabled="busy"
            />
            <template v-if="manual && mode === 'evidence'">
              <div v-for="key in correctionKeys" :key="key">
                <label>{{ document.sections.find((s) => s.key === key)?.title }}</label>
                <details v-if="document.verified_formula_blocks?.[key]">
                  <summary>
                    {{ isZh ? '核验公式说明（修正时完整保留）' : 'Verified formula explanation' }}
                  </summary>
                  <pre>{{ document.verified_formula_blocks[key] }}</pre>
                </details>
                <textarea
                  class="candidate"
                  :data-section="key"
                  v-model="evidenceDrafts[key]"
                  :disabled="busy"
                />
              </div>
            </template>
            <button
              :disabled="
                busy ||
                (!!issueContext && (!issueContext.active || !issueContext.origin_current)) ||
                (!(manual && mode === 'wording') && !instruction.trim()) ||
                (mode === 'evidence' && (!manual || !correctionKeys.length)) ||
                (mode === 'wording' && !manual && !fragmentReady)
              "
              @click="propose"
            >
              {{
                busy ? (isZh ? '处理中…' : 'Working…') : isZh ? '生成修改对比' : 'Preview changes'
              }}
            </button>
          </template>
          <section v-if="proposal" class="proposal">
            <h3>{{ isZh ? '修改前后对比' : 'Review changes' }} · {{ proposal.status }}</h3>
            <p v-if="proposal.origin === 'model'">
              {{ isZh ? '模型局部提案' : 'Model fragment proposal' }}
            </p>
            <p v-else-if="proposal.origin === 'manual'">
              {{ isZh ? '人工候选' : 'Manual candidate' }}
            </p>
            <p v-for="issue in proposal.citation_issues || []" :key="issue" role="alert">
              {{ issue }}
            </p>
            <p v-for="(check, i) in proposal.citation_checks || []" :key="i">
              {{ check.status }} · {{ check.reason }}
            </p>
            <p v-for="(issue, i) in proposal.review_issues || []" :key="i" role="alert">
              {{ issue.section }} · {{ issue.message }}
            </p>
            <p v-if="proposal.source_issue">源意见 {{ proposal.source_issue.id }}</p>
            <p v-if="proposal.review_id">
              {{
                proposal.review_kind === 'issue_segmented'
                  ? '已保存完整分段覆盖与定位源意见判定'
                  : proposal.review_kind === 'issue_section'
                    ? '已保存源意见对应完整章节核查'
                    : proposal.review_kind === 'fragment_equivalence'
                      ? isZh
                        ? '已保存局部等义性审查记录'
                        : 'Fragment equivalence review recorded'
                      : isZh
                        ? '已保存完整章节覆盖审查记录'
                        : 'Complete section review recorded'
              }}
              · {{ proposal.review_id.slice(0, 10) }}
            </p>
            <div v-if="proposal.review_progress" class="candidate-review-progress">
              <p>
                候选分段覆盖 {{ proposal.review_progress.segmented.completed_blocks }} /
                {{ proposal.review_progress.segmented.total_blocks }} 块 ·
                {{ proposal.review_progress.segmented.covered_chars }} /
                {{ proposal.review_progress.segmented.total_chars }} 字符
              </p>
              <p>
                跨节核查：{{
                  reviewLabels[proposal.review_progress.segmented.global.state] ||
                  proposal.review_progress.segmented.global.state
                }}
                · 源意见定向判定：{{
                  reviewLabels[proposal.review_progress.target_state] ||
                  proposal.review_progress.target_state
                }}<span v-if="proposal.review_progress.target_verdict"
                  >（{{
                    verdictLabels[proposal.review_progress.target_verdict] ||
                    proposal.review_progress.target_verdict
                  }}）</span
                >
              </p>
            </div>
            <p v-if="proposal.review_progress_error" role="alert">
              {{ proposal.review_progress_error }}
            </p>
            <div v-for="change in proposalDiffs" :key="change.key" class="section-diff">
              <h4>
                {{ document.sections.find((s) => s.key === change.key)?.title || change.key }}
              </h4>
              <div class="diff">
                <div>
                  <h4>{{ isZh ? '修改前' : 'Before' }}</h4>
                  <pre>{{ change.before }}</pre>
                </div>
                <div>
                  <h4>{{ isZh ? '修改后' : 'After' }}</h4>
                  <pre>{{ change.after }}</pre>
                </div>
              </div>
            </div>
            <p>
              {{
                isZh
                  ? '请核对研究事实和结论强度。应用后保存版本历史并重新导出。'
                  : 'Check facts and conclusion strength. Applying saves history and rebuilds the export.'
              }}
            </p>
            <button
              v-if="proposal.status === 'pending'"
              :disabled="
                busy ||
                !!proposal.citation_issues?.length ||
                !!proposal.review_issues?.length ||
                (!!proposal.source_issue && proposal.source_issue.version !== issueContext?.version)
              "
              @click="decide('apply')"
            >
              {{ isZh ? '应用并更新论文' : 'Apply and update paper' }}
            </button>
            <button
              v-if="
                proposal.status === 'pending' &&
                ['scoped-anchored-edits-v2', 'manual-evidence-v1'].includes(
                  proposal.generation_contract || '',
                ) &&
                !proposal.review_id &&
                proposal.review_issues?.length
              "
              :disabled="busy"
              @click="decide('review')"
            >
              {{ isZh ? '恢复这个候选的审查' : 'Resume this candidate review' }}
            </button>
            <button v-if="proposal.status === 'pending'" :disabled="busy" @click="decide('reject')">
              {{ isZh ? '拒绝' : 'Reject' }}
            </button>
          </section>
        </details>
      </template>
      <details>
        <summary>
          {{ isZh ? '局部提案请求历史' : 'Fragment request history' }} ({{
            document.patch_attempts?.length || 0
          }})
        </summary>
        <div v-for="attempt in document.patch_attempts || []" :key="attempt.id">
          <p>
            {{ attempt.scope.section }} · {{ attempt.state }} · {{ attempt.id.slice(0, 10)
            }}<br />{{ attempt.scope.old.slice(0, 120) }}
          </p>
          <button
            v-if="attempt.state === 'truncated' && !attempt.recovery_of && !attempt.recovery_id"
            :disabled="
              busy ||
              document.source_hash !== attempt.scope.source_hash ||
              !document.sections.some(
                (s) =>
                  s.key === attempt.scope.section && s.content_hash === attempt.scope.section_hash,
              )
            "
            @click="restorePatchFailure(attempt)"
          >
            {{ isZh ? '选择更短片段，恢复一次' : 'Narrow fragment for one recovery' }}
          </button>
        </div>
      </details>
      <details>
        <summary>
          {{ isZh ? '版本与候选历史' : 'Version and candidate history' }} ({{
            document.history.length
          }})
        </summary>
        <button v-for="p in document.history" :key="p.id" @click="selectProposal(p)">
          {{ p.section }} · v{{ p.section_revision }} · {{ p.status }}
        </button>
      </details>
    </template>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'
import {
  paperAPI,
  type PaperDocument,
  type PaperProposal,
  type PaperIssueContext,
} from '@/api/paper'
import { useStudioCopy } from '@/lang/studio'
import MarkdownRenderer from './MarkdownRenderer.vue'
const props = defineProps<{
  workId: string
  ownerId?: number | string
  latex: boolean
  refreshKey?: number
  disabled?: boolean
  citationRequest?: { id: string; at: number } | null
  issueRequest?: { id: string; at: number } | null
}>()
const emit = defineEmits<{
  'open-file': [path: string]
  artifact: []
  updated: []
  ask: [message: string, task?: string]
  research: [message: string, task?: string, sourceIssue?: { id: string; version: string }]
}>()
const { isZh } = useStudioCopy()
const reviewLabels: Record<string, string> = {
  pending: '待核查',
  running: '进行中',
  failed: '中断，可恢复',
  completed: '已完成',
  superseded: '版本已失效',
  blocked: '尚未完成',
}
const verdictLabels: Record<string, string> = {
  resolved: '已解决',
  unresolved: '仍未解决',
  uncertain: '尚不能确认',
}
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
const draftNotice = ref('')
let activeDraftKey = '',
  restoringDraft = false
const draftKey = () => `paperagent:paper-drafts:${props.ownerId || 'session'}:${props.workId}`
const localDrafts: Record<
  string,
  {
    text: string
    instruction: string
    manual: boolean
    mode: string
    base: number
    fragment: string
    correctionKeys: string[]
    evidenceDrafts: Record<string, string>
  }
> = (() => {
  try {
    return typeof sessionStorage === 'undefined'
      ? {}
      : JSON.parse(sessionStorage.getItem(draftKey()) || '{}')
  } catch {
    return {}
  }
})()
function preserveDraft() {
  if (!activeDraftKey || restoringDraft) return
  localDrafts[activeDraftKey] = {
    text: draft.value,
    instruction: instruction.value,
    manual: manual.value,
    mode: mode.value,
    base: draftCommit.value,
    fragment: fragment.value,
    correctionKeys: [...correctionKeys.value],
    evidenceDrafts: { ...evidenceDrafts.value },
  }
  try {
    if (typeof sessionStorage !== 'undefined')
      sessionStorage.setItem(draftKey(), JSON.stringify(localDrafts))
  } catch {
    draftNotice.value = '浏览器存储不可用，请在离开前生成并保存修改候选。'
  }
}
const issueContext = ref<PaperIssueContext | null>(null)
let issueSequence = 0
async function locateIssue() {
  const request = props.issueRequest,
    seq = ++issueSequence,
    work = props.workId
  if (!request) {
    issueContext.value = null
    return
  }
  try {
    const value = await paperAPI.issue(work, request.id)
    if (seq !== issueSequence || work !== props.workId) return
    issueContext.value = value
    proposal.value = null
    await load()
    if (seq !== issueSequence) return
    if (value.section) selected.value = value.section
    await nextTick()
    instruction.value = value.issue.message
    mode.value = value.route === 'research' ? 'research' : 'wording'
    manual.value = false
    fragment.value = value.quote && value.quote.length <= 600 ? value.quote : ''
    draft.value = section.value?.text || ''
    draftCommit.value = document.value?.commit_seq || 0
  } catch (e) {
    if (seq === issueSequence) failure(e)
  }
}
watch(() => props.issueRequest, locateIssue, { immediate: true })
async function selectProposal(p: PaperProposal) {
  const seq = ++issueSequence
  proposal.value = p
  if (!p.source_issue) {
    issueContext.value = null
    selected.value = p.section
    return
  }
  try {
    const value = await paperAPI.issue(props.workId, p.source_issue.id)
    if (seq !== issueSequence) return
    issueContext.value = value
    selected.value = p.section
  } catch (e) {
    if (seq === issueSequence) failure(e)
  }
}
const fragment = ref('')
const patchFailure = ref<{
  attempt_id: string
  recovery_allowed: boolean
  scope: { old: string }
} | null>(null)
const correctionKeys = ref<string[]>([])
const evidenceDrafts = ref<Record<string, string>>({})
watch(correctionKeys, (keys) => {
  for (const key of keys)
    if (!(key in evidenceDrafts.value))
      evidenceDrafts.value[key] = document.value?.sections.find((s) => s.key === key)?.text || ''
})
watch(mode, (m) => {
  if (m === 'evidence') {
    manual.value = false
    correctionKeys.value = section.value ? [section.value.key] : []
  }
})
const proposalDiffs = computed(() =>
  proposal.value?.scope
    ? [
        {
          key: proposal.value.section,
          before: proposal.value.scope.old,
          after: proposal.value.generation_edits?.[0]?.replacement || '',
        },
      ]
    : proposal.value
      ? Object.entries(
          proposal.value.after_sections || { [proposal.value.section]: proposal.value.after },
        ).map(([key, after]) => ({
          key,
          after,
          before: proposal.value?.before_sections?.[key] || proposal.value?.before || '',
        }))
      : [],
)
watch(
  () => props.citationRequest,
  (request) => {
    if (!request) return
    mode.value = 'wording'
    manual.value = false
    instruction.value = `判断文献证据 {{C:${request.id}}} 是否支持本节的背景或方法。支持时仅补充有来源的定性观点并引用此标记，保留全部实验结果、数字、公式和原有引用；不支持时明确说明缺口，不添加引用。`
  },
  { immediate: true },
)
const busy = computed(() => working.value || props.disabled)
const section = computed(() => document.value?.sections.find((s) => s.key === selected.value))
const fragmentReady = computed(() => {
  const old = fragment.value,
    text = section.value?.text || ''
  if (
    !old.trim() ||
    old.length > 600 ||
    text.indexOf(old) < 0 ||
    text.indexOf(old) !== text.lastIndexOf(old)
  )
    return false
  if (!patchFailure.value) return true
  return (
    patchFailure.value.recovery_allowed &&
    patchFailure.value.scope.old.includes(old) &&
    old.length <= Math.floor(patchFailure.value.scope.old.length / 2)
  )
})
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
  if (
    s?.key === previous?.key &&
    s?.revision === previous?.revision &&
    s?.content_hash === previous?.content_hash
  )
    return
  if (manual.value && s?.key === previous?.key) return
  preserveDraft()
  restoringDraft = true
  activeDraftKey = s?.key || ''
  const saved = s ? localDrafts[s.key] : undefined
  draftCommit.value = saved?.base ?? document.value?.commit_seq ?? 0
  draft.value = saved?.text ?? s?.text ?? ''
  instruction.value = saved?.instruction || ''
  mode.value = saved?.mode || 'wording'
  manual.value = saved?.manual || false
  selection.value = ''
  fragment.value = saved?.fragment || ''
  patchFailure.value = null
  correctionKeys.value = saved?.correctionKeys || []
  evidenceDrafts.value = saved?.evidenceDrafts || {}
  if (saved) draftNotice.value = '已恢复本节的编辑草稿；应用前仍会检查正文是否已更新。'
  restoringDraft = false
  try {
    if (activeDraftKey && typeof sessionStorage !== 'undefined')
      sessionStorage.setItem(draftKey() + ':location', activeDraftKey)
  } catch {
    /* Editing remains available without storage. */
  }
})
watch(
  [draft, instruction, manual, mode, fragment, correctionKeys, evidenceDrafts, draftCommit],
  preserveDraft,
  { deep: true, flush: 'sync' },
)
function captureSelection() {
  selection.value = window.getSelection()?.toString().slice(0, 4000) || ''
  if (selection.value.length <= 600 && section.value?.text.split(selection.value).length === 2)
    fragment.value = selection.value
}
function ask() {
  emit(
    'ask',
    `请解释「${section.value?.title}」中${selection.value || '主要结论和图表'}的依据，列出证据、计算记录及局限。`,
    section.value?.task_refs.length === 1 ? section.value.task_refs[0] : undefined,
  )
}
async function research() {
  if (issueContext.value) {
    try {
      const current = await paperAPI.issue(props.workId, issueContext.value.issue.id)
      if (current.version !== issueContext.value.version || !current.active)
        throw new Error('意见版本已改变，请重新定位')
      emit(
        'research',
        `[源意见 ${current.issue.id} / ${current.version}] ${instruction.value}`,
        current.issue.task_id,
        { id: current.issue.id, version: current.version },
      )
    } catch (e) {
      failure(e)
    }
    return
  }
  emit(
    'research',
    `针对章节「${section.value?.title}」：${instruction.value}`,
    section.value?.task_refs.length === 1 ? section.value.task_refs[0] : undefined,
  )
}
function failure(e: unknown) {
  error.value = e instanceof Error ? e.message : String(e)
  const detail = (e as { response?: { data?: { detail?: any } } })?.response?.data?.detail
  if (detail?.attempt_id) patchFailure.value = detail
}
async function restorePatchFailure(attempt: NonNullable<PaperDocument['patch_attempts']>[number]) {
  selected.value = attempt.scope.section
  mode.value = 'wording'
  manual.value = false
  await nextTick()
  fragment.value = attempt.scope.old
  patchFailure.value = {
    attempt_id: attempt.id,
    recovery_allowed: true,
    scope: { old: attempt.scope.old },
  }
}
async function load() {
  try {
    error.value = ''
    document.value = await paperAPI.read(props.workId)
    if (!document.value.sections.some((s) => s.key === selected.value)) {
      let location = ''
      try {
        if (typeof sessionStorage !== 'undefined')
          location = sessionStorage.getItem(draftKey() + ':location') || ''
      } catch {
        /* Use the first section. */
      }
      selected.value =
        document.value.sections.find((s) => s.key === location)?.key ||
        document.value.sections[0]?.key ||
        ''
    }
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
      ...(issueContext.value
        ? { source_issue: { id: issueContext.value.issue.id, version: issueContext.value.version } }
        : {}),
      ...(manual.value && mode.value === 'wording' ? { content: draft.value } : {}),
      ...(mode.value === 'evidence'
        ? {
            edit_kind: 'evidence',
            section_keys: correctionKeys.value,
            ...(manual.value
              ? {
                  section_contents: Object.fromEntries(
                    correctionKeys.value.map((key) => [key, evidenceDrafts.value[key]]),
                  ),
                }
              : {}),
          }
        : {}),
      ...(mode.value === 'wording' && !manual.value
        ? {
            fragment_scope: {
              section_revision: section.value.revision,
              section_hash: section.value.content_hash,
              source_hash: document.value.source_hash,
              old: fragment.value,
            },
            ...(patchFailure.value ? { recovery_of: patchFailure.value.attempt_id } : {}),
          }
        : {}),
    })
    await load()
  } catch (e) {
    failure(e)
    const pendingFailure = patchFailure.value
    const pendingError = error.value,
      pendingFragment = fragment.value
    await load()
    await nextTick()
    patchFailure.value = pendingFailure
    error.value = pendingError
    fragment.value = pendingFragment
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
      if (proposal.value.source_issue)
        issueContext.value = await paperAPI.issue(props.workId, proposal.value.source_issue.id)
    }
    emit('updated')
  } catch (e) {
    failure(e)
  } finally {
    working.value = false
  }
}
watch(
  () => props.workId,
  () => {
    issueSequence++
    issueContext.value = null
    load()
    if (props.issueRequest) locateIssue()
  },
  { immediate: true },
)
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
.editor-tools > summary {
  font-weight: 600;
  color: var(--pa-accent);
  cursor: pointer;
  padding: 10px 0;
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
