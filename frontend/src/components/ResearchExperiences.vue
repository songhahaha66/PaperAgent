<template>
  <section class="experience-library">
    <div class="library-tools">
      <label class="experience-search"
        ><t-icon name="search" /><input
          v-model="query"
          placeholder="检索问题、方法、条件或来源"
          aria-label="检索个人经验"
      /></label>
      <label class="reuse-policy"
        ><input
          type="checkbox"
          :checked="state?.enabled"
          :disabled="busy || !state"
          @change="setPolicy(($event.target as HTMLInputElement).checked)"
        />后续研究检索已采用的经验</label
      >
      <t-dropdown
        :options="[{ content: '整理历史研究中的候选', value: 'rebuild', onClick: rebuild }]"
        trigger="click"
        ><button :disabled="busy" aria-label="更多经验操作"><t-icon name="more" /></button
      ></t-dropdown>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <nav class="experience-filters" aria-label="经验状态">
      <button
        v-for="filter in filters"
        :key="filter.key"
        :aria-pressed="filterKey === filter.key"
        @click="filterKey = filter.key"
      >
        {{ filter.label }} <span>{{ count(filter.key) }}</span>
      </button>
    </nav>
    <div v-if="loading && !state" class="library-empty"><t-loading text="正在检索经验…" /></div>
    <div v-else-if="!filtered.length" class="library-empty">
      <t-icon name="books" />
      <h2>{{ query ? '没有匹配的经验' : '这里还没有经验' }}</h2>
      <p>研究记录可以产生候选；只有来源与证据经过验证、且由你采用的版本，才会供后续研究参考。</p>
    </div>
    <div v-else class="experience-layout">
      <nav class="experience-list" aria-label="经验列表">
        <button
          v-for="entry in filtered"
          :key="entry.id"
          :aria-current="selected?.id === entry.id ? 'true' : undefined"
          @click="selectedId = entry.id"
        >
          <strong>{{
            entry.card.question || entry.summary?.methods.join('、') || '研究经验'
          }}</strong
          ><span
            >v{{ entry.version }} · {{ statusLabel(entry.status)
            }}{{ entry.enabled ? '' : ' · 已停用' }}</span
          ><small>{{ entry.summary?.methods.join('、') || '方法有待核对' }}</small>
        </button>
      </nav>
      <article v-if="selected" :id="'experience-' + selected.id" class="experience-detail">
        <header>
          <p class="muted">{{ levelLabel(selected.card.level) }}</p>
          <h2>{{ selected.card.question || '研究经验' }}</h2>
          <div class="experience-state">
            <span>版本 {{ selected.version }}</span
            ><span>{{ statusLabel(selected.status) }}</span
            ><span v-if="!selected.enabled">已停用</span
            ><span v-else-if="selected.available">可供后续研究检索</span>
          </div>
        </header>
        <section>
          <h3>适用条件</h3>
          <p>{{ selected.summary?.methods.join('、') || '方法尚未明确' }}</p>
          <p class="muted">
            {{ selected.summary?.data || '数据来源待核对' }} ·
            {{ selected.summary?.structure || '结构条件待核对' }}
          </p>
          <p v-for="condition in selected.card.applicable" :key="condition">{{ condition }}</p>
          <p v-for="condition in selected.card.unknown_conditions" :key="condition" class="muted">
            {{ condition }}
          </p>
        </section>
        <section>
          <h3>来源</h3>
          <RouterLink :to="'/work/' + encodeURIComponent(selected.card.source.work_id)"
            >打开来源研究<t-icon name="jump"
          /></RouterLink>
          <p class="muted">
            来源方案第 {{ selected.card.source.revision }} 版。来源、内容版本和验证记录一同保留。
          </p>
        </section>
        <section>
          <h3>
            {{
              selected.status === 'candidate' || selected.status === 'withdrawn'
                ? '验证与采用'
                : '验证依据'
            }}
          </h3>
          <p v-if="!['verified', 'counterexample'].includes(selected.card.level)">
            这条候选尚缺可核验的契约证据，不能采用为可复用经验。
          </p>
          <p v-else>核对的是来源研究中的适用条件与契约证据，不代表已证明对所有项目有效。</p>
          <p v-for="check in selected.card.checks" :key="check.check_id">
            <span class="check-label">{{ checkLabel(check.outcome) }}</span
            >{{ check.message }}
          </p>
          <div class="detail-actions">
            <button
              v-if="
                (selected.status === 'candidate' || selected.status === 'withdrawn') &&
                ['verified', 'counterexample'].includes(selected.card.level)
              "
              :disabled="busy"
              @click="transition(selected, 'validate')"
            >
              核对来源与契约证据</button
            ><button
              v-if="selected.status === 'validated'"
              class="primary"
              :disabled="busy"
              @click="transition(selected, 'adopt')"
            >
              采用此版本</button
            ><button
              v-if="selected.status === 'adopted'"
              :disabled="busy"
              @click="transition(selected, 'withdraw')"
            >
              撤回采用
            </button>
          </div>
        </section>
        <section v-if="selected.card.steps.length || selected.card.observations.length">
          <h3>方法步骤与观察</h3>
          <ol>
            <li v-for="(step, i) in selected.card.steps" :key="i">{{ step }}</li>
          </ol>
          <p v-for="(observation, i) in selected.card.observations" :key="i">{{ observation }}</p>
          <p
            v-if="selected.card.error && !['None', 'null'].includes(selected.card.error)"
            class="muted"
          >
            来源执行记录：{{ selected.card.error }}
          </p>
          <details>
            <summary>不适用的情形</summary>
            <p v-for="condition in selected.card.not_applicable" :key="condition">
              {{ condition }}
            </p>
          </details>
        </section>
        <section>
          <h3>版本与采用历史</h3>
          <p>新内容需重新进入候选；项目保留自己实际读取的版本。</p>
          <p v-for="(event, i) in selected.history" :key="i" class="muted">
            v{{ event.version }} · {{ statusLabel(event.event) }}
          </p>
        </section>
        <footer>
          <button :disabled="busy" @click="change(selected)">
            {{ selected.enabled ? '停用后续检索' : '重新启用' }}</button
          ><button :disabled="busy" @click="remove(selected)">删除…</button>
          <p class="muted">
            停用阻止后续检索，已经使用过的项目保留版本来源。删除后不会因整理历史或恢复研究自动找回。
          </p>
        </footer>
      </article>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { DialogPlugin } from 'tdesign-vue-next'
