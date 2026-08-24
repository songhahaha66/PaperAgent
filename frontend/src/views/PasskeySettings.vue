<template>
  <div class="passkey-page">
    <Sidebar
      :is-sidebar-collapsed="isSidebarCollapsed"
      :active-history-id="activeHistoryId"
      @toggle-sidebar="toggleSidebar"
      @create-new-task="createNewTask"
      @select-history="selectHistory"
    />

    <div class="main-content">
      <div class="workspace-header">
        <h1>Passkey 管理</h1>
        <p>使用指纹、面容或安全密钥登录，无需每次输入密码</p>
      </div>

      <div class="passkey-content">
        <t-card title="我的 Passkey">
          <template #actions>
            <t-button
              theme="primary"
              :disabled="!passkeySupported || !!originHint"
              :loading="registering"
              @click="showAddDialog = true"
            >
              添加 Passkey
            </t-button>
          </template>

          <t-alert v-if="!passkeySupported" theme="warning" message="当前浏览器不支持 Passkey，请使用最新版 Chrome、Edge、Safari 或 Firefox。" />
          <t-alert v-else-if="originHint" theme="warning" :message="originHint" />

          <t-table
            :data="passkeys"
            :columns="columns"
            row-key="id"
            :loading="loading"
            empty="还没有 Passkey。添加后即可在登录页使用指纹、面容或安全密钥登录。"
          >
            <template #backed_up="{ row }">
              <t-tag :theme="row.backed_up ? 'success' : 'default'" variant="light">
                {{ row.backed_up ? '已同步' : '仅本机' }}
              </t-tag>
            </template>
            <template #created_at="{ row }">
              {{ formatDate(row.created_at) }}
            </template>
            <template #last_used_at="{ row }">
              {{ row.last_used_at ? formatDate(row.last_used_at) : '尚未使用' }}
            </template>
            <template #operation="{ row }">
              <t-space>
                <t-link theme="primary" @click="openRename(row)">重命名</t-link>
                <t-link theme="danger" @click="confirmDelete(row)">删除</t-link>
              </t-space>
            </template>
          </t-table>
        </t-card>
      </div>
    </div>

    <t-dialog
      v-model:visible="showAddDialog"
      header="添加 Passkey"
      :confirm-btn="{ content: '开始绑定', loading: registering }"
      @confirm="registerPasskey"
    >
      <t-form>
        <t-form-item label="名称">
          <t-input v-model="newPasskeyName" placeholder="例如：我的笔记本、iPhone" maxlength="100" />
        </t-form-item>
      </t-form>
      <p class="dialog-hint">浏览器将弹出系统提示，请使用指纹、面容、PIN 或安全密钥完成绑定。</p>
    </t-dialog>

    <t-dialog
      v-model:visible="showRenameDialog"
      header="重命名 Passkey"
      :confirm-btn="{ content: '保存', loading: renaming }"
      @confirm="saveRename"
    >
      <t-input v-model="renameValue" placeholder="Passkey 名称" maxlength="100" />
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { DialogPlugin, MessagePlugin } from 'tdesign-vue-next'
import Sidebar from '@/components/Sidebar.vue'
import { authAPI, type PasskeyCredential } from '@/api/auth'
import { createPasskey, isPasskeyCanceled, isPasskeySupported, passkeyOriginHint } from '@/utils/passkey'

const router = useRouter()
const isSidebarCollapsed = ref(window.innerWidth <= 768)
const activeHistoryId = ref<number | null>(null)
const passkeySupported = ref(false)
const originHint = ref<string | null>(null)
const loading = ref(false)
const registering = ref(false)
const renaming = ref(false)
const passkeys = ref<PasskeyCredential[]>([])
const showAddDialog = ref(false)
const showRenameDialog = ref(false)
const newPasskeyName = ref('我的设备')
const renameValue = ref('')
const renamingId = ref<number | null>(null)

