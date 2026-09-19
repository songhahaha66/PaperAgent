<template>
  <div class="preview-section">
    <div v-if="showMainPaper && mainPaperContent">
      <t-card title="主要论文">
        <template #actions>
          <t-button size="small" variant="text" @click="$emit('close-main-paper')">
            <template #icon>
              <t-icon name="close" />
            </template>
          </t-button>
        </template>
        <div class="paper-preview">
          <MarkdownRenderer
            :content="mainPaperContent"
            :work-id="workId"
            :base-path="'papers'"
          />
        </div>
      </t-card>
    </div>
    <div v-else-if="selectedFile">
      <t-card :title="`文件预览: ${selectedFile}`">
        <div class="file-preview">
          <div v-if="!currentFileData" class="loading-container"></div>
          <div v-else-if="currentFileData.type === 'text'" class="text-preview">
            <CodeHighlight v-if="selectedFile.endsWith('.py')" :code="currentFileContent" language="python" />
            <MarkdownRenderer
              v-else-if="selectedFile.endsWith('.md')"
              :content="currentFileContent"
              :work-id="workId"
              :base-path="selectedFile.substring(0, selectedFile.lastIndexOf('/'))"
            />
            <pre v-else>{{ currentFileContent }}</pre>
          </div>
          <div v-else-if="currentFileData.type === 'image'" class="image-preview">
            <img
              v-if="imageUrls[selectedFile]"
              :src="imageUrls[selectedFile]"
              :alt="selectedFile"
              style="max-width: 100%; height: auto"
            />
            <div v-else class="loading-image">正在加载图片...</div>
          </div>
          <div v-else-if="currentFileData.type === 'binary'" class="binary-preview">
            <DocxViewer
              v-if="isDocxFile(selectedFile)"
              :file-info="currentFileData"
              :work-id="workId"
              :token="token"
            />
            <BinaryFileViewer
              v-else
              :file-info="currentFileData"
              :work-id="workId"
              :token="token"
            />
          </div>
          <div v-else-if="currentFileData" class="binary-preview">
            <DocxViewer
              v-if="isDocxFile(selectedFile)"
              :file-info="currentFileData"
              :work-id="workId"
              :token="token"
            />
            <BinaryFileViewer
              v-else
              :file-info="currentFileData"
              :work-id="workId"
              :token="token"
            />
          </div>
          <div v-else class="no-preview">
            <t-icon name="file" size="48px" />
            <p>文件信息加载中...</p>
          </div>
        </div>
      </t-card>
    </div>
    <div v-else-if="currentWork">
      <t-card title="工作信息">
        <div class="work-details">
          <p><strong>标题：</strong>{{ currentWork.title }}</p>
          <p><strong>描述：</strong>{{ currentWork.description || '暂无描述' }}</p>
          <p><strong>标签：</strong>{{ currentWork.tags || '无标签' }}</p>
          <p><strong>状态：</strong>{{ statusText }}</p>
          <p>
            <strong>输出格式：</strong>
            <t-tag :theme="outputModeTheme" variant="light" size="small">
              <template #icon>
                <t-icon :name="outputModeIcon" />
              </template>
              {{ outputModeText }}
            </t-tag>
          </p>
          <p>
            <strong>模板：</strong>
            {{ currentWork.template_id ? `模板ID: ${currentWork.template_id}` : '未选择模板' }}
          </p>
        </div>
      </t-card>
    </div>
    <div v-else>
      <t-card title="论文展示区">
        <div class="pdf-info">
          <p>与AI对话生成论文内容后，将在此处预览生成的论文。</p>
          <p>{{ isMobile ? '在文件页中点击文件可查看具体内容。' : '在左侧文件管理器中点击文件可查看具体内容。' }}</p>
        </div>
      </t-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { Work } from '@/api/workspace'
import BinaryFileViewer from '@/components/BinaryFileViewer.vue'
import CodeHighlight from '@/components/CodeHighlight.vue'
import DocxViewer from '@/components/DocxViewer.vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'

const props = defineProps<{
  isMobile: boolean
  showMainPaper: boolean
  mainPaperContent: string
  selectedFile: string | null
  currentFileData: any
  currentFileContent: string
  imageUrls: Record<string, string>
  currentWork: Work | null
  workId: string
  token: string
}>()

defineEmits<{
  'close-main-paper': []
}>()

const isDocxFile = (filePath: string | null): boolean => {
  if (!filePath) return false
  return filePath.toLowerCase().endsWith('.docx') || filePath.toLowerCase().includes('wordprocessingml')
}

const statusText = computed(() => {
  const texts: Record<string, string> = {
    created: '已创建',
    in_progress: '进行中',
    completed: '已完成',
    paused: '已暂停',
    cancelled: '已取消',
  }
  return texts[props.currentWork?.status || ''] || props.currentWork?.status || ''
})

const outputModeText = computed(() => {
  const texts: Record<string, string> = {
    markdown: 'Markdown',
    word: 'Word (.docx)',
    latex: 'LaTeX',
  }
  return texts[props.currentWork?.output_mode || 'markdown'] || 'Markdown'
})

const outputModeIcon = computed(() => {
  const icons: Record<string, string> = {
    markdown: 'file-1',
    word: 'file-word',
    latex: 'file-pdf',
  }
  return icons[props.currentWork?.output_mode || 'markdown'] || 'file-1'
})

const outputModeTheme = computed(() => {
  const themes: Record<string, 'default' | 'primary' | 'success' | 'warning' | 'danger'> = {
    markdown: 'primary',
    word: 'success',
    latex: 'warning',
  }
  return themes[props.currentWork?.output_mode || 'markdown'] || 'primary'
})
</script>

<style scoped>
.preview-section {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
  background: #f9f9f9;
  height: 100%;
}

.file-preview {
  max-height: 600px;
  overflow-y: auto;
}

.image-preview {
  text-align: center;
  padding: 16px;
}

.image-preview img {
  max-width: 100%;
  height: auto;
  border-radius: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.text-preview {
  background-color: #f5f5f5;
  border-radius: 4px;
  padding: 16px;
  overflow-x: auto;
}

.text-preview pre {
  margin: 0;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-wrap: break-word;
}

.work-details {
  padding: 16px;
  background: #f0f2f5;
  border-radius: 8px;
  border: 1px solid #e0e0e0;
}

.work-details p {
  margin: 8px 0;
  color: #555;
  font-size: 14px;
}

.work-details strong {
  color: #333;
  font-weight: 600;
}

.loading-container {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 40px 0;
}
</style>
