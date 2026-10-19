<template>
  <section class="research-skills">
    <header>
      <h2>{{ isZh ? '科研技能' : 'Research skills' }}</h2>
      <button :disabled="busy" @click="load">{{ isZh ? '刷新' : 'Refresh' }}</button>
    </header>
    <p>
      {{
        isZh
          ? '选择研究可使用的方法，按需查看步骤与来源。'
          : 'Method instructions and sources load on demand. These are curated static skills; cross-research experience is managed separately.'
      }}
    </p>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="state">
      <label
        >{{ isZh ? '研究中的选择方式' : 'Selection policy' }}
        <select v-model="policy.mode" :disabled="busy || disabled">
          <option value="auto">
            {{
              isZh
                ? '自动检索已有方法（排除禁用项）'
                : 'Automatic method selection (excluding disabled skills)'
            }}
          </option>
          <option value="selected">
            {{ isZh ? '仅允许我选中的技能' : 'Only the skills I select' }}
          </option>
        </select>
      </label>
      <p v-if="state.has_research">
        {{
          isZh
            ? '保存变更会使当前计算任务及下游结果失效；原证据保留在历史中。之后点击恢复研究，按新选择重新设计和求解。'
            : 'Saving changes invalidates computation and downstream results, preserving historical evidence. Resume research to redesign and solve with the new selection.'
        }}
      </p>
      <article v-for="skill in state.catalog" :key="skill.key" class="skill-card">
        <header>
          <h3>{{ skill.name }}</h3>
          <span>{{ skill.category }}</span>
        </header>
        <p>{{ skill.use_when }}</p>
        <p>{{ isZh ? '数据要求：' : 'Data requirements: ' }}{{ skill.needs }}</p>
        <div class="skill-controls">
          <label v-if="policy.mode === 'selected'"
            ><input
              type="checkbox"
              :checked="policy.selected.includes(skill.key)"
              :disabled="busy || disabled"
              @change="toggleSelected(skill.key, ($event.target as HTMLInputElement).checked)"
            />{{ isZh ? '选用' : 'Select' }}</label
          >
          <label
            ><input
              type="checkbox"
              :checked="policy.disabled.includes(skill.key)"
              :disabled="busy || disabled"
              @change="toggleDisabled(skill.key, ($event.target as HTMLInputElement).checked)"
            />{{ isZh ? '禁用' : 'Disable' }}</label
          >
          <button :disabled="busy" @click="showDetail(skill.key)">
            {{ isZh ? '查看步骤和来源' : 'View steps and sources' }}
          </button>
        </div>
        <div v-if="details[skill.key]" class="skill-detail">
          <MarkdownRenderer :content="details[skill.key]!.instructions || ''" />
          <p>{{ isZh ? '注意：' : 'Pitfalls: ' }}{{ skill.pitfalls }}</p>
          <ul>
            <li v-for="source in skill.sources" :key="source.url">
              <a :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.title }}</a>
            </li>
          </ul>
        </div>
      </article>
      <button
        class="save-skills"
        :disabled="busy || disabled || (policy.mode === 'selected' && !policy.selected.length)"
        @click="save"
      >
        {{ busy ? (isZh ? '保存中…' : 'Saving…') : isZh ? '保存技能选择' : 'Save skill selection' }}
      </button>
      <p v-if="saved" role="status">{{ saved }}</p>
      <button v-if="affected.length" :disabled="disabled || busy" @click="$emit('resume')">
        {{ isZh ? '按新技能恢复研究' : 'Resume with selected skills' }}
      </button>
      <details v-if="state.usage.length">
        <summary>
          {{ isZh ? '各任务实际引用的技能版本' : 'Skills referenced by task designs' }}
        </summary>
        <p v-for="task in state.usage" :key="task.task_id">
          {{ task.task_id }} · {{ task.validity }} ·
          {{ task.skills.map((s) => `${s.key} v${s.version}`).join(', ') || '—' }}
        </p>
      </details>
    </template>
  </section>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import { skillsAPI, type ResearchSkill, type SkillPolicy, type SkillState } from '@/api/skills'
import { useStudioCopy } from '@/lang/studio'
import MarkdownRenderer from './MarkdownRenderer.vue'
const props = defineProps<{ workId: string; disabled?: boolean }>()
const emit = defineEmits<{ updated: []; resume: [] }>()
const { isZh } = useStudioCopy()
const state = ref<SkillState | null>(null),
  policy = ref<SkillPolicy>({ mode: 'auto', selected: [], disabled: [] }),
  details = ref<Record<string, ResearchSkill>>({}),
  busy = ref(false),
  error = ref(''),
  saved = ref(''),
  affected = ref<string[]>([])
function failure(e: unknown) {
  error.value = e instanceof Error ? e.message : String(e)
}
async function load() {
  try {
    error.value = ''
    state.value = await skillsAPI.read(props.workId)
    policy.value = {
      ...state.value.policy,
      selected: [...state.value.policy.selected],
      disabled: [...state.value.policy.disabled],
    }
  } catch (e) {
    failure(e)
  }
}
function toggleSelected(key: string, checked: boolean) {
  policy.value.selected = policy.value.selected.filter((k) => k !== key)
  if (checked) {
    policy.value.selected.push(key)
    policy.value.disabled = policy.value.disabled.filter((k) => k !== key)
  }
}
function toggleDisabled(key: string, checked: boolean) {
  policy.value.disabled = policy.value.disabled.filter((k) => k !== key)
  if (checked) {
    policy.value.disabled.push(key)
    policy.value.selected = policy.value.selected.filter((k) => k !== key)
  }
}
async function showDetail(key: string) {
  if (details.value[key]) {
    delete details.value[key]
    return
  }
  try {
    details.value[key] = await skillsAPI.detail(props.workId, key)
  } catch (e) {
    failure(e)
  }
}
async function save() {
  if (!state.value || busy.value || props.disabled) return
  busy.value = true
  error.value = ''
  saved.value = ''
  try {
    const result = await skillsAPI.save(props.workId, state.value.version, {
      ...policy.value,
      selected: policy.value.mode === 'auto' ? [] : policy.value.selected,
    })
    affected.value = result.affected
    await load()
    saved.value = isZh.value
      ? `已保存${result.affected.length ? '；待重新计算：' + result.affected.join(', ') : ''}`
      : `Saved${result.affected.length ? '; recompute: ' + result.affected.join(', ') : ''}`
    emit('updated')
  } catch (e) {
    failure(e)
  } finally {
    busy.value = false
  }
}
watch(
  () => props.workId,
  () => {
    details.value = {}
    affected.value = []
    saved.value = ''
    load()
  },
  { immediate: true },
)
</script>
<style scoped>
.research-skills {
  flex: 1;
  overflow: auto;
  padding: 24px;
  color: var(--pa-ink);
}
header,
.skill-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
}
h2,
h3 {
  margin: 0;
  font-size: 17px;
}
p {
  line-height: 1.8;
}
.skill-card {
  padding: 18px;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-card);
  margin: 16px 0;
}
.skill-detail {
  border-top: 1px solid var(--pa-line);
  margin-top: 16px;
  padding-top: 16px;
}
button,
select {
  padding: 8px 12px;
  font: inherit;
  color: var(--pa-ink);
  background: var(--pa-surface);
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-control);
}
button {
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
[role='alert'] {
  color: var(--pa-danger);
}
a {
  color: var(--pa-accent);
}
label {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.save-skills {
  margin-right: 12px;
}
details {
  margin-top: 20px;
}
</style>
