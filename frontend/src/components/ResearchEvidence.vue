<template>
  <details v-if="snapshot?.has_research" class="research-evidence">
    <summary>
      <span><t-icon name="task-checked" />{{ isZh ? '研究依据' : 'Research evidence' }}</span>
      <span class="evidence-count">
        {{ snapshot.results.length }} {{ isZh ? '项结果' : 'results' }}
        <span v-if="snapshot.validation_count">
          · {{ snapshot.validation_count }} {{ isZh ? '项待复核' : 'to review' }}</span
        >
        <span v-if="snapshot.delivery && !snapshot.delivery.ready">
          · {{ isZh ? '交付待验收' : 'Delivery pending' }}
        </span>
        <t-icon name="chevron-down" />
      </span>
    </summary>
    <div class="evidence-body">
      <div v-if="snapshot.delivery" class="delivery-status" role="status">
        <strong>{{
          snapshot.delivery.ready
            ? snapshot.artifacts.length
              ? isZh
                ? '已通过交付验收'
                : 'Delivery checks passed'
              : isZh
                ? '研究验收通过，等待生成交付物'
                : 'Research accepted, preparing deliverables'
            : isZh
              ? '交付条件尚未满足'
              : 'Delivery checks pending'
        }}</strong>
        <details v-if="snapshot.delivery.blockers.length" class="research-contract">
          <summary>
            {{
              isZh
                ? `查看 ${snapshot.delivery.blockers.length} 项交付检查问题`
                : `Review ${snapshot.delivery.blockers.length} delivery findings`
            }}
          </summary>
          <p v-for="(blocker, index) in snapshot.delivery.blockers" :key="index">
            <strong>{{ taskTitle(blocker.task_id) }}</strong> · {{ blocker.message }}
            <small class="source-quote">{{ actionHint(blocker.action) }}</small>
          </p>
        </details>
      </div>
      <details v-if="checkedTasks.length" class="research-contract">
        <summary>{{ isZh ? '检查结果' : 'Check results' }}</summary>
        <div v-for="task in checkedTasks" :key="task.task_id" class="research-task">
          <strong>{{ taskName(task) }}</strong>
          <p v-for="check in task.validation!.checks" :key="check.check_id">
            <strong>{{ checkLabel(check.outcome) }}</strong> · {{ check.message }}
          </p>
          <details>
            <summary>{{ isZh ? '技术详情' : 'Technical details' }}</summary>
            <p>{{ task.task_id }} · v{{ task.revision }}</p>
          </details>
        </div>
      </details>
      <details
        v-if="snapshot.requirements?.length || snapshot.assumptions?.length"
        class="research-contract"
      >
        <summary>{{ isZh ? '要求覆盖与假设' : 'Requirements and assumptions' }}</summary>
        <div
          class="contract-entry"
          v-for="requirement in snapshot.requirements"
          :key="requirement.id"
        >
          <strong>{{ requirement.task_ids.map(taskTitle).join('、') }}</strong> ·
          {{
            requirement.status === 'accepted'
              ? isZh
                ? '已有验收证据'
                : 'Evidence accepted'
              : isZh
                ? '待完成验收'
                : 'Pending acceptance'
          }}
          <br />{{ requirement.statement }}
          <small class="source-quote"
            >{{ isZh ? '原文：' : 'Source: ' }}{{ requirement.source_quote }}</small
          >
          <details>
            <summary>{{ isZh ? '技术详情' : 'Technical details' }}</summary>
            <p>{{ requirement.id }} · {{ requirement.task_ids.join(', ') }}</p>
          </details>
        </div>
        <div class="contract-entry" v-for="assumption in snapshot.assumptions" :key="assumption.id">
          <strong>{{ assumption.task_ids.map(taskTitle).join('、') }}</strong> ·
          {{ assumptionLabel(assumption.status) }} <br />{{ assumption.statement }}
          <small v-if="assumption.source" class="source-quote">
            {{ premiseSource(assumption.source) }}
          </small>
          <details>
            <summary>{{ isZh ? '技术详情' : 'Technical details' }}</summary>
            <p>{{ assumption.id }} · v{{ assumption.revision }}</p>
          </details>
        </div>
      </details>
      <details v-if="snapshot.issues?.length" class="research-contract">
        <summary>{{ isZh ? '研究问题与修复记录' : 'Issues and repairs' }}</summary>
        <div class="contract-entry" v-for="issue in snapshot.issues" :key="issue.id">
          <strong>{{ taskTitle(issue.task_id) }} · {{ issueLabel(issue.status) }}</strong>
          <span v-if="issue.source === 'local_review'">
            · {{ isZh ? '求解审查' : 'Solution review' }}</span
          ><br />{{ issue.message }}
          <details>
            <summary>{{ isZh ? '技术详情' : 'Technical details' }}</summary>
            <p>
              {{ issue.task_id }} · v{{ issue.task_revision }} · {{ issue.id
              }}<span v-if="issue.assumption_id"> · {{ issue.assumption_id }}</span>
            </p>
          </details>
          <small
            v-for="evidence in evidenceFor(issue.evidence_refs)"
            :key="evidence.id"
            class="source-quote"
          >
            {{ evidence.id }} · {{ evidence.name }}：{{ evidence.value }} {{ evidence.unit }}
            <button
              v-if="runFor(evidence)?.stdout_path"
              class="evidence-link"
              @click="$emit('open-file', runFor(evidence)!.stdout_path!)"
            >
              {{ isZh ? '查看核验输出' : 'Check output' }}
            </button>
          </small>
        </div>
      </details>
      <div class="evidence-toolbar">
        <div
          class="evidence-scopes"
          role="group"
          :aria-label="isZh ? '按子问题筛选' : 'Filter by subproblem'"
        >
          <button :class="{ selected: !scope }" @click="scope = ''">
            {{ isZh ? '全部' : 'All' }}
          </button>
          <button
            v-for="id in subproblems"
            :key="id"
            :class="{ selected: scope === id }"
            @click="scope = id"
          >
            {{ taskTitle(id) }}
          </button>
        </div>
        <button v-if="scope && canRevise" class="evidence-link" @click="$emit('revise', scope)">
          {{ isZh ? '修改这部分' : 'Revise this task' }}
        </button>
        <button v-if="scope" class="evidence-link" @click="$emit('ask', scope)">
          {{ isZh ? '追问这部分' : 'Ask about this' }}<t-icon name="arrow-right" />
        </button>
      </div>
      <div v-if="!results.length" class="evidence-empty">
        {{ isZh ? '尚无登记结果' : 'No recorded results yet' }}
      </div>
      <div class="evidence-list">
        <div v-for="result in results" :key="result.id" class="evidence-item">
          <button
            class="result-row"
            :aria-expanded="expanded === result.id"
            @click="expanded = expanded === result.id ? '' : result.id"
          >
            <span class="result-name">{{ result.name }}</span>
            <strong
              ><small v-if="result.kind === 'series'">{{ isZh ? '数值序列 ' : 'Series ' }}</small
              >{{ result.kind === 'figure' ? (isZh ? '图表' : 'Figure') : result.value
              }}<template v-if="result.unit && !result.value.endsWith(result.unit)">
                {{ result.unit }}</template
              ></strong
            >
            <t-icon :name="expanded === result.id ? 'chevron-up' : 'chevron-down'" />
          </button>
          <div v-if="expanded === result.id" class="result-sources">
            <details>
              <summary>{{ isZh ? '技术详情' : 'Technical details' }}</summary>
              <code>{{ result.subproblem_id }} · {{ result.id }} · {{ result.run_id }}</code>
              <button
                v-if="runFor(result)?.input_path"
                @click="$emit('open-file', runFor(result)!.input_path!)"
              >
                {{ isZh ? '执行代码' : 'Code' }}
              </button>
              <button
                v-if="runFor(result)?.stdout_path"
                @click="$emit('open-file', runFor(result)!.stdout_path!)"
              >
                {{ isZh ? '执行输出' : 'Output' }}
              </button>
            </details>
            <div>
              <button v-if="result.figure_path" @click="$emit('open-file', result.figure_path)">
                {{ isZh ? '查看图表' : 'Figure' }}
              </button>
              <button @click="$emit('ask', result.subproblem_id)">
                {{ isZh ? '追问' : 'Ask' }}
              </button>
            </div>
          </div>
        </div>
      </div>
      <div class="evidence-footer">
        <button v-for="path in snapshot.artifacts" :key="path" @click="$emit('open-file', path)">
          <t-icon name="file" />{{ paperLabel(path, isZh) || path }}
        </button>
        <button
          v-if="snapshot.validation_count"
          @click="$emit('open-file', '.system/validation.json')"
        >
          {{ isZh ? '查看校验问题' : 'Review checks' }}
        </button>
        <span v-if="snapshot.truncated">{{
          isZh ? '仅展示部分记录' : 'Partial records shown'
        }}</span>
      </div>
    </div>
  </details>
