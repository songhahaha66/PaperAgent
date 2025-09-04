<template>
  <div class="code-highlight">
    <pre><code :class="languageClass" ref="codeElement" v-html="highlightedCode"></code></pre>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import hljs from 'highlight.js/lib/core'
import python from 'highlight.js/lib/languages/python'
import 'highlight.js/styles/github.css'

// Register the Python language
hljs.registerLanguage('python', python)

interface Props {
  code: string
  language?: string
}

const props = withDefaults(defineProps<Props>(), {
  language: 'python'
})

const codeElement = ref<HTMLElement>()

const highlightedCode = computed(() => {
  if (!props.code) return ''
  
  try {
    const result = hljs.highlight(props.code, { language: props.language })
    return result.value
  } catch (error) {
    console.warn('Syntax highlighting failed, falling back to plain text:', error)
    return props.code
  }
})

const languageClass = computed(() => `language-${props.language}`)
</script>

<style scoped>
.code-highlight {
  background-color: #f6f8fa;
  border-radius: 6px;
  padding: 16px;
  overflow-x: auto;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;
  font-size: 13px;
  line-height: 1.45;
}

.code-highlight pre {
  margin: 0;
  background: transparent;
  border: none;
  padding: 0;
}

.code-highlight code {
  background: transparent;
  padding: 0;
  border: none;
  font-family: inherit;
}
</style>