<template>
  <t-dialog
    :visible="true"
    :header="isZh ? '个人资料' : 'Profile'"
    width="420px"
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
        <span class="profile-avatar">
          <img v-if="avatar" :src="avatar" :alt="isZh ? '头像预览' : 'Avatar preview'" />
          <span v-else>{{ (username.trim() || '?').charAt(0).toUpperCase() }}</span>
        </span>
        <div>
          <t-button variant="outline" :disabled="saving || reading" @click="fileInput?.click()">
            {{ isZh ? '更换头像' : 'Change avatar' }}
          </t-button>
          <t-button
            v-if="avatar"
            variant="text"
            :disabled="saving || reading"
            @click="avatar = null"
          >
            {{ isZh ? '恢复默认' : 'Reset' }}
          </t-button>
        </div>
        <input
          ref="fileInput"
          type="file"
          accept="image/png,image/jpeg,image/webp"
          hidden
          @change="selectAvatar"
        />
      </div>
      <p class="profile-hint">
        {{
          isZh
            ? '支持 JPG、PNG、WebP，最大 2 MB，自动居中裁剪为正方形。'
            : 'JPG, PNG or WebP up to 2 MB. Images are center-cropped to a square.'
        }}
      </p>
      <label :for="nameId">{{ isZh ? '用户名' : 'Username' }}</label>
      <t-input
        :id="nameId"
        v-model="username"
        :disabled="saving"
        :maxlength="50"
        :status="validName ? undefined : 'error'"
      />
      <p class="profile-hint">
        {{ isZh ? '2–50 个字符，用户名不可重复。' : '2–50 characters. Usernames must be unique.' }}
      </p>
      <label>{{ isZh ? '登录邮箱' : 'Login email' }}</label>
      <p class="profile-email">{{ auth.currentUser?.email }}</p>
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
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.profile-avatar-row {
  display: flex;
  align-items: center;
  gap: 20px;
}
.profile-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 76px;
  height: 76px;
  flex-shrink: 0;
  overflow: hidden;
  border-radius: 50%;
  background: var(--pa-accent-soft);
  color: var(--pa-accent);
  font-size: 28px;
}
.profile-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.profile-hint {
  color: var(--pa-muted);
  font-size: 12px;
  margin: 0 0 10px;
  line-height: 1.6;
}
.profile-email {
  margin: 0 0 10px;
  color: var(--pa-muted);
  overflow-wrap: anywhere;
}
</style>
