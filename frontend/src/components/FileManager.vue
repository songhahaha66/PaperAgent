<template>
  <div class="file-manager" :class="{ collapsed: isCollapsed }">
    <div class="fm-header">
      <t-button variant="text" shape="square" @click="isCollapsed = !isCollapsed" class="fm-btn">
        <template #icon>
          <t-icon :name="isCollapsed ? 'chevron-up' : 'chevron-down'" size="16px" />
        </template>
      </t-button>
      <t-tabs v-if="!isCollapsed" v-model="activeTab" size="medium" class="fm-tabs">
        <t-tab-panel value="files">
          <template #label>
            <div class="tab-label">
              <t-icon name="folder-open" />
              <span>文件</span>
            </div>
          </template>
        </t-tab-panel>
        <t-tab-panel value="plan">
          <template #label>
            <div class="tab-label">
              <t-icon name="task" />
              <span>计划</span>
            </div>
          </template>
        </t-tab-panel>
      </t-tabs>
      <span v-else class="fm-collapsed-title">文件管理器</span>
      <t-button variant="text" shape="square" @click.stop="handleRefresh" :loading="isLoading" class="fm-btn">
        <template #icon>
          <t-icon name="refresh" size="16px" />
        </template>
      </t-button>
    </div>
    <div v-show="!isCollapsed" class="fm-body">
      <div v-if="activeTab === 'files'" class="tab-content">
        <div class="file-tree">
          <div v-if="isLoading" class="loading-state">
            <t-loading size="small" text="加载文件中..." />
          </div>
          <div v-else-if="!fileTreeData || fileTreeData.length === 0" class="empty-state">
            <div class="empty-icon">📁</div>
            <div class="empty-text">暂无文件</div>
          </div>
          <t-tree
            v-else
            :data="processedFileTreeData"
            :expand-on-click-node="true"
            @click="handleFileClick"
            @select="handleFileSelect"
          />
        </div>
        <div class="file-manager-info">
          <span v-if="selectedFile" class="selected-file-info">{{ selectedFile }}</span>
          <span v-else class="no-file-selected">点击文件查看</span>
        </div>
      </div>
      <div v-else-if="activeTab === 'plan'" class="tab-content plan-tab">
        <div v-if="!planData || planData.items.length === 0" class="empty-state">
          <div class="empty-icon">📋</div>
          <div class="empty-text">等待AI制定写作计划...</div>
        </div>
        <div v-else class="plan-board">
          <div class="plan-summary">
            <div class="plan-heading">
              <div class="plan-title">{{ planData.title || '写作计划' }}</div>
              <div class="plan-meta">
                {{ planData.planning_mode === 'dynamic' ? '动态计划' : planData.methodology === 'spec-driven' ? '规范计划' : '结构化计划' }}
                <span v-if="planData.revision !== undefined"> · r{{ planData.revision }}</span>
                <span v-if="planData.updated_at"> · {{ formatPlanTime(planData.updated_at) }}</span>
              </div>
            </div>
            <div class="plan-percent">
              <span>{{ planData.stats.progress_percent }}</span>
              <small>%</small>
            </div>
          </div>
          <div class="plan-progress">
            <div class="plan-progress-bar" :style="{ width: `${planData.stats.progress_percent}%` }" />
          </div>
          <div class="plan-stats">
            <span>完成 {{ planData.stats.completed }}/{{ planData.stats.total }}</span>
            <span>进行中 {{ planData.stats.in_progress }}</span>
            <span v-if="planData.stats.blocked > 0">阻塞 {{ planData.stats.blocked }}</span>
          </div>

          <div v-if="planData.current_focus || planData.next_actions?.length" class="plan-dynamic">
            <div v-if="planData.current_focus" class="dynamic-row">
              <span class="dynamic-label">当前</span>
              <span class="dynamic-value">{{ planData.current_focus.title }}</span>
            </div>
            <div v-if="planData.next_actions?.length" class="dynamic-row">
              <span class="dynamic-label">下一步</span>
              <span class="dynamic-value">{{ planData.next_actions.map((item) => item.title).join('、') }}</span>
            </div>
          </div>

          <div v-if="planData.phases?.length" class="plan-phases">
            <div v-for="phase in planData.phases" :key="phase.id" class="plan-phase">
              <span class="phase-dot" />
              <span class="phase-title">{{ phase.title }}</span>
            </div>
          </div>

          <div class="plan-items">
            <div
              v-for="item in planData.items"
              :key="item.id"
              class="plan-item"
              :class="`status-${item.status}`"
            >
              <div class="plan-item-index">{{ item.order }}</div>
              <div class="plan-item-body">
                <div class="plan-item-head">
                  <span class="plan-item-title">{{ item.title }}</span>
                  <span class="plan-item-status">{{ statusLabel(item.status, item.status_label) }}</span>
                </div>
                <div v-if="item.description" class="plan-item-desc">{{ item.description }}</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import type { PlanData, PlanItemStatus } from '@/api/workspace'

