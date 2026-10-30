<template>
  <section class="paper-checks">
    <p class="check-intro">检查与当前正文、登记结果和引用来源绑定。未完成的范围不算通过。</p>
    <button class="refresh-checks" :disabled="loading" @click="load">刷新检查</button>
    <p v-if="error" role="alert">{{ error }}</p>
    <article v-for="task in state?.tasks || []" :key="task.stage" class="check-item">
      <header>
        <strong>{{ task.label }}</strong
        ><span :data-state="task.state">{{ labels[task.state] || '等待核查' }}</span>
      </header>
      <p v-if="task.outcome === 'findings'" class="finding-note">检查已完成，发现内容需要处理。</p>
      <p>{{ task.coverage }}</p>
      <p v-if="task.reason">{{ task.reason }}</p>
      <p v-if="task.segmented">
        已核查 {{ task.segmented.completed_blocks }} /
        {{ task.segmented.total_blocks }} 段；跨章节核查：{{
          labels[task.segmented.global.state] || '尚未完成'
        }}
      </p>
      <details
        v-if="task.coverage_detail?.sections && Object.keys(task.coverage_detail.sections).length"
      >
        <summary>查看覆盖范围</summary>
        <p v-for="(coverage, key) in task.coverage_detail.sections" :key="key" class="coverage-row">
          <span>{{ sectionTitles?.[key] || '其他正文' }}</span
          ><span>{{
            task.state === 'completed' && coverage.included_chars === coverage.total_chars
              ? '已核查'
              : task.segmented
                ? coverage.included_chars === coverage.total_chars
                  ? '本节分段已完成'
                  : coverage.included_chars
                    ? '部分完成'
                    : '待核查'
                : '本次范围'
          }}</span>
        </p>
      </details>
      <button v-if="task.can_resume" :disabled="!!busy" @click="recover(task)">继续这项检查</button>
    </article>
  </section>
</template>
<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { reviewTasksAPI, type ReviewTaskState, type ReviewTaskItem } from '@/api/researchSetup'
const props = defineProps<{
    workId: string
    refreshKey?: number
    sectionTitles?: Record<string, string>
  }>(),
  emit = defineEmits<{ changed: []; paper: []; issue: [id: string] }>()
const state = ref<ReviewTaskState | null>(null),
  error = ref(''),
  loading = ref(false),
  busy = ref('')
const labels: Record<string, string> = {
  blocked: '尚不能完成',
  partial: '覆盖不完整',
  pending: '待审查',
  running: '运行中',
  completed: '已完成',
  failed: '失败，可恢复',
  superseded: '被新版本替代',
  not_required: '当前无需执行',
}
const stageLabels: Record<string, string> = {
  reconcile: '结论口径',
  sections: '章节一致性',
  citations: '文献支持',
  fragment: '局部等义',
  section_blocks: '正文分块',
  section_global: '跨节结论',
}
let version = 0,
  operation = 0,
  timer: ReturnType<typeof setInterval> | undefined
async function load() {
  const seq = ++version
  loading.value = true
  try {
    const value = await reviewTasksAPI.read(props.workId)
    if (seq === version) {
      state.value = value
      error.value = ''
    }
  } catch (e) {
    if (seq === version) error.value = e instanceof Error ? e.message : '审查状态暂不可读'
  } finally {
    if (seq === version) loading.value = false
  }
}
async function recover(task: ReviewTaskItem) {
  if (busy.value || !state.value || !task.can_resume) return
  const op = ++operation,
    work = props.workId
  busy.value = task.stage
  error.value = ''
  try {
    await reviewTasksAPI.recover(work, {
      research_id: state.value.research_id,
      stage: task.stage,
      version: task.version,
      command_id: crypto.randomUUID(),
    })
    if (op === operation && work === props.workId) {
      await load()
      emit('changed')
    }
  } catch (e) {
    if (op === operation && work === props.workId)
      error.value = e instanceof Error ? e.message : '恢复未提交，进度保留'
  } finally {
    if (op === operation) busy.value = ''
  }
}
watch(
  () => props.workId,
  () => {
    operation++
    busy.value = ''
    state.value = null
    load()
  },
  { immediate: true },
)
watch(() => props.refreshKey, load)
onMounted(() => {
  timer = setInterval(() => {
    if (state.value?.tasks.some((t) => t.state === 'running') && !loading.value) load()
  }, 1500)
})
onUnmounted(() => {
  version++
  operation++
  if (timer) clearInterval(timer)
})
</script>
<style scoped>
.paper-checks {
  color: var(--pa-ink);
}
.check-intro {
  color: var(--pa-muted);
  margin-top: 0;
}
.refresh-checks {
  margin: 0 0 12px;
}
.check-item {
  padding: 18px 0;
  border-top: 1px solid var(--pa-line);
}
.check-item strong {
  font-size: 13px;
  font-weight: 500;
}
.check-item header > span {
  font-size: 13px;
  color: var(--pa-muted);
}
.coverage-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.finding-note {
  color: var(--td-warning-color);
}
header,
.review-row {
  display: flex;
  gap: 10px;
  justify-content: space-between;
  align-items: center;
}
.review-row {
  padding: 8px 0;
  border-top: 1px solid var(--pa-line);
}
small {
  display: block;
  color: var(--pa-muted);
  font-size: 13px;
  overflow-wrap: anywhere;
}
button {
  font: inherit;
  padding: 5px 8px;
  cursor: pointer;
}
p,
details {
  font-size: 13px;
  overflow-wrap: anywhere;
}
[data-state='failed'],
[role='alert'] {
  color: var(--pa-danger);
}
</style>