import { experienceAPI, type ExperienceState, type ExperienceCard } from '@/api/experiences'
const query = ref(''),
  filterKey = ref('all'),
  selectedId = ref('')
const state = ref<ExperienceState | null>(null),
  busy = ref(false),
  loading = ref(false),
  error = ref(''),
  notice = ref('')
const filters = [
  { key: 'all', label: '全部' },
  { key: 'candidate', label: '候选与验证' },
  { key: 'adopted', label: '已采用' },
  { key: 'inactive', label: '已停用与撤回' },
]
const matches = (entry: ExperienceCard, key: string) =>
  key === 'all' ||
  (key === 'candidate'
    ? ['candidate', 'validated'].includes(entry.status) && entry.enabled
    : key === 'adopted'
      ? entry.status === 'adopted' && entry.enabled
      : !entry.enabled || entry.status === 'withdrawn')
const filtered = computed(
  () => state.value?.cards.filter((entry) => matches(entry, filterKey.value)) || [],
)
const selected = computed(
  () => filtered.value.find((entry) => entry.id === selectedId.value) || filtered.value[0],
)
const count = (key: string) => state.value?.cards.filter((entry) => matches(entry, key)).length || 0
const statusLabel = (status: string) =>
  ({
    candidate: '待验证候选',
    validated: '已验证，待采用',
    adopted: '已采用',
    withdrawn: '已撤回',
    migrated_candidate: '历史迁入候选',
  })[status] || '状态待核对'
const levelLabel = (level: string) =>
  ({
    verified: '来源实验有通过的检查证据',
    counterexample: '来源实验记录了反例',
    execution_error: '来源执行失败，不能证明方法无效',
    unconfirmed: '未经确认的观察',
  })[level] || '证据待核对'
const checkLabel = (status: string) =>
  ({ passed: '检查通过', failed: '检查发现反例', inconclusive: '证据不足' })[status] || '待核对'
let generation = 0,
  searchTimer: ReturnType<typeof setTimeout> | undefined
