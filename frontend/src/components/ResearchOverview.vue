<template>
  <section class="research-overview">
    <header>
      <div>
        <span class="eyebrow">{{ workStatus === 'completed' ? '研究已完成' : '当前运行' }}</span>
        <h2>{{ stage || '等待开始研究' }}</h2>
      </div>
      <button :disabled="loading" @click="load">刷新状态</button>
    </header>
    <div class="run-actions">
      <button v-if="actionLabel" :disabled="actionBusy" @click="emit('action')">
        {{ actionLabel }}</button
      ><button @click="emit('open', 'chat')">回到对话</button>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="state?.last_error" role="alert">{{ state.last_error }}</p>
    <section v-if="workStatus === 'waiting'" class="attention">
      <h3>等待你确认方案</h3>
      <p>请在对话中核对研究方案，确认继续或提出修改。</p>
      <button @click="emit('open', 'chat')">查看方案</button>
    </section>
    <ResearchProgress
      :snapshot="snapshot"
      :work-status="workStatus"
      @object="(object) => emit('object', object)"
      @issue="(id) => emit('issue', id)"
    />
    <section class="generated">
      <h3>已生成结果</h3>
      <p
        v-if="
          !snapshot?.results.length && !state?.comparisons.length && !snapshot?.artifacts.length
        "
      >
        结果会随研究推进出现在这里。
      </p>
      <button
        v-for="r in snapshot?.results || []"
        :key="r.id"
        @click="emit('object', { type: 'result', resultType: 'registered', id: r.id })"
      >
        <strong>{{ r.name }}</strong
        ><span>{{ r.kind === 'figure' ? '图表' : r.value }} {{ r.unit }}</span>
      </button>
      <button
        v-for="c in state?.comparisons || []"
        :key="c.id"
        @click="emit('object', { type: 'result', resultType: 'experiment', id: c.id })"
      >
        <strong>{{ c.task_id }} 实验结果</strong><span>{{ c.reason || c.status }}</span>
      </button>
      <button
        v-for="path in snapshot?.artifacts || []"
        :key="path"
        @click="emit('object', { type: 'file', path })"
      >
        {{ path }}
      </button>
      <button v-if="!state?.comparisons.length" @click="emit('open', 'comparisons')">
        查看历史实验结果
      </button>
    </section>
    <details>
      <summary>审查与待处理事项</summary>
      <ReviewTasks
        :work-id="workId"
        :refresh-key="refreshKey"
        @changed="load"
        @issue="(id) => emit('issue', id)"
        @paper="emit('open', 'paper')"
      />
    </details>
    <template v-if="state">
      <section
        v-if="
          state.research.issues.some(
            (i: any) => i.category === 'execution' && i.status === 'confirmed',
          )
        "
        class="attention"
      >
        <h3>执行问题恢复</h3>
        <div
          v-for="i in state.research.issues.filter(
            (i: any) => i.category === 'execution' && i.status === 'confirmed',
          )"
          :key="i.id"
        >
          <p>{{ i.message }}</p>
          <button :disabled="running || recoveryBusy" @click="previewRecovery(i.id)">
            检查是否已恢复
          </button>
        </div>
      </section>
      <section v-if="recovery" class="attention">
        <h3>恢复检查已完成</h3>
        <p>{{ recovery.validation.checks.length }} 项检查通过。</p>
        <button
          v-if="!recoveryRecorded"
          :disabled="running || recoveryBusy"
          @click="recordRecovery"
        >
          记录恢复并关闭此问题
        </button>
        <p v-else>恢复已记录，可继续研究。</p>
      </section>
      <details>
        <summary>运行记录</summary>
        <p v-for="r in state.requests" :key="r.sequence">
          {{ r.sequence }} {{ r.label || '研究调用' }}：{{ r.http_status || r.state }}
        </p>
        <p v-for="entry in state.execution_resolutions || []" :key="entry.issue.id">
          {{ entry.issue.message }}：{{ entry.issue.status }}
        </p>
        <p v-if="!state.requests.length">暂无请求记录。</p>
      </details>
    </template>
  </section>
