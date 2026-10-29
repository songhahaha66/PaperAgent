<template>
  <section class="research-references">
    <h3>本次使用的方法</h3>
    <p class="references-note">
      记录具体方案实际读取的方法版本。资源库的变化不会改写历史引用。
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
    </article>
  </section>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import { skillsAPI, type SkillState } from '@/api/skills'
import type { ResearchSnapshot } from '@/types/research'
const props = defineProps<{ workId: string; snapshot?: ResearchSnapshot | null }>()
const emit = defineEmits<{ revise: [message: string, task: string] }>()
const methods = ref<SkillState | null>(null), error = ref('')
const taskTitle = (id: string) =>
  props.snapshot?.experiments?.find((task) => task.task_id === id)?.objective || '研究方案'
let sequence = 0
async function load() {
  const seq = ++sequence
  error.value = ''
  const [skillResult] = await Promise.allSettled([skillsAPI.read(props.workId)])
  if (seq !== sequence) return
  if (skillResult.status === 'fulfilled') methods.value = skillResult.value
  else {
    methods.value = null
    error.value = '本次方法引用暂不可读。'
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
[role='alert'] {
  color: var(--pa-danger);
}
</style>
