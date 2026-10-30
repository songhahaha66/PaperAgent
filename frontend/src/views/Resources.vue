<template>
  <LibraryShell
    :title="isZh ? '资源库' : 'Resources'"
    :description="
      isZh
        ? '查阅研究方法与论文模板，在具体研究方案中记录实际选用。'
        : 'Methods and paper templates; actual choices belong to research plans.'
    "
  >
    <nav class="resource-tabs" aria-label="资源类型">
      <button :aria-pressed="tab === 'methods'" @click="setTab('methods')">
        {{ isZh ? '研究方法' : 'Methods' }}
      </button>
      <button :aria-pressed="tab === 'templates'" @click="setTab('templates')">
        {{ isZh ? '论文模板' : 'Paper templates' }}
      </button>
    </nav>
    <Template v-if="tab === 'templates'" embedded />
    <section v-else class="methods-library">
      <p v-if="error" role="alert">{{ error }} <button @click="load">重试</button></p>
      <t-loading v-if="loading" text="正在读取方法…" />
      <div v-else class="methods-layout">
        <nav class="method-list" aria-label="研究方法">
          <label class="method-search"
            ><t-icon name="search" /><input v-model="query" placeholder="查找方法、用途或条件"
          /></label>
          <button
            v-for="item in filtered"
            :key="item.key"
            :aria-current="selected === item.key ? 'true' : undefined"
            @click="select(item.key)"
          >
            <strong>{{ item.name }}</strong>
            <span>{{ item.category }}</span>
            <span>{{ item.needs }}</span>
          </button>
          <p v-if="!filtered.length" class="muted">未找到匹配的方法。</p>
        </nav>
        <article v-if="method" class="method-detail">
          <header>
            <div>
              <p class="muted">{{ method.category }}</p>
              <p class="muted">版本 {{ method.version }}</p>
              <h2>{{ method.name }}</h2>
            </div>
          </header>
          <section>
            <h3>何时使用</h3>
            <p>{{ method.use_when }}</p>
            <p class="muted">数据条件：{{ method.needs }}</p>
          </section>
          <section>
            <h3>具体方法</h3>
            <MarkdownRenderer v-if="detail?.instructions" :content="detail.instructions" />
            <ol v-else>
              <li v-for="step in method.steps" :key="step">{{ step }}</li>
            </ol>
          </section>
          <section>
            <h3>注意事项</h3>
            <p>{{ method.pitfalls }}</p>
          </section>
          <section>
            <h3>来源</h3>
            <a
              v-for="source in method.sources"
              :key="source.url"
              :href="source.url"
              target="_blank"
              rel="noopener noreferrer"
              >{{ source.title }}<t-icon name="jump"
            /></a>
          </section>
          <p class="method-note">
            方法由具体方案选用，浏览资源不会改变正在执行的研究。步骤与版本随实验记录。
          </p>
        </article>
      </div>
    </section>
  </LibraryShell>
</template>
<script setup lang="ts">
import { computed, ref, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import LibraryShell from '@/components/LibraryShell.vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import Template from './Template.vue'
import { skillsAPI, type ResearchSkill } from '@/api/skills'
import { useStudioCopy } from '@/lang/studio'
const { isZh } = useStudioCopy()
const route = useRoute(),
  router = useRouter()
const tab = computed(() => (route.query.tab === 'templates' ? 'templates' : 'methods'))
const catalog = ref<ResearchSkill[]>([]),
  selected = ref(''),
  query = ref(''),
  error = ref(''),
  loading = ref(false)
const detail = ref<ResearchSkill | null>(null)
const method = computed(() => catalog.value.find((item) => item.key === selected.value))
const filtered = computed(() =>
  catalog.value.filter((item) =>
    `${item.name} ${item.use_when} ${item.needs} ${item.category}`
      .toLowerCase()
      .includes(query.value.toLowerCase()),
  ),
)
let request = 0
function setTab(value: string) {
  router.replace({ path: '/resources', query: { tab: value } })
}
function select(key: string) {
  router.replace({ path: '/resources', query: { method: key } })
}
async function load() {
  loading.value = true
  error.value = ''
  try {
    catalog.value = (await skillsAPI.catalog()).methods
    selected.value = catalog.value.some((item) => item.key === route.query.method)
      ? String(route.query.method)
      : catalog.value[0]?.key || ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '方法资源暂不可用'
  } finally {
    loading.value = false
  }
}
watch(
  () => route.query.method,
  (key) => {
    if (catalog.value.some((item) => item.key === key)) selected.value = String(key)
  },
)
watch(selected, async (key) => {
  const seq = ++request
  detail.value = null
  if (!key) return
  try {
    const value = await skillsAPI.method(key)
    if (seq === request) detail.value = value
  } catch (e) {
    if (seq === request) error.value = e instanceof Error ? e.message : '方法说明暂不可用'
  }
})
onMounted(load)
</script>
<style scoped>
.resource-tabs {
  display: flex;
  gap: 20px;
  padding: 0 32px;
  border-bottom: 1px solid var(--pa-line);
}
button {
  font: inherit;
  cursor: pointer;
  color: var(--pa-ink);
}
.resource-tabs button {
  padding: 15px 0;
  background: transparent;
  border: 0;
  border-bottom: 2px solid transparent;
}
.resource-tabs button[aria-pressed='true'] {
  border-color: var(--pa-accent);
  color: var(--pa-accent);
}
.methods-library {
  padding: 28px 32px;
}
.methods-layout {
  display: grid;
  grid-template-columns: minmax(220px, 30%) minmax(0, 1fr);
  gap: 36px;
}
.method-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-self: start;
}
.method-list > button {
  text-align: left;
  border: 1px solid transparent;
  border-radius: var(--pa-radius-control);
  padding: 16px;
  background: transparent;
}
.method-list > button:hover {
  background: var(--pa-surface-hover);
}
.method-list > button[aria-current] {
  background: var(--pa-surface-muted);
  border-color: var(--pa-line);
}
.method-list strong,
.method-list span {
  display: block;
}
.method-list strong {
  font-weight: 500;
}
.method-list span {
  color: var(--pa-muted);
  font-size: 13px;
  margin-top: 6px;
}
.method-search {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-control);
  padding: 10px 12px;
  margin-bottom: 8px;
}
.method-search input {
  width: 100%;
  border: 0;
  outline: 0;
  font: inherit;
  background: transparent;
  color: var(--pa-ink);
  min-width: 0;
}
.method-detail {
  max-width: 760px;
  min-width: 0;
}
h2 {
  font-size: 22px;
  margin: 0;
  font-weight: 500;
}
h3 {
  font-size: 15px;
  font-weight: 500;
  margin: 0 0 10px;
}
.method-detail section {
  padding: 22px 0;
  border-bottom: 1px solid var(--pa-line);
}
.method-detail p,
li {
  line-height: 1.85;
}
.muted,
.method-note {
  color: var(--pa-muted);
  font-size: 13px;
}
.method-detail header .muted {
  margin: 0;
}
.method-note {
  margin-top: 24px;
}
a {
  display: flex;
  gap: 8px;
  align-items: center;
  color: var(--pa-accent);
  text-decoration: none;
}
@media (max-width: 850px) {
  .methods-layout {
    grid-template-columns: 1fr;
    gap: 25px;
  }
  .method-list {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .method-search {
    grid-column: 1/-1;
  }
}
@media (max-width: 540px) {
  .methods-library {
    padding: 20px;
  }
  .resource-tabs {
    padding: 0 20px;
  }
  .method-list {
    grid-template-columns: 1fr;
  }
}
</style>
