<template>
  <t-dialog
    :visible="true"
    :header="isZh ? '个人资料' : 'Profile'"
    width="440px"
    placement="center"
    :close-on-overlay-click="false"
    :close-on-esc-keydown="!saving"
    :close-btn="!saving"
    :confirm-btn="{
      content: isZh ? '保存' : 'Save',
      loading: saving,
      disabled: reading || !validName || !changed,
    }"
    :cancel-btn="{ content: isZh ? '取消' : 'Cancel', disabled: saving }"
    @confirm="save"
    @close="close"
  >
    <form class="profile-form" @submit.prevent="save">
      <div class="profile-avatar-row">
        <button
          type="button"
          class="profile-avatar"
          :disabled="saving || reading"
          :aria-label="isZh ? '更换头像' : 'Change profile photo'"
          @click="fileInput?.click()"
        >
          <img v-if="avatar" :src="avatar" alt="" />
          <span v-else>{{ (username.trim() || '?').charAt(0).toUpperCase() }}</span>
          <span class="profile-avatar-overlay" aria-hidden="true">{{ isZh ? '更换' : 'Change' }}</span>
        </button>
        <t-button v-if="avatar" size="small" variant="text" :disabled="saving || reading" @click="avatar = null">
          {{ isZh ? '恢复默认' : 'Reset' }}
        </t-button>
        <input
          ref="fileInput"
          type="file"
          accept="image/png,image/jpeg,image/webp"
          hidden
          @change="selectAvatar"
        />
      </div>
      <div class="profile-fields">
        <div class="profile-field">
          <label class="profile-field-label" :for="nameId">{{ isZh ? '用户名' : 'Username' }}</label>
          <t-input
            :id="nameId"
            v-model="username"
            :disabled="saving"
            :maxlength="50"
            :status="validName ? undefined : 'error'"
          />
          <p v-if="!validName" class="profile-hint profile-error">{{ isZh ? '请输入 2–50 个字符' : 'Enter 2–50 characters' }}</p>
        </div>
        <div class="profile-email-row">
          <span class="profile-field-label">{{ isZh ? '登录邮箱' : 'Login email' }}</span>
          <span class="profile-email">{{ auth.currentUser?.email }}</span>
        </div>
      </div>
      <t-alert v-if="error" theme="error" :message="error" role="alert" />
    </form>
  </t-dialog>
</template>

<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import { MessagePlugin } from 'tdesign-vue-next'
import { useAuthStore } from '@/stores/auth'
import type { UserProfileUpdate } from '@/api/auth'

const emit = defineEmits<{ close: [] }>()
const auth = useAuthStore()
const { locale } = useI18n()
const isZh = computed(() => locale.value.startsWith('zh'))
const nameId = useId()
const originalName = auth.currentUser?.username || ''
const originalAvatar = auth.currentUser?.avatar_url || null
const username = ref(originalName)
const avatar = ref<string | null>(originalAvatar)
const fileInput = ref<HTMLInputElement | null>(null)
const saving = ref(false)
const reading = ref(false)
const error = ref('')
const validName = computed(() => {
  const name = username.value.trim()
  return [...name].length >= 2 && [...name].length <= 50 && !/[\x00-\x1f\x7f]/.test(name)
})
const changed = computed(
  () => username.value.trim() !== originalName || avatar.value !== originalAvatar,
)

function close() {
  if (!saving.value) emit('close')
}

async function selectAvatar(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  error.value = ''
  if (
    !['image/jpeg', 'image/png', 'image/webp'].includes(file.type) ||
    file.size > 2 * 1024 * 1024
  ) {
    error.value = isZh.value
      ? '请选择不超过 2 MB 的 JPG、PNG 或 WebP 图片。'
      : 'Choose a JPG, PNG or WebP image up to 2 MB.'
    return
  }
  reading.value = true
  try {
    const data = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.onerror = () => reject(new Error('Image read failed'))
      reader.readAsDataURL(file)
    })
    const image = new Image()
    image.src = data
    await image.decode()
    if (image.naturalWidth * image.naturalHeight > 16_000_000) throw new Error('Image too large')
    avatar.value = data
  } catch {
    error.value = isZh.value
      ? '无法读取图片，请选择有效且不超过 1600 万像素的图片。'
      : 'Choose a valid image up to 16 megapixels.'
  } finally {
    reading.value = false
  }
}

async function save() {
  if (saving.value || reading.value || !validName.value || !changed.value) return
  saving.value = true
  error.value = ''
  const changes: UserProfileUpdate = {}
  if (username.value.trim() !== originalName) changes.username = username.value.trim()
  if (avatar.value !== originalAvatar) changes.avatar_url = avatar.value
  try {
    await auth.updateProfile(changes)
    MessagePlugin.success(isZh.value ? '个人资料已更新' : 'Profile updated')
    emit('close')
  } catch (reason: any) {
    if (reason?.response?.status === 409) {
      error.value = isZh.value ? '该用户名已被使用，请换一个。' : 'That username is already taken.'
    } else if (reason?.response?.status === 422) {
      error.value = isZh.value
        ? '请检查用户名和图片格式、大小后重试。'
        : 'Check the username, image format and size.'
    } else {
      error.value = isZh.value ? '保存失败，请稍后重试。' : 'Could not save. Please try again.'
    }
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.profile-form {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  align-items: start;
  gap: 16px 24px;
}
.profile-avatar-row {
  display: flex;
  flex-direction: column;
  align-items: center;
  align-self: center;
  gap: 6px;
}
.profile-avatar {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 72px;
  height: 72px;
  flex-shrink: 0;
  overflow: hidden;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: var(--pa-accent-soft);
  color: var(--pa-accent);
  font: inherit;
  font-size: 26px;
  cursor: pointer;
}
.profile-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.profile-avatar-overlay {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  background: rgb(0 0 0 / 55%);
  color: #fff;
  font-size: 12px;
  opacity: 0;
  transition: opacity 0.15s;
}
.profile-avatar:hover .profile-avatar-overlay,
.profile-avatar:focus-visible .profile-avatar-overlay {
  opacity: 1;
}
.profile-avatar:focus-visible {
  outline: 2px solid var(--pa-accent);
  outline-offset: 3px;
}
.profile-avatar:disabled {
  cursor: default;
}
.profile-fields {
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-width: 0;
}
.profile-field {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 7px;
  min-width: 0;
}
.profile-form > .t-alert {
  grid-column: 1 / -1;
}
.profile-field-label {
  color: var(--pa-ink);
  font-size: 13px;
  font-weight: 600;
}
.profile-field :deep(.t-input__wrap) {
  width: 100%;
}
.profile-hint {
  color: var(--pa-muted);
  font-size: 12px;
  margin: 0;
  line-height: 1.5;
}
.profile-email-row {
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.profile-email {
  color: var(--pa-muted);
  font-size: 13px;
  overflow-wrap: anywhere;
}
.profile-error {
  color: var(--pa-danger);
}
@media (max-width: 380px) {
  .profile-form {
    grid-template-columns: 1fr;
  }
  .profile-avatar-row {
    align-items: flex-start;
  }
}
@media (hover: none) {
  .profile-avatar-overlay {
    opacity: 1;
  }
}
</style>
