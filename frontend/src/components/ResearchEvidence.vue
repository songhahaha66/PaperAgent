<template>
  <details v-if="snapshot?.has_research" class="research-evidence">
    <summary>
      <span><t-icon name="task-checked" />{{ isZh ? '研究结果' : 'Research results' }}</span>
      <span class="evidence-count">
        {{ snapshot.results.length }} {{ isZh ? '项结果' : 'results' }}
        <span v-if="snapshot.validation_count">
          · {{ snapshot.validation_count }} {{ isZh ? '项待复核' : 'to review' }}</span
        >
        <t-icon name="chevron-down" />
      </span>
    </summary>
    <div class="evidence-body">
      <div v-if="snapshot.tasks?.length" class="research-tasks">
        <p v-for="task in snapshot.tasks" :key="task.task_id">
          <strong>{{ task.task_id }} · v{{ task.revision }}</strong>
          {{ taskLabel(task.execution_status, task.acceptance, task.validity) }}
          <small v-if="task.reason"> · {{ task.reason }}</small>
          <button
            v-if="
              task.kind !== 'verify' &&
              snapshot.work_status !== 'in_progress' &&
              snapshot.work_status !== 'waiting'
            "
            class="evidence-link"
            @click="$emit('revise', task.task_id)"
          >
            {{ isZh ? '修改这部分' : 'Revise this task' }}
          </button>
        </p>
      </div>
      <details v-if="snapshot.requirements?.length" class="research-contract">
        <summary>{{ isZh ? '要求覆盖与假设' : 'Requirements and assumptions' }}</summary>
        <p v-for="requirement in snapshot.requirements" :key="requirement.id">
          <strong>{{ requirement.id }}</strong> · {{ requirement.task_ids.join(', ') }} ·
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
        </p>
        <p v-for="assumption in snapshot.assumptions" :key="assumption.id">
          <strong>{{ assumption.id }} · v{{ assumption.revision }}</strong> ·
          {{ assumptionLabel(assumption.status) }} <br />{{ assumption.statement }}
        </p>
      </details>
      <details v-if="snapshot.issues?.length" class="research-contract">
        <summary>{{ isZh ? '研究问题与修复记录' : 'Issues and repairs' }}</summary>
        <p v-for="issue in snapshot.issues" :key="issue.id">
          <strong
            >{{ issue.task_id }} · v{{ issue.task_revision }} ·
            {{ issueLabel(issue.status) }}</strong
          >
          <span v-if="issue.assumption_id"> · {{ issue.assumption_id }}</span
          ><br />{{ issue.message }}
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
        </p>
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
            {{ id }}
          </button>
        </div>
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
            <span class="result-id">{{ result.subproblem_id }} · {{ result.id }}</span>
            <span class="result-name">{{ result.name }}</span>
            <strong
              >{{ result.kind === 'figure' ? (isZh ? '图表' : 'Figure') : result.value
              }}<template v-if="result.unit && !result.value.endsWith(result.unit)">
                {{ result.unit }}</template
              ></strong
            >
            <t-icon :name="expanded === result.id ? 'chevron-up' : 'chevron-down'" />
          </button>
          <div v-if="expanded === result.id" class="result-sources">
            <code>{{ result.run_id }}</code>
            <div>
              <button v-if="result.figure_path" @click="$emit('open-file', result.figure_path)">
                {{ isZh ? '查看图表' : 'Figure' }}
              </button>
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
              <button @click="$emit('ask', result.subproblem_id)">
                {{ isZh ? '追问' : 'Ask' }}
              </button>
            </div>
          </div>
        </div>
      </div>
      <div class="evidence-footer">
        <button
          v-for="path in snapshot.artifacts.filter((p) => p !== 'paper.tex')"
          :key="path"
          @click="$emit('open-file', path)"
        >
          <t-icon name="file" />{{ path }}
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
const taskLabel = (execution: string, acceptance: string, validity: string) => {
  if (validity === 'stale')
    return isZh.value ? '结果已失效，等待重算' : 'Stale, awaiting recomputation'
  if (acceptance === 'rejected' || acceptance === 'inconclusive')
    return isZh.value ? '尚未通过验收' : 'Awaiting acceptance'
  if (acceptance === 'accepted') return isZh.value ? '已验收' : 'Accepted'
  if (execution === 'running') return isZh.value ? '执行中' : 'Running'
  return isZh.value ? '等待执行' : 'Pending'
}
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
  ...new Set(props.snapshot?.results.map((r) => r.subproblem_id) || []),
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
  border: 1px solid #e3e5e3;
  border-radius: 14px;
  background: #fff;
  color: #252a27;
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
  color: #666e68;
  font-weight: 400;
  font-size: 13px;
}
.source-quote {
  display: block;
  color: #69716b;
  margin-top: 5px;
  overflow-wrap: anywhere;
}
.research-contract {
  border-bottom: 1px solid #eceeec;
  margin-bottom: 10px;
}
.research-contract summary {
  padding: 8px 0;
  font-size: 13px;
}
.research-contract p {
  font-size: 13px;
  line-height: 1.6;
}
.evidence-body {
  border-top: 1px solid #eceeec;
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
  color: #626963;
  padding: 5px 9px;
  border-radius: 7px;
  font-size: 13px;
}
.evidence-scopes button.selected {
  color: white;
  background: var(--td-brand-color, #12845c);
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
  border-top: 1px solid #eff0ef;
}
.result-row {
  width: 100%;
  display: grid;
  grid-template-columns: 74px minmax(0, 1fr) minmax(50px, auto) 16px;
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
  background: #fafbf9;
}
.result-id {
  color: #777d78;
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
  padding: 0 0 12px 82px;
}
.result-sources code {
  display: block;
  margin-bottom: 9px;
  font-size: 12px;
  color: #69716b;
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
  color: var(--td-brand-color, #12845c);
  font-size: 13px;
}
.evidence-footer {
  padding-top: 12px;
  align-items: center;
  color: #737a74;
  font-size: 12px;
}
.evidence-footer button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.evidence-empty {
  padding: 16px 0;
  color: #737a74;
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
    grid-template-columns: 62px minmax(0, 1fr) minmax(45px, auto) 14px;
  }
  .result-sources {
    padding-left: 70px;
  }
}
</style>
