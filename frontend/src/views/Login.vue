<template>
  <div class="login-page">
    <div class="login-container">
      <div class="language-control"><LanguageSwitcher /></div>
      <div class="login-header">
        <div class="title-with-logo">
          <img src="/logo.png" alt="PaperAgent Logo" class="logo" />
          <h1>PaperAgent</h1>
        </div>
        <p>{{ isLogin ? t('login.signInTitle') : t('login.signUpTitle') }}</p>
      </div>

      <t-form
        ref="formRef"
        :data="formData"
        :rules="rules"
        :label-width="0"
        @submit="onSubmit"
        :required-mark="false"
        class="login-form"
      >
        <t-form-item name="email">
          <t-input
            v-model="formData.email"
            :placeholder="t('login.email')"
            type="email"
            autocomplete="username webauthn"
            clearable
          >
            <template #prefix-icon>
              <MailIcon />
            </template>
          </t-input>
        </t-form-item>

        <t-form-item v-if="!isLogin" name="username">
          <t-input v-model="formData.username" :placeholder="t('login.username')" clearable>
            <template #prefix-icon>
              <UserIcon />
            </template>
          </t-input>
        </t-form-item>

        <t-form-item name="password">
          <t-input v-model="formData.password" :placeholder="t('login.password')" type="password">
            <template #prefix-icon>
              <LockOnIcon />
            </template>
          </t-input>
        </t-form-item>

        <t-form-item v-if="!isLogin" name="confirmPassword">
          <t-input
            v-model="formData.confirmPassword"
            :placeholder="t('login.confirmPassword')"
            type="password"
          >
            <template #prefix-icon>
              <LockOnIcon />
            </template>
          </t-input>
        </t-form-item>

        <t-form-item>
          <t-button type="submit" theme="primary" size="large" block :loading="passwordLoading">
            {{ isLogin ? t('login.signIn') : t('login.signUp') }}
          </t-button>
        </t-form-item>
      </t-form>

      <div v-if="isLogin && passkeySupported && passkeyOriginOk" class="passkey-section">
        <div class="divider">
          <span>{{ t('login.or') }}</span>
        </div>
        <t-button
          theme="default"
          variant="outline"
          size="large"
          block
          :loading="passkeyLoading"
          @click="onPasskeyLogin"
        >
          <template #icon>
            <SecuredIcon />
          </template>
          {{ t('login.passkeySignIn') }}
        </t-button>
      </div>

      <div class="form-footer">
        <p v-if="isLogin">
          {{ t('login.noAccount') }}
          <t-link theme="primary" @click="switchMode">{{ t('login.signUpNow') }}</t-link>
        </p>
        <p v-else>
          {{ t('login.hasAccount') }}
          <t-link theme="primary" @click="switchMode">{{ t('login.signInNow') }}</t-link>
        </p>
        <p v-if="isLogin" class="passkey-hint">{{ t('login.passkeyHint') }}</p>
        <p v-if="isLogin && originHint" class="passkey-hint">{{ originHint }}</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import LanguageSwitcher from '@/components/LanguageSwitcher.vue'
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { MessagePlugin, type FormInstanceFunctions } from 'tdesign-vue-next'
import { MailIcon, LockOnIcon, UserIcon, SecuredIcon } from 'tdesign-icons-vue-next'
import { useAuthStore } from '@/stores/auth'
import { authAPI } from '@/api/auth'
import {
  assertPasskey,
  cancelPasskeyCeremony,
  isPasskeyAutofillSupported,
  isPasskeyCanceled,
  isPasskeyOriginSupported,
  isPasskeySupported,
} from '@/utils/passkey'

const { t, locale } = useI18n()
const formRef = ref<FormInstanceFunctions>()
watch(locale, () => formRef.value?.clearValidate())
const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const isLogin = ref(true)
const passkeySupported = ref(false)
const passkeyOriginOk = ref(true)
const originHint = computed(() => {
  if (passkeyOriginOk.value) return null
  return t(
    /^\d{1,3}(?:\.\d{1,3}){3}$/.test(window.location.hostname)
      ? 'login.passkeyIpHint'
      : 'login.passkeySecureHint',
  )
})
const passkeyLoading = ref(false)
const passwordLoading = ref(false)
let conditionalLoginActive = false

// 检查URL参数，如果是注册模式则自动切换
if (route.query.mode === 'register') {
  isLogin.value = false
}

const formData = reactive({
  email: '',
  username: '',
  password: '',
  confirmPassword: '',
})

const rules = computed(() => ({
  email: [
    { required: true, message: t('login.emailRequired'), type: 'error' },
    { email: true, message: t('login.emailInvalid'), type: 'error' },
  ],
  username: [
    { required: true, message: t('login.usernameRequired'), type: 'error' },
    { min: 2, message: t('login.usernameMin'), type: 'error' },
    { max: 50, message: t('login.usernameMax'), type: 'error' },
  ],
  password: [
    { required: true, message: t('login.passwordRequired'), type: 'error' },
    { min: 6, message: t('login.passwordMin'), type: 'error' },
  ],
  confirmPassword: [
    { required: true, message: t('login.confirmRequired'), type: 'error' },
    {
      validator: (val: string) => val === formData.password,
      message: t('login.passwordMismatch'),
      type: 'error',
    },
  ],
}))

