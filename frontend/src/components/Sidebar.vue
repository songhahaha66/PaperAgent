<template>
  <div class="sidebar" :class="{ 'sidebar-collapsed': isSidebarCollapsed }">
    <!-- 收起状态 - logo 单独在最顶部 -->
    <div v-if="isSidebarCollapsed" class="collapsed-logo-section">
      <img src="/logo.png" alt="PaperAgent Logo" class="header-logo-small" />
    </div>

    <div class="sidebar-header">
      <!-- 展开状态 -->
      <div v-if="!isSidebarCollapsed" class="header-title">
        <h2>PaperAgent</h2>
        <img src="/logo.png" alt="PaperAgent Logo" class="header-logo" />
      </div>
      <div class="header-controls">
        <t-button
          theme="default"
          shape="square"
          variant="text"
          @click="toggleSidebar"
          class="sidebar-toggle-btn"
          :title="t('sidebar.toggle')"
          :aria-label="t('sidebar.toggle')"
        >
          <t-icon :name="isSidebarCollapsed ? 'chevron-right' : 'chevron-left'" />
        </t-button>
      </div>
    </div>

    <!-- 展开状态的内容 -->
    <div class="sidebar-content" v-if="!isSidebarCollapsed">
      <div class="menu-section">
        <t-button
          theme="default"
          variant="text"
          block
          class="new-task-button"
          @click="createNewTask"
          ><template #icon><t-icon name="edit" /></template>{{ t('sidebar.newWork') }}</t-button
        >
      </div>

      <nav class="sidebar-nav" :aria-label="ui.workspace">
        <t-button
          v-for="item in navigationItems"
          :key="item.path"
          theme="default"
          variant="text"
          block
          :class="{ 'nav-active': route.path === item.path }"
          @click="navigate(item.path)"
          ><template #icon><t-icon :name="item.icon" /></template>{{ item.label }}</t-button
        >
      </nav>
      <div class="menu-section">
        <div class="menu-title">
          <browse-icon />
          <span>{{ t('sidebar.history') }}</span>
        </div>
        <div class="history-list">
          <!-- 加载状态 -->
          <div v-if="loadingHistory" class="loading-state">
            <t-loading size="small" />
            <p>{{ t('common.loading') }}</p>
          </div>

          <!-- 历史工作列表 -->
          <template v-else-if="historyItems.length > 0">
            <button
              v-for="item in historyItems"
              :key="item.id"
              class="history-item"
              :class="{
                active: activeHistoryId === item.id || route.params.work_id === item.work_id,
              }"
              :title="`${item.title || ui.untitled} · ${getStatusText(item.status)} · ${formatWorkDate(item.date, locale)}`"
              @click="selectHistory(item)"
            >
              <span class="history-title">{{ item.title?.trim() || t('sidebar.generating') }}</span>
              <span
                v-if="item.status === 'in_progress' || item.status === 'running'"
                class="running-dot"
                :aria-label="getStatusText(item.status)"
              />
            </button>
          </template>

          <!-- 无历史记录时显示空状态 -->
          <template v-else>
            <div class="empty-history">
              <t-icon name="browse" class="empty-icon" />
              <p class="empty-text">{{ t('sidebar.noHistory') }}</p>
              <p class="empty-hint">{{ t('sidebar.emptyHint') }}</p>
            </div>
          </template>
        </div>
      </div>
    </div>

    <div class="sidebar-footer" v-if="!isSidebarCollapsed">
      <t-dropdown :options="userOptions" placement="top-left" trigger="click">
        <button class="user-info" aria-label="账户与设置">
          <div class="user-avatar">
            <span class="user-avatar-text">{{ userName.charAt(0).toUpperCase() }}</span>
          </div>
          <div class="user-details">
            <div class="user-name">{{ userName }}</div>
            <div class="user-email">{{ userEmail }}</div>
          </div>
          <t-icon name="setting" class="settings-icon" />
        </button>
      </t-dropdown>
    </div>

    <!-- 收起状态下的用户头像 -->
    <!-- 收起时居中的新建按钮（位于 header 和 footer-collapsed 之间） -->
    <div v-if="isSidebarCollapsed" class="collapsed-center">
      <t-button
        theme="default"
        variant="text"
        shape="square"
        @click="createNewTask"
        class="sidebar-create-btn"
        :title="t('sidebar.newWork')"
      >
        <t-icon name="add" />
      </t-button>
    </div>

    <div class="sidebar-footer-collapsed" v-if="isSidebarCollapsed">
      <t-dropdown :options="userOptions" placement="top-left" trigger="click">
        <div class="user-avatar-collapsed">
          <t-avatar
            :content="userName.charAt(0).toUpperCase()"
            size="large"
            shape="circle"
            class="collapsed-avatar"
          />
        </div>
      </t-dropdown>
    </div>
  </div>

  <!-- 手机端侧边栏遮罩 -->
  <div v-if="isMobile && !isSidebarCollapsed" class="sidebar-backdrop" @click="toggleSidebar" />

  <!-- 手机端侧边栏收起时的浮动展开按钮 -->
  <div v-if="isSidebarCollapsed" class="mobile-expand-btn-container">
    <t-button
      theme="default"
      variant="outline"
      aria-label="打开导航"
      shape="square"
      @click="toggleSidebar"
      class="mobile-expand-btn"
    >
      <t-icon name="view-list" />
    </t-button>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { BrowseIcon } from 'tdesign-icons-vue-next'
