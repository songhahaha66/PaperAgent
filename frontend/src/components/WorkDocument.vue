<template>
  <section ref="scroller" class="work-document" @scroll="rememberScroll">
    <div v-if="loading" class="document-empty" role="status"><t-loading :text="ui.loading" /></div>
    <div v-else-if="error" class="document-empty">
      <t-icon name="file-1" />
      <h2>{{ isZh ? '文件暂时无法打开' : 'Could not open file' }}</h2>
      <t-button theme="default" @click="load">{{ isZh ? '重试' : 'Retry' }}</t-button>
    </div>
    <div v-else-if="!data" class="document-empty">
      <t-icon name="file-1" />
      <h2>{{ isZh ? '论文尚未生成' : 'Your paper is not ready yet' }}</h2>
      <p>{{ ui.previewEmptyHint }}</p>
      <t-button theme="default" variant="text" @click="$emit('open-files')">{{
        ui.files
      }}</t-button>
    </div>
    <article v-else class="document-sheet">
      <header v-if="showToolbar" class="document-toolbar">
        <span :title="loadedPath">{{ paperLabel(loadedPath, isZh) || loadedPath }}</span>
        <div>
          <t-button v-if="paper" size="small" theme="default" @click="$emit('edit-paper')">{{
            isZh ? '正文与修改' : 'Text and edits'
          }}</t-button>
          <t-button
            v-if="paper && latexSource"
            size="small"
            theme="default"
            variant="text"
            @click="$emit('open-file', 'paper.tex')"
            >{{ isZh ? 'LaTeX 源码' : 'LaTeX source' }}</t-button
          >
          <t-button
            size="small"
            theme="default"
            variant="text"
            :aria-label="isZh ? '刷新文件' : 'Refresh file'"
            @click="load"
            ><template #icon><t-icon name="refresh" /></template
            >{{ isZh ? '刷新' : 'Refresh' }}</t-button
          >
        </div>
      </header>
      <div v-if="data.type === 'text' && !data.content?.trim()" class="document-empty">
        <t-icon name="file-1" />
        <h2>{{ ui.emptyFile }}</h2>
        <p>{{ ui.emptyFileHint }}</p>
      </div>
      <template v-else-if="data.type === 'text'">
        <MarkdownRenderer
          v-if="loadedPath.endsWith('.md')"
          :content="data.content || ''"
          :work-id="workId"
          :base-path="basePath"
        />
        <CodeHighlight
          v-else-if="codeLanguage"
          :code="data.content || ''"
          :language="codeLanguage"
        />
        <pre v-else class="document-text">{{ data.content }}</pre>
      </template>
      <img
        v-else-if="data.type === 'image'"
        class="document-image"
        :src="imageSource"
        :alt="data.filename"
        @load="restoreScroll"
      />
      <DocxViewer
        v-else-if="loadedPath.toLowerCase().endsWith('.docx')"
        :file-info="binaryInfo"
        :work-id="workId"
        :token="auth.token || ''"
      />
      <BinaryFileViewer
        v-else
        :file-info="binaryInfo"
        :work-id="workId"
        :token="auth.token || ''"
      />
    </article>
  </section>
</template>
<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { workspaceFileAPI, type FileContent } from '@/api/workspace'
import { useAuthStore } from '@/stores/auth'
import { useStudioCopy } from '@/lang/studio'
import { paperLabel } from '@/utils/workFiles'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import CodeHighlight from '@/components/CodeHighlight.vue'
import DocxViewer from '@/components/DocxViewer.vue'
import BinaryFileViewer from '@/components/BinaryFileViewer.vue'
const props = withDefaults(
  defineProps<{
    workId: string
    path?: string
    paper?: boolean
    available?: boolean
    fallbackPath?: string
    latexSource?: boolean
    active: boolean
    refreshKey?: number
    scrollTop?: number
    showToolbar?: boolean
  }>(),
  { path: 'paper.md', available: true, scrollTop: 0, showToolbar: true },
)
const emit = defineEmits<{
  'open-file': [path: string]
  'open-files': []
  'edit-paper': []
  'scroll-position': [top: number]
}>()
const auth = useAuthStore()
const { ui, isZh } = useStudioCopy()
const scroller = ref<HTMLElement | null>(null)
const loading = ref(false),
  error = ref(false),
  data = ref<FileContent | null>(null),
  loadedPath = ref(props.path)