</template>
<script setup lang="ts">
import { paperLabel } from '@/utils/workFiles'
import { taskName } from '@/utils/researchProgress'
import { computed, ref, watch } from 'vue'
import { useStudioCopy } from '@/lang/studio'
import type { ResearchSnapshot, ResearchResult } from '@/types/research'
const props = defineProps<{ snapshot: ResearchSnapshot | null }>()
defineEmits<{
  'open-file': [path: string]
  ask: [subproblemId: string]
  revise: [subproblemId: string]
}>()
const { isZh } = useStudioCopy()
const taskTitle = (id: string) => {
  const task = props.snapshot?.tasks?.find((t) => t.task_id === id)
  return task ? taskName(task) : id
}
const checkedTasks = computed(
  () => props.snapshot?.tasks?.filter((t) => t.validation?.checks.length) || [],
)
const canRevise = computed(() => {
  const task = props.snapshot?.tasks?.find((t) => t.task_id === scope.value)
  return (
    !!task &&
    task.kind !== 'verify' &&
    !['in_progress', 'running', 'waiting'].includes(props.snapshot?.work_status || '')
  )
})
const checkLabel = (outcome: string) =>
  ({
    passed: isZh.value ? '通过' : 'Passed',
    failed: isZh.value ? '未通过' : 'Failed',
    inconclusive: isZh.value ? '证据不足' : 'Inconclusive',
  })[outcome] || outcome