const switchMode = () => {
  isLogin.value = !isLogin.value
  cancelPasskeyCeremony()
  // 清空表单数据
  formData.email = ''
  formData.username = ''
  formData.password = ''
  formData.confirmPassword = ''
  if (isLogin.value) {
    startConditionalLogin()
  }
}

const finishPasskeyLogin = async (challengeId: string, credential: object) => {
  const tokenResponse = await authAPI.verifyPasskeyLogin(challengeId, credential)
  const result = await authStore.loginWithToken(tokenResponse.access_token)
  if (result.success) {
    MessagePlugin.success(t('login.success'))
    router.push('/home')
  } else {
    MessagePlugin.error(result.error || t('login.failed'))
  }
}

const startConditionalLogin = async () => {
  if (!isLogin.value || !passkeySupported.value || !passkeyOriginOk.value) {
    return
  }
  if (!(await isPasskeyAutofillSupported())) {
    return
  }
  if (conditionalLoginActive) return
  conditionalLoginActive = true
  try {
    const { challenge_id, options } = await authAPI.getPasskeyLoginOptions()
    const assertion = await assertPasskey(options as any, true)
    await finishPasskeyLogin(challenge_id, assertion)
  } catch (error) {
    if (!isPasskeyCanceled(error)) {
      console.warn('Passkey 自动填充登录未完成:', error)
    }
  } finally {
    conditionalLoginActive = false
  }
}

const onPasskeyLogin = async () => {
  if (!passkeySupported.value) {
    MessagePlugin.warning(t('login.passkeyUnsupported'))
    return
  }
  passkeyLoading.value = true
  try {
    const email = formData.email.trim() || undefined
    const { challenge_id, options } = await authAPI.getPasskeyLoginOptions(email)
    const assertion = await assertPasskey(options as any)
    await finishPasskeyLogin(challenge_id, assertion)
  } catch (error) {
    if (isPasskeyCanceled(error)) {
      MessagePlugin.info(t('login.passkeyCanceled'))
    } else {
      console.error('Passkey 登录失败:', error)
      MessagePlugin.error(error instanceof Error ? error.message : t('login.passkeyFailed'))
    }
  } finally {
    passkeyLoading.value = false
    startConditionalLogin()
  }
}

onMounted(() => {
  passkeySupported.value = isPasskeySupported()
  passkeyOriginOk.value = isPasskeyOriginSupported()
  if (isLogin.value) {
    startConditionalLogin()
  }
})

onUnmounted(() => {
  cancelPasskeyCeremony()
})

const onSubmit = async ({ validateResult }: { validateResult: any }) => {
  if (validateResult === true) {
    cancelPasskeyCeremony()
    passwordLoading.value = true
    try {
      if (isLogin.value) {
        // 登录逻辑
        const result = await authStore.login({
          email: formData.email,
          password: formData.password,
        })

        if (result.success) {
          MessagePlugin.success(t('login.success'))
          router.push('/home')
        } else {
          MessagePlugin.error(result.error || t('login.failed'))
        }
      } else {
        // 注册逻辑
        const result = await authStore.register({
          email: formData.email,
          username: formData.username,
          password: formData.password,
        })

        if (result.success) {
          MessagePlugin.success(t('login.registerSuccess'))
          // 切换到登录模式
          isLogin.value = true
          // 清空表单
          formData.email = ''
          formData.username = ''
          formData.password = ''
          formData.confirmPassword = ''
        } else {
          MessagePlugin.error(result.error || t('login.registerFailed'))
        }
      }
    } catch (error) {
      console.error('操作失败:', error)
      MessagePlugin.error(t('login.operationFailed'))
    } finally {
      passwordLoading.value = false
    }
  }
}
</script>

<style scoped>
.language-control {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 20px;
}

.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #f5f7fa 0%, #e4edf5 100%);
  padding: 20px;
}

.login-container {
  width: 100%;
  max-width: 400px;
  padding: 40px 30px;
  background: white;
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.1);
  text-align: center;
}

.login-header .title-with-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-bottom: 12px;
}

.login-header .logo {
  width: 40px;
  height: 40px;
  object-fit: contain;
}

.login-header h1 {
  font-size: 2rem;
  color: #2c3e50;
  margin: 0;
  line-height: 1;
}

.login-header p {
  color: #7f8c8d;
  margin-top: 0;
}

.login-form {
  margin: 30px 0;
  text-align: left;
}

.login-form :deep(.t-form__item) {
  width: 100%;
}

.login-form :deep(.t-form__controls) {
  margin-left: 0;
}

@media (max-width: 768px) {
  .login-container {
    padding: 32px 20px;
  }
}

.form-footer p {
  margin: 0;
  color: #7f8c8d;
}

.passkey-section {
  margin: -10px 0 24px;
}

.divider {
  display: flex;
  align-items: center;
  color: #b0b8bf;
  font-size: 13px;
  margin-bottom: 16px;
}

.divider::before,
.divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: #e8edf2;
}

.divider span {
  padding: 0 12px;
}

.passkey-hint {
  margin-top: 12px !important;
  font-size: 12px;
  color: #95a5a6;
}
</style>