let generation = 0,
  restoring = false
const basePath = computed(() =>
  loadedPath.value.includes('/')
    ? loadedPath.value.slice(0, loadedPath.value.lastIndexOf('/'))
    : '',
)
const codeLanguage = computed(() =>
  loadedPath.value.endsWith('.py') ? 'python' : loadedPath.value.endsWith('.tex') ? 'latex' : '',
)
const imageSource = computed(
  () =>
    `data:image/${loadedPath.value.toLowerCase().endsWith('.svg') ? 'svg+xml' : loadedPath.value.toLowerCase().endsWith('.jpg') ? 'jpeg' : loadedPath.value.split('.').pop()};base64,${data.value?.content || ''}`,
)
const binaryInfo = computed(() => ({
  filename: data.value?.filename || loadedPath.value,
  size: data.value?.size || 0,
  mime_type: data.value?.mime_type || 'application/octet-stream',
  download_url: data.value?.download_url || '',
  message: data.value?.message || '',
}))
function restoreScroll() {
  if (scroller.value && props.active) {
    restoring = true
    scroller.value.scrollTop = props.scrollTop
    nextTick(() => {
      restoring = false
    })
  }
}
function rememberScroll() {
  if (!loading.value && props.active && !restoring)
    emit('scroll-position', scroller.value?.scrollTop || 0)
}
async function load() {
  const request = ++generation
  error.value = false
  if (!auth.token || (props.paper && !props.available)) {
    loading.value = false
    data.value = null
    return
  }
  loading.value = true
  error.value = false
  try {
    let path = props.path
    let response: FileContent
    try {
      response = await workspaceFileAPI.readFile(auth.token, props.workId, path)
    } catch (cause) {
      if (!props.paper || !props.fallbackPath || props.fallbackPath === path) throw cause
      path = props.fallbackPath
      response = await workspaceFileAPI.readFile(auth.token, props.workId, path)
    }
    if (
      props.paper &&
      response.type === 'text' &&
      !response.content?.trim() &&
      props.fallbackPath &&
      props.fallbackPath !== path
    ) {
      path = props.fallbackPath
      response = await workspaceFileAPI.readFile(auth.token, props.workId, path)
    }
    if (request !== generation) return
    data.value = response
    loadedPath.value = path
  } catch {
    if (request === generation) error.value = true
  } finally {
    if (request === generation) {
      loading.value = false
      await nextTick()
      restoreScroll()
    }
  }
}
watch(() => [props.workId, props.path, props.available, props.refreshKey], load, {
  immediate: true,
})
watch(
  () => props.active,
  async (active) => {
    if (active) {
      await nextTick()
      restoreScroll()
    }
  },
)
onUnmounted(() => {
  generation++
})
</script>
<style scoped>
.work-document {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 28px;
  background: var(--pa-surface);
}
.document-sheet {
  max-width: 880px;
  margin: 0 auto;
  padding: 0 8px 48px;
}
.document-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 40px;
  padding-bottom: 16px;
  margin-bottom: 28px;
  border-bottom: 1px solid var(--pa-line);
  color: var(--pa-muted);
  font-size: 12px;
}
.document-toolbar > span {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.document-toolbar > div {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.document-empty {
  min-height: 260px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: 16px;
  text-align: center;
  color: var(--pa-muted);
}
.document-empty > .t-icon {
  font-size: 32px;
}
.document-empty h2 {
  color: var(--pa-ink);
  font-size: 17px;
  font-weight: 500;
  margin: 0;
}
.document-empty p {
  font-size: 13px;
  max-width: 340px;
  margin: 0;
  line-height: 1.8;
}
.document-text {
  font-size: 14px;
  line-height: 1.8;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.document-image {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 0 auto;
}
.document-sheet :deep(.markdown-body) {
  font-size: 15px;
  line-height: 1.9;
}
.document-sheet :deep(.markdown-body h1) {
  font-size: 26px;
}
.document-sheet :deep(.markdown-body h2) {
  font-size: 21px;
}
@media (max-width: 768px) {
  .work-document {
    padding: 20px 16px;
  }
  .document-sheet {
    padding-inline: 0;
  }
  .document-toolbar {
    flex-wrap: wrap;
    margin-bottom: 20px;
  }
  .document-sheet :deep(.markdown-body) {
    font-size: 14px;
  }
}
</style>