const actionHint = (action: string) =>
  ({
    verify: isZh.value
      ? '下一步：独立核验疑点，再决定是否修改计划。'
      : 'Next: independently verify the finding before revising the plan.',
    recompute: isZh.value
      ? '下一步：修正研究计划或输入，重算受影响任务并重新验收。'
      : 'Next: correct the plan or inputs, recompute affected tasks and validate again.',
    retry: isZh.value
      ? '下一步：处理执行或服务错误后，继续研究以重试。'
      : 'Next: resolve the execution or service error, then resume.',
    rewrite: isZh.value
      ? '下一步：修复正文缺项或引用，再检查交付；已验收计算可保留。'
      : 'Next: repair missing content or references and check delivery again; accepted computations are retained.',
    wait: isZh.value
      ? '下一步：等待当前任务或其依赖完成验收。'
      : 'Next: wait for the task or its dependencies to pass acceptance.',
  })[action] || action
const premiseSource = (source: string) =>
  ({
    analysis: isZh.value ? '题目分析提出' : 'Analysis',
    design: isZh.value ? '选定方案提出' : 'Selected design',
    solve: isZh.value ? '求解中提出' : 'Solving',
  })[source] || source
const assumptionLabel = (status: string) =>
  ({
    active: isZh.value ? '采用中，仍可被检验' : 'In use, testable',
    refuted: isZh.value ? '已被反证' : 'Refuted',
    retired: isZh.value ? '已停用' : 'Retired',
  })[status] || status
const issueLabel = (status: string) =>
  ({
    suspected: isZh.value ? '待独立核验' : 'Awaiting verification',
    confirmed: isZh.value ? '问题已确认' : 'Confirmed',
    repairing: isZh.value ? '计划修复中' : 'Repairing',
    resolved: isZh.value ? '已重算并通过验收' : 'Resolved and accepted',
    dismissed: isZh.value ? '核验未支持疑点' : 'Dismissed by verification',
    inconclusive: isZh.value ? '证据不足，交付受阻' : 'Inconclusive, delivery blocked',
  })[status] || status
const evidenceFor = (refs: string[]) =>
  (props.snapshot?.issue_evidence || []).filter((r) => refs.includes(r.id))