</template>
<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from 'vue'
import {
  researchSetupAPI,
  executionRecoveryAPI,
  type ExecutionRecoveryProof,
} from '@/api/researchSetup'
import type { ResearchSnapshot } from '@/types/research'
import type { WorkObject } from '@/stores/workPanels'
import ResearchProgress from './ResearchProgress.vue'
import ReviewTasks from './ReviewTasks.vue'
const props = defineProps<{
    workId: string
    running?: boolean
    refreshKey?: number
    snapshot: ResearchSnapshot | null
    workStatus?: string
    stage?: string
    actionLabel?: string
    actionBusy?: boolean
  }>(),
  emit = defineEmits<{
    open: [kind: string]
    resolved: []
    issue: [id: string]
    object: [object: WorkObject]
    action: []
  }>()
const recovery = ref<ExecutionRecoveryProof | null>(null),
  recoveryBusy = ref(false),
  recoveryRecorded = ref(false)
const state = ref<any>(null),
  error = ref(''),
  loading = ref(false)
let timer: ReturnType<typeof setInterval> | undefined,
  version = 0
async function load() {
  const request = ++version
  loading.value = true
  try {
    const result = await researchSetupAPI.overview(props.workId)
    if (request === version) {
      state.value = result
      error.value = ''
    }
  } catch (e) {
    if (request === version) error.value = e instanceof Error ? e.message : '研究状态暂不可读'
  } finally {
    if (request === version) loading.value = false
  }
}
async function previewRecovery(issue: string) {
  recoveryBusy.value = true
  recovery.value = null
  recoveryRecorded.value = false
  try {
    recovery.value = await executionRecoveryAPI.preview(props.workId, issue)
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '恢复证据核验失败'
  } finally {
    recoveryBusy.value = false
  }
}
async function recordRecovery() {
  if (!recovery.value) return
  recoveryBusy.value = true
  try {
    recovery.value = await executionRecoveryAPI.resolve(
      props.workId,
      recovery.value.issue_id,
      recovery.value.proof_hash,
    )
    recoveryRecorded.value = true
    emit('resolved')
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '恢复证明未保存'
  } finally {
    recoveryBusy.value = false
  }
}
watch(
  () => props.workId,
  () => {
    state.value = null
    recovery.value = null
    load()
  },
  { immediate: true },
)
watch(
  () => props.refreshKey,
  () => load(),
)
onMounted(() => {
  timer = setInterval(() => {
    if (props.running && !loading.value) load()
  }, 5000)
})
onUnmounted(() => {
  version++
  if (timer) clearInterval(timer)
})
</script>
<style scoped>
.research-overview {
  padding: 28px;
  overflow: auto;
  flex: 1;
  color: var(--pa-ink);
  max-width: 960px;
  box-sizing: border-box;
  width: 100%;
  margin: 0 auto;
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}
.eyebrow {
  font-size: 12px;
  color: var(--pa-muted);
}
h2 {
  font-size: 21px;
  line-height: 1.5;
  margin: 6px 0 12px;
}
h3 {
  font-size: 15px;
}
p {
  font-size: 13px;
  line-height: 1.8;
}
button {
  border: 1px solid var(--pa-line);
  border-radius: 6px;
  padding: 8px 12px;
  background: var(--pa-surface);
  color: inherit;
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: wait;
}
.run-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 18px;
}
.generated {
  border-top: 1px solid var(--pa-line);
  padding: 16px 0;
}
.generated button {
  display: flex;
  width: 100%;
  justify-content: space-between;
  text-align: left;
  gap: 12px;
  margin: 8px 0;
}
.generated span {
  font-size: 13px;
  overflow-wrap: anywhere;
  color: var(--pa-muted);
}
details {
  margin: 16px 0;
  font-size: 13px;
}
summary {
  cursor: pointer;
  color: var(--pa-muted);
}
.attention {
  padding: 14px;
  background: var(--pa-surface-muted);
  border-radius: 8px;
  margin: 12px 0;
}
[role='alert'] {
  color: var(--pa-danger);
}
</style>
