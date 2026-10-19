<template>
  <section class="research-references">
    <h3>本次使用的方法与经验</h3>
    <p class="references-note">
      记录具体方案实际读取的版本。资源库和个人经验的变化不会改写历史引用。
    </p>
    <p v-if="error" role="alert">{{ error }} <button @click="load">重试</button></p>
    <article v-for="entry in methods?.usage || []" :key="entry.task_id">
      <h4>{{ taskTitle(entry.task_id) }}</h4>
      <div v-for="skill in entry.skills" :key="skill.key" class="method-ref">
        <RouterLink :to="{ path: '/resources', query: { method: skill.key } }">{{
          methods?.catalog.find((item) => item.key === skill.key)?.name || '方法说明'
        }}</RouterLink
        ><span>v{{ skill.version }}</span>
      </div>
      <p v-if="!entry.skills.length" class="references-note">此方案未记录独立的方法资源版本。</p>
      <button
        @click="
          emit(
            'revise',
            `请修订「${taskTitle(entry.task_id)}」的研究方法，先说明影响范围及新旧方案差异。`,
            entry.task_id,
          )
        "
      >
        提出方案变更
      </button>
    </article>
    <article v-for="entry in usage" :key="entry.attempt_id">
      <h4>
        {{ taskTitle(entry.task_id) }} · {{ entry.published ? '已提交实验' : '尚未发布的实验' }}
      </h4>
      <div v-for="ref in entry.refs" :key="ref.id + ref.stage" class="experience-ref">
        <strong>{{ source(entry, ref)?.title || '历史经验版本' }} · v{{ ref.version }}</strong>
        <p>{{ ref.reason }}</p>
        <p class="references-note">
          用于{{ stageLabel(ref.stage) }}{{ entry.available ? '' : ' · 当前来源或复用状态已改变' }}
        </p>
        <RouterLink
          v-if="source(entry, ref)?.source_work_id"
          :to="'/work/' + encodeURIComponent(source(entry, ref)!.source_work_id!)"
          >打开来源研究</RouterLink
        >
      </div>
    </article>
    <p v-if="!usage.length" class="references-note">本项目尚未记录个人经验的使用。</p>
    <p v-if="usageError" role="alert">{{ usageError }}</p>
  </section>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import { skillsAPI, type SkillState } from '@/api/skills'
import { experienceAPI, type ExperienceState, type ExperienceRef } from '@/api/experiences'
import type { ResearchSnapshot } from '@/types/research'
const props = defineProps<{ workId: string; snapshot?: ResearchSnapshot | null }>()
const emit = defineEmits<{ revise: [message: string, task: string] }>()
const methods = ref<SkillState | null>(null),
  usage = ref<NonNullable<ExperienceState['usage']>>([]),
  usageError = ref(''),
  error = ref('')
const taskTitle = (id: string) =>
  props.snapshot?.tasks?.find((task) => task.task_id === id)?.objective || '研究方案'
const stageLabel = (stage: string) =>
  ({ design: '方案制定', solve: '实验求解', repair: '问题修复' })[stage] || '本次研究'
const source = (entry: NonNullable<ExperienceState['usage']>[number], ref: ExperienceRef) =>
  entry.sources?.find((item) => item.id === ref.id && item.version === ref.version)
let sequence = 0
async function load() {
  const seq = ++sequence
  error.value = ''
  const responses = await Promise.allSettled([
    skillsAPI.read(props.workId),
    experienceAPI.usage(props.workId),
  ])
  if (seq !== sequence) return
  const [skillResult, experienceResult] = responses
  if (skillResult.status === 'fulfilled') methods.value = skillResult.value
  else {
    methods.value = null
    error.value = '本次方法引用暂不可读。'
  }
  if (experienceResult.status === 'fulfilled') {
    usage.value = experienceResult.value.usage || []
    usageError.value = experienceResult.value.usage_error || ''
  } else {
    usage.value = []
    usageError.value = '本次经验引用暂不可读。'
  }
}
watch(() => [props.workId, props.snapshot?.commit_seq], load, { immediate: true })
</script>
<style scoped>
.research-references {
  margin-top: 28px;
  padding-top: 20px;
  border-top: 1px solid var(--pa-line);
}
h3 {
  font-size: 16px;
  font-weight: 500;
  margin: 0 0 10px;
}
h4 {
  font-size: 14px;
  font-weight: 500;
  margin: 0 0 12px;
}
article {
  padding: 18px 0;
  border-bottom: 1px solid var(--pa-line);
}
p {
  line-height: 1.8;
  font-size: 13px;
}
.references-note {
  font-size: 12px;
  color: var(--pa-muted);
}
.method-ref {
  display: flex;
  gap: 12px;
  align-items: center;
  margin: 8px 0;
  font-size: 13px;
}
.method-ref span {
  color: var(--pa-muted);
}
a {
  color: var(--pa-accent);
  text-decoration: none;
  font-size: 13px;
}
button {
  font: inherit;
  font-size: 12px;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-control);
  background: var(--pa-surface);
  color: var(--pa-ink);
  padding: 7px 10px;
  cursor: pointer;
  margin-top: 10px;
}
.experience-ref {
  padding: 10px 0;
}
.experience-ref strong {
  font-size: 13px;
  font-weight: 500;
}
[role='alert'] {
  color: var(--pa-danger);
}
</style>
