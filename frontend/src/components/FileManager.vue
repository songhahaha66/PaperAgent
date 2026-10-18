<template>
  <div class="file-manager" :class="{ collapsed: isCollapsed, 'fill-height': fillHeight }">
    <div class="fm-header">
      <t-button
        v-if="!fillHeight"
        variant="text"
        shape="square"
        :aria-label="isCollapsed ? '展开文件管理器' : '收起文件管理器'"
        @click="isCollapsed = !isCollapsed"
        class="fm-btn"
      >
        <template #icon>
          <t-icon :name="isCollapsed ? 'chevron-up' : 'chevron-down'" size="16px" />
        </template>
      </t-button>
      <span class="fm-title"><t-icon name="folder-open" />文件</span>
      <t-button
        variant="text"
        shape="square"
        aria-label="刷新文件"
        @click.stop="handleRefresh"
        :loading="isLoading"
        class="fm-btn"
      >
        <template #icon>
          <t-icon name="refresh" size="16px" />
        </template>
      </t-button>
    </div>
    <div v-show="!isCollapsed" class="fm-body">
      <div class="tab-content">
        <div class="file-tree">
          <div v-if="isLoading && !fileTreeData?.length" class="loading-state">
            <t-loading size="small" text="加载文件中..." />
          </div>
          <div v-else-if="!fileTreeData || fileTreeData.length === 0" class="empty-state">
            <t-icon name="folder-open" class="empty-icon" />
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
        <div v-if="selectedFile" class="file-manager-info">
          <span class="selected-file-info">{{ selectedFile }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'

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
  fillHeight?: boolean
}

interface Emits {
  (e: 'file-select', fileKey: string): void
  (e: 'refresh'): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const isCollapsed = ref(false)
const selectedFile = ref<string | null>(null)

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
      attachments: [] as any[],
    }

    fileInfos.forEach((file) => {
      if (file.type === 'file') {
        const category = file.category || 'outputs' // 默认归类到outputs
        const fileItem = {
          value: file.path,
          label: file.name || file.path.split('/').pop() || 'Unknown',
          isLeaf: true,
          category: category,
          category_path: file.category_path,
        }

        if (categorizedFiles[category as keyof typeof categorizedFiles]) {
          categorizedFiles[category as keyof typeof categorizedFiles].push(fileItem)
        }
      }
    })

    // 对每个分类的文件进行排序
    Object.keys(categorizedFiles).forEach((category) => {
      categorizedFiles[category as keyof typeof categorizedFiles].sort((a, b) =>
        a.label.localeCompare(b.label),
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

.file-manager.fill-height {
  height: 100%;
  display: flex;
  flex-direction: column;
  border-top: none;
}

.file-manager.fill-height .fm-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.fm-header {
  display: flex;
  align-items: center;
  min-height: 52px;
  padding: 0 16px;
  border-bottom: 1px solid #ededed;
  gap: 12px;
  flex-shrink: 0;
  box-sizing: border-box;
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

.fm-btn :deep(.t-loading),
.fm-btn :deep(.t-loading__gradient) {
  width: 16px !important;
  height: 16px !important;
  font-size: 16px;
}

.fm-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  font-size: 13px;
  font-weight: 500;
  color: #637369;
}

.fm-body {
  border-top: none;
}

.tab-content {
  padding: 16px;
}

.loading-state {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 160px;
  color: var(--td-text-color-secondary);
}

.empty-state {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  min-height: 160px;
  color: var(--td-text-color-secondary);
}

.empty-icon {
  font-size: 22px;
  margin-bottom: 6px;
}

.empty-text {
  font-size: 13px;
  color: #999;
}

.file-tree :deep(.t-tree-node) {
  font-size: 13px;
}

.file-tree :deep(.t-tree-node__label) {
  color: var(--td-text-color-primary);
}

.file-tree :deep(.t-tree-node--leaf .t-tree-node__label) {
  color: var(--td-brand-color);
  cursor: pointer;
}

.file-tree :deep(.t-tree-node--leaf .t-tree-node__label:hover) {
  color: var(--td-brand-color-hover);
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
  color: var(--td-brand-color);
  font-weight: 500;
  padding: 2px 6px;
  background-color: var(--td-brand-color-light);
  border-radius: 3px;
  font-size: 11px;
}

.no-file-selected {
  color: #b0b0b0;
  font-style: italic;
}
</style>