interface Props {
  fileTreeData:
    | Array<{
        value: string
        label: string
        children?: Array<{
          value: string
          label: string
          isLeaf: boolean
        }>
      }>
    | Array<{
        name: string
        type: 'file' | 'directory'
        size?: number
        modified: number
        path: string
        display_path?: string
        depth?: number
        category?: string
        category_path?: string
      }>
  workId?: string
  loading?: boolean
  planData?: PlanData | null
}

interface Emits {
  (e: 'file-select', fileKey: string): void
  (e: 'refresh'): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const activeTab = ref('files')
const isCollapsed = ref(false)
const selectedFile = ref<string | null>(null)

const statusLabel = (status: PlanItemStatus, fallback?: string) => {
  if (fallback) return fallback
  const labels: Record<PlanItemStatus, string> = {
    pending: '待写',
    in_progress: '进行中',
    completed: '已完成',
    blocked: '阻塞',
  }
  return labels[status] || '待写'
}

const formatPlanTime = (value: string) => {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// 计算加载状态：优先使用父组件传入的loading状态，否则使用内部状态
const isLoading = computed(() => {
  if (props.loading !== undefined) {
    return props.loading
  }
  // 如果没有传入loading状态，则根据数据状态判断
  return !props.fileTreeData || props.fileTreeData.length === 0
})

// 处理文件树数据，按后端新的分类结构组织
const processedFileTreeData = computed(() => {
  // 如果正在加载，返回空数组，避免显示默认结构
  if (isLoading.value) {
    return []
  }

  if (!props.fileTreeData || props.fileTreeData.length === 0) {
    // 返回默认的空结构，使用后端的五个分类
    return [
       {
        value: 'papers',
        label: '论文文档 (0)',
        children: [],
      },
      {
        value: 'code',
        label: '代码文件 (0)',
        children: [],
      },
      {
        value: 'outputs',
        label: '输出文件 (0)',
        children: [],
      },
      {
        value: 'attachments',
        label: '附件 (0)',
        children: [],
      },
    ]
  }

  // 检查是否为FileInfo[]类型（有path和type属性）
  const isFileInfoType =
    props.fileTreeData.length > 0 &&
    props.fileTreeData[0] &&
    'path' in props.fileTreeData[0] &&
    'type' in props.fileTreeData[0]

  if (isFileInfoType) {
    // 处理FileInfo[]类型的数据
    const fileInfos = props.fileTreeData as Array<{
      name: string
      type: 'file' | 'directory'
      size?: number
      modified: number
      path: string
      display_path?: string
      depth?: number
      category?: string
      category_path?: string
    }>

    // 按照后端的五个分类组织文件
    const categorizedFiles = {
      code: [] as any[],
      outputs: [] as any[],
      papers: [] as any[],
      attachments: [] as any[]
    }

    fileInfos.forEach((file) => {
      if (file.type === 'file') {
        const category = file.category || 'outputs' // 默认归类到outputs
        const fileItem = {
          value: file.path,
          label: file.name || file.path.split('/').pop() || 'Unknown',
          isLeaf: true,
          category: category,
          category_path: file.category_path
        }

        if (categorizedFiles[category as keyof typeof categorizedFiles]) {
          categorizedFiles[category as keyof typeof categorizedFiles].push(fileItem)
        }
      }
    })

    // 对每个分类的文件进行排序
    Object.keys(categorizedFiles).forEach(category => {
      categorizedFiles[category as keyof typeof categorizedFiles].sort((a, b) =>
        a.label.localeCompare(b.label)
      )
    })

    return [
       {
        value: 'papers',
        label: `论文文档 (${categorizedFiles.papers.length})`,
        children: categorizedFiles.papers,
      },
      {
        value: 'code',
        label: `代码文件 (${categorizedFiles.code.length})`,
        children: categorizedFiles.code,
      },
      {
        value: 'outputs',
        label: `输出文件 (${categorizedFiles.outputs.length})`,
        children: categorizedFiles.outputs,
      },
      {
        value: 'attachments',
        label: `附件 (${categorizedFiles.attachments.length})`,
        children: categorizedFiles.attachments,
      },
    ]
  } else {
    // 处理原有的树形结构数据
    // 过滤掉空目录，只保留有文件的目录
    const hasActualFiles = props.fileTreeData.some((file: any) => file.type === 'file')

    if (!hasActualFiles) {
      // 如果没有实际文件，只显示分类结构
      const pythonFiles: any[] = []
      const markdownFiles: any[] = []
      const imageFiles: any[] = []

      return [
        {
          value: 'python_files',
          label: 'Python脚本 (0)',
          children: pythonFiles,
        },
        {
          value: 'markdown_files',
          label: 'Markdown文档 (0)',
          children: markdownFiles,
        },
        {
          value: 'image_files',
          label: '图片文件 (0)',
          children: imageFiles,
        },
      ]
    }

    // 构建目录树结构
    const buildDirectoryTree = (files: any[]) => {
      const tree: any = {}

      files.forEach((file) => {
        const pathParts = file.path.split('/')
        let currentLevel = tree

        // 构建目录路径
        for (let i = 0; i < pathParts.length - 1; i++) {
          const part = pathParts[i]
          if (!currentLevel[part]) {
            currentLevel[part] = {
              type: 'directory',
              children: {},
              files: [],
            }
          }
          currentLevel = currentLevel[part].children
        }

        // 添加文件到对应目录
        const fileName = pathParts[pathParts.length - 1]
        if (currentLevel.files) {
          currentLevel.files.push({
            ...file,
            displayName: fileName,
          })
        }
      })

      return tree
    }

    // 将目录树转换为组件需要的格式
    const convertTreeToComponentFormat = (tree: any, parentPath = ''): any[] => {
      const result: any[] = []

      // 先添加目录
      Object.keys(tree).forEach((key) => {
        if (key !== 'files' && key !== 'type') {
          const node = tree[key]
          const fullPath = parentPath ? `${parentPath}/${key}` : key

          result.push({
            value: fullPath,
            label: key,
            children: convertTreeToComponentFormat(node, fullPath),
          })
        }
      })

      // 再添加文件
      if (tree.files) {
        tree.files.forEach((file: any) => {
          result.push({
            value: file.path,
            label: file.displayName,
            isLeaf: true,
            fileType: getFileType(file.path),
          })
        })
      }

      return result
    }

    // 获取文件类型
    const getFileType = (filePath: string): string => {
      if (filePath.endsWith('.py')) return 'python'
      if (filePath.endsWith('.md')) return 'markdown'
      if (isImageFile(filePath)) return 'image'
      return 'text'
    }

    // 构建完整的目录树
    const fullTree = convertTreeToComponentFormat(buildDirectoryTree(props.fileTreeData))

    // 按文件类型分类（保持原有逻辑作为备选）
    const pythonFiles: any[] = []
    const markdownFiles: any[] = []
    const imageFiles: any[] = []

    props.fileTreeData.forEach((file: any) => {
      if (file.path && file.path.endsWith('.py')) {
        pythonFiles.push({
          value: file.path,
          label: file.name || file.path.split('/').pop() || 'Unknown',
          isLeaf: true,
        })
      } else if (file.path && file.path.endsWith('.md')) {
        markdownFiles.push({
          value: file.path,
          label: file.name || file.path.split('/').pop() || 'Unknown',
          isLeaf: true,
        })
      } else if (file.path && isImageFile(file.path)) {
        imageFiles.push({
          value: file.path,
          label: file.name || file.path.split('/').pop() || 'Unknown',
          isLeaf: true,
        })
      }
    })

    // 返回完整的目录树结构，如果没有文件则回退到分类显示
    if (fullTree.length > 0 && hasActualFiles) {
      return fullTree
    } else {
      return [
        {
          value: 'python_files',
          label: `Python脚本 (${pythonFiles.length})`,
          children: pythonFiles,
        },
        {
          value: 'markdown_files',
          label: `Markdown文档 (${markdownFiles.length})`,
          children: markdownFiles,
        },
        {
          value: 'image_files',
          label: `图片文件 (${imageFiles.length})`,
          children: imageFiles,
        },
      ]
    }
  }
})

// 判断是否为图片文件
const isImageFile = (filePath: string): boolean => {
  const imageExtensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp']
  const lowerPath = filePath.toLowerCase()
  return imageExtensions.some((ext) => lowerPath.endsWith(ext))
}

// 检查节点是否为叶子节点
const checkIfLeaf = (key: string): boolean => {
  // 如果是分类名称，直接返回false
  if (key === 'python_files' || key === 'markdown_files' || key === 'image_files') {
    return false
  }

  // 检查是否在分类的子节点中
  for (const category of processedFileTreeData.value) {
    if (category.value === key) {
      return false // 分类节点不是叶子节点
    }
    for (const child of category.children || []) {
      if (child.value === key) {
        return child.isLeaf || false
      }
    }
  }

  // 对于新的目录树结构，检查是否为文件
  return !key.endsWith('/') && key.includes('.') // 简单判断：包含扩展名的不是目录
}

// 检查是否为分类节点
const isCategoryNode = (key: string): boolean => {
  return key === 'code' || key === 'outputs' || key === 'papers' || key === 'attachments'
}

// 处理文件点击
const handleFileClick = (context: { node: any; e: PointerEvent }) => {
  console.log('文件节点点击:', context)
  const node = context.node
  if (node && node.isLeaf && node.value && !isCategoryNode(node.value)) {
    selectedFile.value = node.value
    emit('file-select', node.value)
    console.log('成功选中文件:', node.value)
  } else {
    console.log('点击的不是文件节点或点击的是分类节点:', node?.value)
  }
}

// 处理文件选择
const handleFileSelect = (selectedKeys: string[]) => {
  console.log('文件选择事件:', selectedKeys)

  if (selectedKeys && selectedKeys.length > 0) {
    const selectedKey = selectedKeys[0]
    if (!selectedKey) return
    
    console.log('选中的key:', selectedKey)

    // 检查是否是叶子节点（文件）且不是分类名称
    const isLeaf = checkIfLeaf(selectedKey)
    const isNotCategory = !isCategoryNode(selectedKey)

    if (isLeaf && isNotCategory) {
      selectedFile.value = selectedKey
      emit('file-select', selectedKey)
      console.log('成功选中文件:', selectedKey)
    } else {
      console.log('选中的不是文件节点或点击的是分类节点:', selectedKey)
    }
  }
}

// 处理刷新
const handleRefresh = () => {
  emit('refresh')
}

// 暴露方法供父组件调用
defineExpose({
  selectedFile,
  setSelectedFile: (file: string | null) => {
    selectedFile.value = file
  },
})
</script>

<style scoped>
.file-manager {
  border-top: 1px solid #e7e7e7;
  background: #fff;
}

.fm-header {
  display: flex;
  align-items: stretch;
  min-height: 40px;
  padding: 0 4px;
}

.file-manager.collapsed .fm-header {
  align-items: center;
  border-bottom: none;
}

.fm-btn {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  align-self: center;
}

.fm-tabs {
  flex: 1;
  min-width: 0;
}

.fm-tabs :deep(.t-tabs__header) {
  padding: 0;
  border-bottom: 1px solid #e7e7e7;
  height: 100%;
}

.fm-tabs :deep(.t-tabs__content) {
  display: none;
}

.fm-collapsed-title {
  flex: 1;
  font-size: 13px;
  font-weight: 500;
  color: #666;
  padding-left: 4px;
}

.tab-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
}

.fm-body {
  border-top: none;
}

.tab-content {
  padding: 8px 12px 12px;
}

.loading-state {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 80px;
  color: #7f8c8d;
}

.empty-state {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  height: 80px;
  color: #7f8c8d;
}

.empty-icon {
  font-size: 22px;
  margin-bottom: 6px;
}

.empty-text {
  font-size: 13px;
  color: #999;
}

.file-tree .t-tree-node {
  font-size: 13px;
}

.file-tree .t-tree-node__label {
  color: #2c3e50;
}

.file-tree .t-tree-node--leaf .t-tree-node__label {
  color: #0052d9;
  cursor: pointer;
}

.file-tree .t-tree-node--leaf .t-tree-node__label:hover {
  color: #003cab;
  text-decoration: underline;
}

.file-manager-info {
  margin-top: 8px;
  padding: 6px 0;
  border-top: 1px solid #f0f0f0;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.selected-file-info {
  color: #0052d9;
  font-weight: 500;
  padding: 2px 6px;
  background-color: #e8f3ff;
  border-radius: 3px;
  font-size: 11px;
}

.no-file-selected {
  color: #b0b0b0;
  font-style: italic;
}

.plan-tab {
  max-height: 320px;
  overflow-y: auto;
}

.plan-board {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 12px;
}

.plan-summary {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #f0f0f0;
}

.plan-heading {
  min-width: 0;
}

.plan-title {
  color: #2c3e50;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.plan-meta {
  color: #999;
  margin-top: 2px;
  font-size: 11px;
}

.plan-percent {
  flex: 0 0 auto;
  color: #333;
  font-weight: 600;
  line-height: 1;
  text-align: right;
}

.plan-percent span {
  font-size: 18px;
}

.plan-percent small {
  font-size: 11px;
  color: #999;
  margin-left: 1px;
}

.plan-progress {
  height: 4px;
  background: #f0f0f0;
  border-radius: 2px;
  overflow: hidden;
}

.plan-progress-bar {
  height: 100%;
  background: #595959;
  border-radius: 2px;
  transition: width 0.2s ease;
}

.plan-stats {
  display: flex;
  gap: 8px;
  color: #777;
  flex-wrap: wrap;
  font-size: 11px;
}

.plan-stats span {
  padding-right: 8px;
  border-right: 1px solid #e7e7e7;
}

.plan-stats span:last-child {
  border-right: none;
}

.plan-dynamic {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 7px 8px;
  background: #f8f8f8;
  border: 1px solid #eeeeee;
  border-radius: 4px;
}

.dynamic-row {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  gap: 8px;
}

.dynamic-label {
  color: #666;
  font-weight: 500;
}

.dynamic-value {
  color: #333;
  overflow-wrap: anywhere;
}

.plan-phases {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px 8px;
  padding: 0;
}

.plan-phase {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: #777;
  font-size: 11px;
}

.phase-dot {
  width: 5px;
  height: 5px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: #c5c5c5;
}

.phase-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.plan-items {
  display: flex;
  flex-direction: column;
  gap: 0;
  border-top: 1px solid #f0f0f0;
}

.plan-item {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid #f0f0f0;
  background: transparent;
}

.plan-item.status-completed {
  opacity: 0.82;
}

.plan-item.status-in_progress {
  opacity: 1;
}

.plan-item.status-pending {
  opacity: 0.9;
}

.plan-item.status-blocked {
  opacity: 1;
}

.plan-item-index {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: #f3f3f3;
  color: #777;
  font-weight: 600;
  font-size: 11px;
}

.plan-item.status-completed .plan-item-index {
  background: var(--td-success-color-light);
  color: var(--td-success-color);
}

.plan-item.status-in_progress .plan-item-index {
  background: #e7e7e7;
  color: #333;
}

.plan-item.status-blocked .plan-item-index {
  background: var(--td-error-color-light);
  color: var(--td-error-color);
}

.plan-item-body {
  min-width: 0;
}

.plan-item-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: center;
}

.plan-item-title {
  color: #2c3e50;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.plan-item-status {
  flex: 0 0 auto;
  color: #777;
  background: transparent;
  padding: 0;
  font-size: 11px;
}

.plan-item.status-completed .plan-item-status {
  color: var(--td-success-color);
}

.plan-item.status-blocked .plan-item-status {
  color: var(--td-error-color);
}

.plan-item-desc {
  margin-top: 4px;
  color: #999;
  line-height: 1.45;
  overflow-wrap: anywhere;
}
</style>