async function load() {
  const seq = ++generation
  loading.value = true
  error.value = ''
  try {
    const value = await experienceAPI.read(query.value)
    if (seq !== generation) return
    state.value = value
    if (!selectedId.value && typeof window !== 'undefined')
      selectedId.value = window.location.hash.replace('#experience-', '')
  } catch (e) {
    if (seq === generation) error.value = e instanceof Error ? e.message : '经验服务暂不可用'
  } finally {
    if (seq === generation) loading.value = false
  }
}
async function action(fn: () => Promise<unknown>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await fn()
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '经验操作未完成'
  } finally {
    busy.value = false
  }
}
async function setPolicy(enabled: boolean) {
  await action(() => experienceAPI.policy(enabled))
}
async function rebuild() {
  await action(async () => {
    const result = await experienceAPI.rebuild()
    notice.value = `已整理 ${result.updated} 条候选；有 ${result.unavailable_sources} 个来源暂不可读。候选不会自动采用。`
  })
}
async function change(entry: ExperienceCard) {
  await action(() => experienceAPI.change(entry.id, !entry.enabled))
}
async function transition(entry: ExperienceCard, operation: 'validate' | 'adopt' | 'withdraw') {
  await action(() => experienceAPI.transition(entry.id, operation, entry.version))
}
function remove(entry: ExperienceCard) {
  const dialog = DialogPlugin.confirm({
    header: '删除这条个人经验？',
    body: '个人库中的内容和内容历史会被删除，历史项目保留已经使用过的版本来源。删除后不会自动重建。',
    confirmBtn: { content: '删除', theme: 'danger' },
    cancelBtn: '取消',
    onConfirm: async () => {
      await action(() => experienceAPI.delete(entry.id))
      dialog.destroy()
    },
  })
}
watch(query, () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(load, 250)
})
void load()
onUnmounted(() => {
  generation++
  clearTimeout(searchTimer)
})
</script>
<style scoped>
.experience-library {
  max-width: 1300px;
  margin: auto;
  padding: 26px 32px 48px;
  color: var(--pa-ink);
}
.library-tools {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
}
.experience-search {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-control);
  padding: 10px 12px;
  min-width: 240px;
  flex: 1;
  max-width: 440px;
}
.experience-search input {
  width: 100%;
  min-width: 0;
  border: 0;
  outline: 0;
  font: inherit;
  color: inherit;
  background: transparent;
}
.reuse-policy {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--pa-muted);
}
button {
  font: inherit;
  color: inherit;
  cursor: pointer;
  background: var(--pa-surface);
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-control);
  padding: 8px 11px;
}
button:hover {
  background: var(--pa-surface-hover);
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
.experience-filters {
  display: flex;
  gap: 22px;
  margin: 24px 0;
  border-bottom: 1px solid var(--pa-line);
  flex-wrap: wrap;
}
.experience-filters button {
  border: 0;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  padding: 12px 0;
  font-size: 13px;
}
.experience-filters button[aria-pressed='true'] {
  border-color: var(--pa-accent);
  color: var(--pa-accent);
}
.experience-filters span {
  font-size: 12px;
  color: var(--pa-muted);
  margin-left: 6px;
}
.experience-layout {
  display: grid;
  grid-template-columns: minmax(230px, 32%) minmax(0, 1fr);
  gap: 36px;
}
.experience-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-self: start;
}
.experience-list button {
  padding: 18px;
  border-color: transparent;
  text-align: left;
}
.experience-list button[aria-current] {
  border-color: var(--pa-line);
  background: var(--pa-surface-muted);
}
.experience-list strong,
.experience-list span,
.experience-list small {
  display: block;
}
.experience-list strong {
  font-size: 14px;
  line-height: 1.7;
  font-weight: 500;
}
.experience-list span,
.experience-list small {
  font-size: 12px;
  margin-top: 8px;
  color: var(--pa-muted);
}
.experience-detail {
  max-width: 760px;
  min-width: 0;
  overflow-wrap: anywhere;
}
h2 {
  font-size: 22px;
  font-weight: 500;
  margin: 0 0 12px;
  line-height: 1.6;
}
h3 {
  font-size: 15px;
  font-weight: 500;
  margin: 0 0 12px;
}
.muted,
.experience-state {
  color: var(--pa-muted);
  font-size: 12px;
}
.experience-state {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
}
.experience-detail section {
  border-bottom: 1px solid var(--pa-line);
  padding: 23px 0;
}
.experience-detail p,
.experience-detail li {
  line-height: 1.9;
  font-size: 13px;
}
.experience-detail a {
  color: var(--pa-accent);
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.detail-actions,
footer {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
footer {
  padding-top: 24px;
}
footer p {
  width: 100%;
}
.primary {
  background: var(--pa-accent);
  color: var(--pa-on-accent);
  border-color: var(--pa-accent);
}
.check-label {
  color: var(--pa-muted);
  margin-right: 10px;
}
summary {
  cursor: pointer;
  font-size: 13px;
  color: var(--pa-muted);
}
.library-empty {
  min-height: 260px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: 14px;
  color: var(--pa-muted);
}
.library-empty .t-icon {
  font-size: 32px;
}
.library-empty h2 {
  font-size: 18px;
  color: var(--pa-ink);
  margin: 0;
}
.library-empty p {
  max-width: 420px;
  line-height: 1.8;
  margin: 0;
}
[role='alert'] {
  color: var(--pa-danger);
  font-size: 13px;
}
@media (max-width: 900px) {
  .experience-layout {
    grid-template-columns: 1fr;
    gap: 28px;
  }
  .experience-list {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 540px) {
  .experience-library {
    padding: 20px;
  }
  .experience-list {
    grid-template-columns: 1fr;
  }
  .experience-filters {
    gap: 15px;
  }
}
</style>
