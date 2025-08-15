<template>
  <div class="file-manager">
    <t-collapse v-model="fileManagerExpanded" :border="false">
      <t-collapse-panel value="files" header="文件管理器">
        <div class="file-tree">
          <t-tree
            :data="fileTreeData"
            :expand-on-click-node="true"
            :default-expanded="['generated_code', 'execution_results', 'paper_drafts']"
            @click="handleFileClick"
            @select="handleFileSelect"
          />
        </div>
        <div class="file-manager-info">
          <span v-if="selectedFile" class="selected-file-info">
            当前选中: {{ selectedFile }}
          </span>
          <span v-else class="no-file-selected">
            请点击文件进行选择
          </span>
        </div>
      </t-collapse-panel>
    </t-collapse>
  </div>
</template>

<script setup lang="ts">
import { ref, defineProps, defineEmits } from 'vue'
import { Tree, Collapse, CollapsePanel } from 'tdesign-vue-next'

// 定义props
interface Props {
  fileTreeData: Array<{
    value: string
    label: string
    children?: Array<{
      value: string
      label: string
      isLeaf: boolean
    }>
  }>
}

// 定义emits
interface Emits {
  (e: 'file-select', fileKey: string): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

// 文件管理器状态
const fileManagerExpanded = ref(['files'])
const selectedFile = ref<string | null>(null)

// 处理文件点击
const handleFileClick = (context: { node: any, e: PointerEvent }) => {
  console.log('文件节点点击:', context)
  const node = context.node
  if (node && node.isLeaf) {
    selectedFile.value = node.value
    emit('file-select', node.value)
    console.log('成功选中文件:', node.value)
  } else {
    console.log('点击的不是文件节点:', node?.value)
  }
}

// 处理文件选择
const handleFileSelect = (selectedKeys: string[]) => {
  console.log('文件选择事件:', selectedKeys)
  
  if (selectedKeys && selectedKeys.length > 0) {
    const selectedKey = selectedKeys[0]
    console.log('选中的key:', selectedKey)
    
    // 检查是否是叶子节点（文件）
    const isLeaf = checkIfLeaf(selectedKey)
    if (isLeaf) {
      selectedFile.value = selectedKey
      emit('file-select', selectedKey)
      console.log('成功选中文件:', selectedKey)
    } else {
      console.log('选中的不是文件节点:', selectedKey)
    }
  }
}

// 检查节点是否为叶子节点
const checkIfLeaf = (key: string): boolean => {
  for (const category of props.fileTreeData) {
    for (const child of category.children || []) {
      if (child.value === key) {
        return child.isLeaf || false
      }
    }
  }
  return false
}

// 暴露方法供父组件调用
defineExpose({
  selectedFile,
  setSelectedFile: (file: string | null) => {
    selectedFile.value = file
  }
})
</script>

<style scoped>
.file-manager {
  border-top: 1px solid #eee;
  background: white;
}

.file-manager .t-collapse {
  border: none;
}

.file-manager .t-collapse-panel__header {
  padding: 8px 16px;
  font-size: 14px;
  font-weight: 500;
  color: #2c3e50;
}

.file-manager .t-collapse-panel__body {
  padding: 0 16px 16px;
}

.file-tree {
  max-height: 200px;
  overflow-y: auto;
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
  color: #0052d9;
  text-decoration: underline;
}

.file-manager-info {
  margin-top: 12px;
  padding: 8px 0;
  border-top: 1px solid #eee;
  font-size: 12px;
}

.selected-file-info {
  color: #0052d9;
  font-weight: 500;
  padding: 4px 8px;
  background-color: #e6f3ff;
  border-radius: 4px;
}

.no-file-selected {
  color: #7f8c8d;
  font-style: italic;
}
</style>