const scope = ref('')
const expanded = ref('')
const subproblems = computed(() => [
  ...new Set([
    ...(props.snapshot?.tasks?.map((t) => t.task_id) || []),
    ...(props.snapshot?.results.map((r) => r.subproblem_id) || []),
  ]),
])
const results = computed(() =>
  (props.snapshot?.results || []).filter((r) => !scope.value || r.subproblem_id === scope.value),
)
const runFor = (result: ResearchResult) => props.snapshot?.runs.find((r) => r.id === result.run_id)
watch(subproblems, (ids) => {
  if (!ids.includes(scope.value)) scope.value = ''
})
</script>
<style scoped>
.research-evidence {
  margin: 12px 0 4px;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-card);
  background: var(--pa-surface);
  color: var(--pa-ink);
}
summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  list-style: none;
}
summary::-webkit-details-marker {
  display: none;
}
summary > span {
  display: flex;
  align-items: center;
  gap: 7px;
}
.evidence-count {
  color: var(--pa-muted);
  font-weight: 400;
  font-size: 13px;
}
.source-quote {
  display: block;
  color: var(--pa-muted);
  margin-top: 5px;
  overflow-wrap: anywhere;
}
.research-contract {
  border-bottom: 1px solid var(--pa-line);
  margin-bottom: 10px;
}
.research-task {
  margin: 12px 0;
  font-size: 13px;
  line-height: 1.6;
}
.delivery-status {
  border-bottom: 1px solid var(--pa-line);
  padding-bottom: 10px;
  margin-bottom: 10px;
  font-size: 13px;
  line-height: 1.6;
}
.research-contract summary {
  padding: 8px 0;
  font-size: 13px;
}
.contract-entry {
  margin: 12px 0;
  font-size: 13px;
  line-height: 1.6;
}
.research-contract p {
  font-size: 13px;
  line-height: 1.6;
}
.evidence-body {
  border-top: 1px solid var(--pa-line);
  padding: 12px 16px;
}
.evidence-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}
.evidence-scopes {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
button {
  font: inherit;
  cursor: pointer;
}
.evidence-scopes button {
  border: 0;
  background: transparent;
  color: var(--pa-muted);
  padding: 5px 9px;
  border-radius: var(--pa-radius-control);
  font-size: 13px;
}
.evidence-scopes button.selected {
  color: var(--pa-on-accent);
  background: var(--td-brand-color);
}
.evidence-link {
  display: flex;
  align-items: center;
  gap: 4px;
  border: 0;
  background: transparent;
  color: var(--td-brand-color);
  font-size: 13px;
}
.evidence-list {
  max-height: 320px;
  overflow: auto;
}
.evidence-item + .evidence-item {
  border-top: 1px solid var(--pa-line);
}
.result-row {
  width: 100%;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(50px, auto) 16px;
  align-items: center;
  gap: 8px;
  padding: 11px 0;
  border: 0;
  text-align: left;
  background: transparent;
  color: inherit;
  font-size: 14px;
}
.result-row:hover {
  background: var(--pa-surface-hover);
}
.result-id {
  color: var(--pa-muted);
  font-size: 12px;
}
.result-name {
  overflow-wrap: anywhere;
}
.result-row strong {
  max-width: 180px;
  overflow-wrap: anywhere;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.result-sources {
  padding: 0 0 12px;
}
.result-sources code {
  display: block;
  margin-bottom: 9px;
  font-size: 12px;
  color: var(--pa-muted);
  overflow-wrap: anywhere;
}
.result-sources div,
.evidence-footer {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.result-sources button,
.evidence-footer button {
  border: 0;
  padding: 0;
  background: transparent;
  color: var(--td-brand-color);
  font-size: 13px;
}
.evidence-footer {
  padding-top: 12px;
  align-items: center;
  color: var(--pa-muted);
  font-size: 12px;
}
.evidence-footer button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.evidence-empty {
  padding: 16px 0;
  color: var(--pa-muted);
  font-size: 14px;
}
@media (max-width: 480px) {
  summary {
    padding: 12px;
  }
  .evidence-count {
    font-size: 12px;
  }
  .evidence-body {
    padding: 10px 12px;
  }
  .result-row {
    grid-template-columns: minmax(0, 1fr) minmax(45px, auto) 14px;
  }
  .result-sources {
    padding-left: 0;
  }
}
</style>
