<template>
  <div class="sidebar" :class="{ 'sidebar-collapsed': isSidebarCollapsed }">
    <div v-if="isSidebarCollapsed" class="collapsed-logo-section">
      <button
        class="logo-toggle"
        :title="isZh ? '展开侧边栏' : 'Expand sidebar'"
        :aria-label="isZh ? '展开侧边栏' : 'Expand sidebar'"
        :aria-expanded="false"
        @click="toggleSidebar"
      >
        <img src="/logo.png" alt="" class="header-logo-small" /><SidebarToggleIcon
          class="logo-toggle-icon"
        />
      </button>
    </div>
    <div v-else class="sidebar-header">
      <div class="header-title">
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
          ><SidebarToggleIcon :collapsed="false"
        /></t-button>
      </div>
    </div>

    <!-- 展开状态的内容 -->
    <div class="sidebar-content" v-if="!isSidebarCollapsed">
      <div class="menu-section">
        <t-button theme="primary" block class="new-task-button" @click="createNewTask"
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
          <div
            v-if="loadingHistory && !historyStore.hasLoaded && !historyItems.length"
            class="loading-state"
          >
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
      <AccountMenu :collapsed="false" @navigate="closeSidebarOnMobile" />
    </div>

    <!-- 收起状态下的用户头像 -->
    <!-- 收起时居中的新建按钮（位于 header 和 footer-collapsed 之间） -->
    <div v-if="isSidebarCollapsed" class="collapsed-center">
      <t-button
        theme="primary"
        shape="square"
        @click="createNewTask"
        class="sidebar-create-btn"
        :title="t('sidebar.newWork')"
      >
        <t-icon name="add" />
      </t-button>
    </div>

    <div class="sidebar-footer-collapsed" v-if="isSidebarCollapsed">
      <AccountMenu :collapsed="true" @navigate="closeSidebarOnMobile" />
    </div>
  </div>

  <!-- 手机端侧边栏遮罩 -->
  <div v-if="isMobile && !isSidebarCollapsed" class="sidebar-backdrop" @click="toggleSidebar" />

  <header v-if="isMobile" class="mobile-pagebar">
    <button
      class="mobile-navigation-button"
      :aria-label="isZh ? '打开导航' : 'Open navigation'"
      :aria-expanded="!isSidebarCollapsed"
      @click="toggleSidebar"
    >
      <SidebarToggleIcon />
    </button>
    <router-link to="/home" class="mobile-brand"
      ><img src="/logo.png" alt="" /><span>PaperAgent</span></router-link
    >
    <div class="mobile-account">
      <AccountMenu :collapsed="true" placement="bottom-right" @navigate="closeSidebarOnMobile" />
    </div>
  </header>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { BrowseIcon } from 'tdesign-icons-vue-next'
import AccountMenu from '@/components/AccountMenu.vue'
import { useWorkHistoryStore } from '@/stores/workHistory'
import SidebarToggleIcon from '@/components/SidebarToggleIcon.vue'
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
const { ui, isZh } = useStudioCopy()
const historyStore = useWorkHistoryStore()
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

const historyItems = computed<HistoryItem[]>(() =>
  historyStore.works.map((work) => ({
    id: work.id,
    work_id: work.work_id,
    title: work.title,
    date: work.created_at,
    content: work.description || '',
    status: work.status,
  })),
)
const loadingHistory = computed(() => historyStore.loading)

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

// 当前选中的历史工作ID
const activeHistoryId = computed(() => props.activeHistoryId)

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
  const { workId, status } = (event as CustomEvent<{ workId: string; status: string }>).detail
  historyStore.patch(workId, { status })
}
const onWorkTitleUpdated = (event: Event) => {
  const { workId, newTitle } = (event as CustomEvent<{ workId: string; newTitle: string }>).detail
  historyStore.patch(workId, { title: newTitle })
}
onMounted(() => {
  void historyStore.ensureLoaded()
  window.addEventListener('work-status-updated', onWorkStatusUpdated)
  window.addEventListener('work-title-updated', onWorkTitleUpdated)
})
onUnmounted(() => {
  window.removeEventListener('work-status-updated', onWorkStatusUpdated)
  window.removeEventListener('work-title-updated', onWorkTitleUpdated)
})
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
  height: 72px;
  min-height: 72px;
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
  color: #fff;
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
  background: var(--td-brand-color);
  color: #fff;
  font-weight: 500;
}
.menu-title {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 13px;
  margin: 0 0 12px;
  font-size: 12px;
  font-weight: 500;
  color: #7a7a7a;
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
  background: var(--td-brand-color);
  color: #fff;
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
.collapsed-center {
  display: flex;
  justify-content: center;
  padding: 5px 0;
}
.sidebar-create-btn {
  width: 36px;
  height: 36px;
  border-radius: 11px;
  color: #fff;
}
.sidebar-footer-collapsed {
  padding: 12px 10px;
  margin-top: auto;
  display: flex;
  justify-content: center;
}
.sidebar-backdrop {
  display: none;
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
  .sidebar-header {
    height: 68px;
    min-height: 68px;
  }
}

.logo-toggle {
  position: relative;
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border: 0;
  background: transparent;
  border-radius: 11px;
  cursor: pointer;
}
.logo-toggle:hover,
.logo-toggle:focus-visible {
  background: #f5f5f5;
}
.logo-toggle-icon {
  position: absolute;
  font-size: 21px;
  color: #666;
  opacity: 0;
}
.logo-toggle:hover .header-logo-small,
.logo-toggle:focus-visible .header-logo-small {
  opacity: 0;
}
.logo-toggle:hover .logo-toggle-icon,
.logo-toggle:focus-visible .logo-toggle-icon {
  opacity: 1;
}
.sidebar-footer-collapsed {
  padding: 12px 10px;
}

.mobile-pagebar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 58px;
  min-height: 58px;
  width: 100%;
  padding: 0 14px;
  background: #fff;
  border-bottom: 1px solid #e8e9e7;
  box-sizing: border-box;
}
.mobile-navigation-button {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border: 0;
  background: transparent;
  color: #444;
  border-radius: 8px;
  cursor: pointer;
  font-size: 22px;
}
.mobile-navigation-button:hover {
  background: #f4f5f3;
}
.mobile-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #333;
  text-decoration: none;
  font-size: 15px;
  font-weight: 600;
}
.mobile-brand img {
  width: 19px;
  height: 23px;
  object-fit: contain;
}
.mobile-account {
  margin-left: auto;
  display: flex;
  align-items: center;
}
.mobile-account :deep(.account-trigger.compact) {
  width: 34px;
  height: 34px;
  padding: 3px;
}
.mobile-account :deep(.account-avatar) {
  width: 28px;
  height: 28px;
  font-size: 12px;
}
.sidebar-nav .nav-active:hover {
  background: var(--td-brand-color-hover);
  color: #fff;
}
.history-item.active:hover {
  background: var(--td-brand-color-hover);
  color: #fff;
}
.history-item.active .running-dot {
  background: #fff;
}
</style>