const columns = [
  { colKey: 'name', title: '名称', minWidth: 140 },
  { colKey: 'backed_up', title: '同步', width: 100 },
  { colKey: 'created_at', title: '添加时间', width: 180 },
  { colKey: 'last_used_at', title: '最近使用', width: 180 },
  { colKey: 'operation', title: '操作', width: 140, fixed: 'right' },
]

const toggleSidebar = () => {
  isSidebarCollapsed.value = !isSidebarCollapsed.value
}

const createNewTask = () => {
  router.push('/home')
}

const selectHistory = (id: number) => {
  router.push(`/work/${id}`)
}

const formatDate = (dateString: string) => {
  if (!dateString) return ''
  return new Date(dateString).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const loadPasskeys = async () => {
  loading.value = true
  try {
    passkeys.value = await authAPI.listPasskeys()
  } catch (error) {
    MessagePlugin.error(error instanceof Error ? error.message : '加载 Passkey 失败')
  } finally {
    loading.value = false
  }
}

const registerPasskey = async () => {
  if (!passkeySupported.value) {
    MessagePlugin.warning('当前浏览器不支持 Passkey')
    return
  }
  if (originHint.value) {
    MessagePlugin.warning(originHint.value)
    return
  }
  const name = newPasskeyName.value.trim()
  showAddDialog.value = false
  registering.value = true
  try {
    const { challenge_id, options } = await authAPI.getPasskeyRegisterOptions()
    const credential = await createPasskey(options as any)
    await authAPI.verifyPasskeyRegister(challenge_id, credential, name || undefined)
    MessagePlugin.success('Passkey 添加成功')
    newPasskeyName.value = '我的设备'
    await loadPasskeys()
  } catch (error) {
    if (isPasskeyCanceled(error)) {
      MessagePlugin.info('已取消添加 Passkey')
    } else {
      console.error('添加 Passkey 失败:', error)
      MessagePlugin.error(error instanceof Error ? error.message : '添加 Passkey 失败')
    }
  } finally {
    registering.value = false
  }
}

const openRename = (row: PasskeyCredential) => {
  renamingId.value = row.id
  renameValue.value = row.name
  showRenameDialog.value = true
}

const saveRename = async () => {
  if (!renamingId.value) return
  const name = renameValue.value.trim()
  if (!name) {
    MessagePlugin.warning('请输入名称')
    return
  }
  renaming.value = true
  try {
    await authAPI.renamePasskey(renamingId.value, name)
    MessagePlugin.success('已更新名称')
    showRenameDialog.value = false
    await loadPasskeys()
  } catch (error) {
    MessagePlugin.error(error instanceof Error ? error.message : '重命名失败')
  } finally {
    renaming.value = false
  }
}

const confirmDelete = (row: PasskeyCredential) => {
  const dialog = DialogPlugin.confirm({
    header: '删除 Passkey',
    body: `确定删除「${row.name}」吗？删除后将无法再用它登录。`,
    confirmBtn: { content: '删除', theme: 'danger' },
    onConfirm: async () => {
      try {
        await authAPI.deletePasskey(row.id)
        MessagePlugin.success('已删除')
        dialog.destroy()
        await loadPasskeys()
      } catch (error) {
        MessagePlugin.error(error instanceof Error ? error.message : '删除失败')
      }
    },
  })
}

onMounted(async () => {
  passkeySupported.value = isPasskeySupported()
  originHint.value = passkeyOriginHint()
  await loadPasskeys()
})
</script>

<style scoped>
.passkey-page {
  display: flex;
  height: 100vh;
  width: 100vw;
  background: #f5f7fa;
  overflow: hidden;
}

.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.workspace-header {
  padding: 15px 30px;
  background: white;
  border-bottom: 1px solid #eee;
}

.workspace-header h1 {
  margin: 0 0 8px 0;
  color: #2c3e50;
  font-size: 1.5em;
}

.workspace-header p {
  margin: 0;
  color: #7f8c8d;
  font-size: 0.9em;
}

.passkey-content {
  flex: 1;
  padding: 20px 30px;
  overflow-y: auto;
}

.dialog-hint {
  margin: 8px 0 0;
  color: #7f8c8d;
  font-size: 13px;
}
</style>
