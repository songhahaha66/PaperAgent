<template>
  <div class="file-manager">
    <t-collapse v-model="fileManagerExpanded" :border="false">
      <t-collapse-panel value="files" header="文件管理器">
        <!-- 文件树 -->
        <div class="file-tree">
          <t-tree
            :data="processedFileTreeData"
            :expand-on-click-node="true"
            :default-expanded="['python_files', 'markdown_files', 'image_files']"
            @click="handleFileClick"
            @select="handleFileSelect"
          />
        </div>
        
        <!-- 文件信息 -->
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
import { ref, defineProps, defineEmits, computed } from 'vue'
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
  workId?: string
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

// 处理文件树数据，按文件类型分类
const processedFileTreeData = computed(() => {
  if (!props.fileTreeData || props.fileTreeData.length === 0) {
    // 返回默认的空结构
    return [
      {
        value: 'python_files',
        label: 'Python脚本',
        children: []
      },
      {
        value: 'markdown_files',
        label: 'Markdown文档',
        children: []
      },
      {
        value: 'image_files',
        label: '图片文件',
        children: []
      }
    ]
  }
  
  // 过滤掉空目录，只保留有文件的目录
  const hasActualFiles = props.fileTreeData.some(file => file.type === 'file');
  
  if (!hasActualFiles) {
    // 如果没有实际文件，只显示分类结构
    const pythonFiles: any[] = [];
    const markdownFiles: any[] = [];
    const imageFiles: any[] = [];
    
    return [
      {
        value: 'python_files',
        label: 'Python脚本 (0)',
        children: pythonFiles
      },
      {
        value: 'markdown_files',
        label: 'Markdown文档 (0)',
        children: markdownFiles
      },
      {
        value: 'image_files',
        label: '图片文件 (0)',
        children: imageFiles
      }
    ]
  }
  
  // 构建目录树结构
  const buildDirectoryTree = (files: any[]) => {
    const tree: any = {};
    
    files.forEach(file => {
      const pathParts = file.path.split('/');
      let currentLevel = tree;
      
      // 构建目录路径
      for (let i = 0; i < pathParts.length - 1; i++) {
        const part = pathParts[i];
        if (!currentLevel[part]) {
          currentLevel[part] = {
            type: 'directory',
            children: {},
            files: []
          };
        }
        currentLevel = currentLevel[part].children;
      }
      
      // 添加文件到对应目录
      const fileName = pathParts[pathParts.length - 1];
      if (currentLevel.files) {
        currentLevel.files.push({
          ...file,
          displayName: fileName
        });
      }
    });
    
    return tree;
  };
  
  // 将目录树转换为组件需要的格式
  const convertTreeToComponentFormat = (tree: any, parentPath = ''): any[] => {
    const result: any[] = [];
    
    // 先添加目录
    Object.keys(tree).forEach(key => {
      if (key !== 'files' && key !== 'type') {
        const node = tree[key];
        const fullPath = parentPath ? `${parentPath}/${key}` : key;
        
        result.push({
          value: fullPath,
          label: key,
          children: convertTreeToComponentFormat(node, fullPath)
        });
      }
    });
    
    // 再添加文件
    if (tree.files) {
      tree.files.forEach((file: any) => {
        result.push({
          value: file.path,
          label: file.displayName,
          isLeaf: true,
          fileType: getFileType(file.path)
        });
      });
    }
    
    return result;
  };
  
  // 获取文件类型
  const getFileType = (filePath: string): string => {
    if (filePath.endsWith('.py')) return 'python';
    if (filePath.endsWith('.md')) return 'markdown';
    if (isImageFile(filePath)) return 'image';
    return 'other';
  };
  
  // 构建完整的目录树结构
  const directoryTree = buildDirectoryTree(props.fileTreeData);
  const fullTree = convertTreeToComponentFormat(directoryTree);
  
  // 按文件类型分类（保持原有逻辑作为备选）
  const pythonFiles: any[] = [];
  const markdownFiles: any[] = [];
  const imageFiles: any[] = [];
  
  props.fileTreeData.forEach(file => {
    if (file.path.endsWith('.py')) {
      pythonFiles.push({
        value: file.path,
        label: file.name,
        isLeaf: true
      })
    } else if (file.path.endsWith('.md')) {
      markdownFiles.push({
        value: file.path,
        label: file.name,
        isLeaf: true
      })
    } else if (isImageFile(file.path)) {
      imageFiles.push({
        value: file.path,
        label: file.name,
        isLeaf: true
      })
    }
  })
  
  // 返回完整的目录树结构，如果没有文件则回退到分类显示
  if (fullTree.length > 0 && hasActualFiles) {
    return fullTree;
  } else {
    return [
      {
        value: 'python_files',
        label: `Python脚本 (${pythonFiles.length})`,
        children: pythonFiles
      },
      {
        value: 'markdown_files',
        label: `Markdown文档 (${markdownFiles.length})`,
        children: markdownFiles
      },
      {
        value: 'image_files',
        label: `图片文件 (${imageFiles.length})`,
        children: imageFiles
      }
    ]
  }
})

// 判断是否为图片文件
const isImageFile = (filePath: string): boolean => {
  const imageExtensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp']
  const lowerPath = filePath.toLowerCase()
  return imageExtensions.some(ext => lowerPath.endsWith(ext))
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
  return key === 'python_files' || key === 'markdown_files' || key === 'image_files'
}

// 处理文件点击
const handleFileClick = (context: { node: any, e: PointerEvent }) => {
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
  max-height: 300px;
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