import { MessagePlugin } from 'tdesign-vue-next'
import { useAuthStore } from '@/stores/auth'
import { workspaceAPI } from '@/api/workspace'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useStudioCopy } from '@/lang/studio'
import { formatWorkDate } from '@/utils/dates'

// 定义props
interface Props {
  isSidebarCollapsed: boolean
  activeHistoryId: number | null
}

// 定义emits
interface Emits {
  (e: 'toggle-sidebar'): void
  (e: 'create-new-task'): void
  (e: 'select-history', id: number): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const { t, locale } = useI18n()
const router = useRouter()
const route = useRoute()
const { ui } = useStudioCopy()
const authStore = useAuthStore()
const { isMobile } = useBreakpoint()

// 历史工作数据类型定义
interface HistoryItem {
  id: number
  work_id?: string
  title: string
  date: string
  content: string
  status?: string
}

// 历史工作数据状态
const historyItems = ref<HistoryItem[]>([])
const loadingHistory = ref(false)

// 侧边栏折叠状态
const isSidebarCollapsed = computed(() => props.isSidebarCollapsed)

// 切换侧边栏折叠状态
const toggleSidebar = () => {
  emit('toggle-sidebar')
}

const closeSidebarOnMobile = () => {
  if (isMobile.value && !props.isSidebarCollapsed) {
    emit('toggle-sidebar')
  }
}

const navigationItems = computed(() => [
  { path: '/home', label: ui.value.workspace, icon: 'home' },
  { path: '/template', label: ui.value.templates, icon: 'file-copy' },
  { path: '/api-config', label: ui.value.models, icon: 'setting' },
])
function navigate(path: string) {
  closeSidebarOnMobile()
  router.push(path)
}
// 新建工作
const createNewTask = () => {
  emit('create-new-task')
  closeSidebarOnMobile()
  router.push('/home')
}

// 选择历史工作
const selectHistory = (item: any) => {
  emit('select-history', item.id)
  closeSidebarOnMobile()

  if (item.work_id) {
    router.push(`/work/${item.work_id}`)
  } else {
    router.push(`/work/${item.id}`)
  }
}

// 用户信息
const userName = computed(() => authStore.currentUser?.username || t('common.user'))
const userEmail = computed(() => authStore.currentUser?.email || '')
const userAvatar = ref('') // 默认头像，如果为空则使用默认头像

// 加载用户工作列表
const loadUserWorks = async () => {
  if (!authStore.token) return

  loadingHistory.value = true
  try {
    const worksResponse = await workspaceAPI.getWorks(authStore.token, 0, 10)

    // 转换工作数据为历史记录格式
    historyItems.value = worksResponse.works.map((work) => ({
      id: work.id,
      work_id: work.work_id,
      title: work.title,
      date: work.created_at,
      content: work.description || '',
      status: work.status,
    }))
  } catch (error) {
    console.error('加载工作列表失败:', error)
    // 清空历史记录，显示空状态
    historyItems.value = []
  } finally {
    loadingHistory.value = false
  }
}

// 当前选中的历史工作ID
const activeHistoryId = computed(() => props.activeHistoryId)

// 获取状态主题
const getStatusTheme = (status?: string) => {
  if (!status) return 'default'
  const themes: Record<string, 'default' | 'primary' | 'success' | 'warning' | 'danger'> = {
    created: 'default',
    in_progress: 'primary',
    running: 'primary',
    completed: 'success',
    paused: 'warning',
    waiting: 'warning',
    cancelled: 'danger',
    failed: 'danger',
  }
  return themes[status] || 'default'
}

// 获取状态文本
const getStatusText = (status?: string) => {
  if (!status) return ''
  const texts: Record<string, string> = {
    created: t('sidebar.created'),
    in_progress: t('sidebar.inProgress'),
    running: t('sidebar.inProgress'),
    completed: t('sidebar.completed'),
    paused: t('sidebar.paused'),
    waiting: t('sidebar.waiting'),
    cancelled: t('sidebar.cancelled'),
    failed: t('sidebar.failed'),
  }
  return texts[status] || status
}

watch(
  [isMobile, isSidebarCollapsed],
  ([mobile, collapsed]) => {
    if (mobile && !collapsed) {
      document.body.style.overflow = 'hidden'
    } else {
      document.body.style.overflow = ''
    }
  },
  { immediate: true },
)

onUnmounted(() => {
  document.body.style.overflow = ''
})

const onWorkStatusUpdated = (event: Event) => {
  const { workId, status } = (event as CustomEvent).detail
  const workItem = historyItems.value.find((item) => item.work_id === workId)
  if (workItem) workItem.status = status
}
onUnmounted(() => window.removeEventListener('work-status-updated', onWorkStatusUpdated))

// 组件挂载时加载历史工作数据
onMounted(() => {
  loadUserWorks()

  // 监听工作标题更新事件，刷新侧边栏工作列表
  window.addEventListener('work-title-updated', (event: any) => {
    const { workId, newTitle } = event.detail

    // 找到对应的工作项并更新标题
    const workItem = historyItems.value.find((item) => item.work_id === workId)
    if (workItem) {
      workItem.title = newTitle
      console.log('侧边栏工作标题已更新:', newTitle)
    }

    // 也可以选择重新加载整个工作列表
    // loadUserWorks();
  })

  window.addEventListener('work-status-updated', onWorkStatusUpdated)

  // 组件卸载时清理事件监听器
  onUnmounted(() => {
    window.removeEventListener('work-title-updated', (event: any) => {
      const { workId, newTitle } = event.detail

      // 找到对应的工作项并更新标题
      const workItem = historyItems.value.find((item) => item.work_id === workId)
      if (workItem) {
        workItem.title = newTitle
        console.log('侧边栏工作标题已更新:', newTitle)
      }
    })
  })
})

// 用户菜单选项
const userOptions = computed(() => [
  {
    content: t('sidebar.templates'),
    value: 'template',
    onClick: () => {
      router.push('/template')
    },
  },
  {
    content: t('sidebar.apiKey'),
    value: 'api-key',
    onClick: () => {
      router.push('/api-config')
    },
  },
  {
    content: t('sidebar.passkeys'),
    value: 'passkey',
    onClick: () => {
      router.push('/passkey')
    },
  },
  {
    content: t('sidebar.logout'),
    value: 'logout',
    onClick: () => {
      authStore.logout()
      MessagePlugin.success(t('sidebar.loggedOut'))
      router.push('/login')
    },
  },
])
</script>

<style scoped>
.sidebar {
  width: 248px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-right: 1px solid #ededed;
  overflow: hidden;
  transition: width 0.2s;
}
.sidebar-collapsed {
  width: 64px;
}
.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  height: 72px;
  min-height: 72px;
  padding: 0 17px;
}
.header-title {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}
.header-title h2 {
  font-family: var(--td-font-family);
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.4px;
  margin: 0;
  color: #262626;
}
.header-logo {
  width: 22px;
  height: 26px;
  object-fit: contain;
  order: -1;
}
.header-controls {
  margin-left: auto;
}
.sidebar-toggle-btn {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  color: #888;
}
.collapsed-logo-section {
  display: flex;
  justify-content: center;
  align-items: center;
  padding-top: 22px;
}
.header-logo-small {
  width: 25px;
  height: 29px;
  object-fit: contain;
}
.sidebar-collapsed .sidebar-header {
  min-height: 47px;
  height: 47px;
  padding: 0 16px;
}
.sidebar-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 10px;
  scrollbar-width: thin;
}
.menu-section {
  margin-bottom: 22px;
}
.new-task-button {
  width: 100%;
  height: 39px;
  border-radius: 10px;
  justify-content: flex-start;
  font-size: 13px;
  color: #333;
}
.new-task-button :deep(.t-button__text) {
  text-align: left;
  flex: 1;
}
.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: -14px 0 32px;
}
.sidebar-nav .t-button {
  justify-content: flex-start;
  height: 37px;
  border-radius: 10px;
  color: #555;
  font-size: 13px;
  padding: 0 12px;
}
.sidebar-nav :deep(.t-button__text) {
  flex: 1;
  text-align: left;
}
.sidebar-nav .t-button:hover {
  background: #f7f7f7;
}
.sidebar-nav .nav-active {
  background: #f4f4f4;
  color: #222;
}
.menu-title {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 13px;
  margin: 0 0 12px;
  font-size: 12px;
  font-weight: 400;
  color: #999;
  font-family: var(--td-font-family);
}
.menu-title > .t-icon {
  display: none;
}
.history-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  text-align: left;
  padding: 11px 13px;
  border: 0;
  background: transparent;
  border-radius: 9px;
  cursor: pointer;
  color: #555;
  font-size: 13px;
  line-height: 1.6;
  margin-bottom: 2px;
}
.history-item:hover {
  background: #f7f7f7;
}
.history-item.active {
  background: #f2f2f2;
  color: #222;
}
.history-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}
.running-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--td-success-color);
  flex-shrink: 0;
}
.sidebar-footer {
  padding: 12px 10px;
  margin-top: auto;
  border-top: 0;
}
.user-info {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 10px;
  border: 0;
  background: #fff;
  border-radius: 12px;
  text-align: left;
  padding: 10px 11px;
  cursor: pointer;
}
.user-info:hover {
  background: #f7f7f7;
}
.user-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #eee;
}
.user-avatar-text {
  color: #666;
  font-size: 13px;
  font-weight: 500;
}
.user-details {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}
.user-name {
  font-size: 12px;
  color: #444;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.user-email {
  font-size: 10px;
  color: #999;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.settings-icon {
  font-size: 15px;
  color: #aaa;
  margin-left: auto;
}
.collapsed-center {
  display: flex;
  justify-content: center;
  padding: 5px 0;
}
.sidebar-create-btn {
  width: 36px;
  height: 36px;
  border-radius: 11px;
  color: #555;
}
.sidebar-footer-collapsed {
  padding: 15px 10px;
  margin-top: auto;
  display: flex;
  justify-content: center;
}
.user-avatar-collapsed {
  cursor: pointer;
}
.collapsed-avatar {
  width: 32px;
  height: 32px;
  background: #eee;
  color: #555;
  font-size: 13px;
  box-shadow: none;
}
.sidebar-backdrop,
.mobile-expand-btn-container {
  display: none;
}
.mobile-expand-btn-container {
  position: fixed;
  top: 15px;
  left: 14px;
  z-index: 1080;
}
.mobile-expand-btn {
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 9px;
  box-shadow: none;
  background: #fff;
  color: #555;
  font-size: 21px;
}
.loading-state {
  text-align: center;
  padding: 30px 16px;
  color: #999;
  font-size: 12px;
}
.empty-history {
  padding: 22px 12px;
  color: #999;
  font-size: 12px;
  line-height: 1.8;
}
.empty-icon {
  font-size: 24px;
  color: #bbb;
}
.empty-text {
  margin: 12px 0 6px;
  font-size: 12px;
  font-weight: 400;
}
.empty-hint {
  font-size: 11px;
  color: #aaa;
  margin: 0;
}
@media (max-width: 768px) {
  .sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    width: min(280px, 86vw);
    height: 100dvh;
    z-index: 1100;
    transform: translateX(0);
    transition: transform 0.2s;
  }
  .sidebar-collapsed {
    transform: translateX(-100%);
    width: min(280px, 86vw);
  }
  .sidebar-backdrop {
    display: block;
    position: fixed;
    inset: 0;
    background: #0005;
    z-index: 1090;
  }
  .mobile-expand-btn-container {
    display: block;
  }
  .sidebar-header {
    height: 68px;
    min-height: 68px;
  }
}
</style>
