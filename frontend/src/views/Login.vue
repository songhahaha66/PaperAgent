<template>
  <div class="login-page">
    <div class="login-container">
      <div class="login-header">
        <div class="title-with-logo">
          <img src="/logo.png" alt="PaperAgent Logo" class="logo" />
          <h1>PaperAgent</h1>
        </div>
        <p>{{ isLogin ? '登录到您的账户' : '创建新账户' }}</p>
      </div>

      <t-form
        :data="formData"
        :rules="rules"
        @submit="onSubmit"
        :required-mark="false"
        class="login-form"
      >
        <t-form-item name="email">
          <t-input
            v-model="formData.email"
            placeholder="邮箱地址"
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
          <t-input v-model="formData.username" placeholder="用户名" clearable>
            <template #prefix-icon>
              <UserIcon />
            </template>
          </t-input>
        </t-form-item>

        <t-form-item name="password">
          <t-input v-model="formData.password" placeholder="密码" type="password">
            <template #prefix-icon>
              <LockOnIcon />
            </template>
          </t-input>
        </t-form-item>

        <t-form-item v-if="!isLogin" name="confirmPassword">
          <t-input v-model="formData.confirmPassword" placeholder="确认密码" type="password">
            <template #prefix-icon>
              <LockOnIcon />
            </template>
          </t-input>
        </t-form-item>

        <t-form-item>
          <t-button type="submit" theme="primary" size="large" block :loading="authStore.loading">
            {{ isLogin ? '登录' : '注册' }}
          </t-button>
        </t-form-item>
      </t-form>

      <div v-if="isLogin && passkeySupported" class="passkey-section">
        <div class="divider"><span>或</span></div>
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
          使用 Passkey 登录
        </t-button>
      </div>

      <div class="form-footer">
        <p v-if="isLogin">
          还没有账户？
          <t-link theme="primary" @click="switchMode">立即注册</t-link>
        </p>
        <p v-else>
          已有账户？
          <t-link theme="primary" @click="switchMode">立即登录</t-link>
        </p>
        <p v-if="isLogin" class="passkey-hint">登录后可在账户设置中添加 Passkey</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { MessagePlugin } from 'tdesign-vue-next'
import { MailIcon, LockOnIcon, UserIcon, SecuredIcon } from 'tdesign-icons-vue-next'
import { useAuthStore } from '@/stores/auth'
import { authAPI } from '@/api/auth'
import {
  assertPasskey,
  cancelPasskeyCeremony,
  isPasskeyAutofillSupported,
  isPasskeyCanceled,
  isPasskeySupported,
} from '@/utils/passkey'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const isLogin = ref(true)
const passkeySupported = ref(false)
const passkeyLoading = ref(false)
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

const rules = {
  email: [
    { required: true, message: '邮箱地址必填', type: 'error' },
    { email: true, message: '请输入正确的邮箱地址', type: 'error' },
  ],
  username: [
    { required: true, message: '用户名必填', type: 'error' },
    { min: 2, message: '用户名至少2位', type: 'error' },
    { max: 50, message: '用户名最多50位', type: 'error' },
  ],
  password: [
    { required: true, message: '密码必填', type: 'error' },
    { min: 6, message: '密码至少6位', type: 'error' },
  ],
  confirmPassword: [
    { required: true, message: '请确认密码', type: 'error' },
    {
      validator: (val: string) => val === formData.password,
      message: '两次输入的密码不一致',
      type: 'error',
    },
  ],
}

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
    MessagePlugin.success('登录成功')
    router.push('/home')
  } else {
    MessagePlugin.error(result.error || '登录失败')
  }
}

const startConditionalLogin = async () => {
  if (!isLogin.value || !passkeySupported.value) {
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
    MessagePlugin.warning('当前浏览器不支持 Passkey')
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
      MessagePlugin.info('已取消 Passkey 登录')
    } else {
      console.error('Passkey 登录失败:', error)
      MessagePlugin.error(error instanceof Error ? error.message : 'Passkey 登录失败')
    }
  } finally {
    passkeyLoading.value = false
    startConditionalLogin()
  }
}

onMounted(() => {
  passkeySupported.value = isPasskeySupported()
  if (isLogin.value) {
    startConditionalLogin()
  }
})

onUnmounted(() => {
  cancelPasskeyCeremony()
})

const onSubmit = async ({ validateResult }: { validateResult: any }) => {
  if (validateResult === true) {
    try {
      if (isLogin.value) {
        // 登录逻辑
        const result = await authStore.login({
          email: formData.email,
          password: formData.password,
        })

        if (result.success) {
          MessagePlugin.success('登录成功')
          router.push('/home')
        } else {
          MessagePlugin.error(result.error || '登录失败')
        }
      } else {
        // 注册逻辑
        const result = await authStore.register({
          email: formData.email,
          username: formData.username,
          password: formData.password,
        })

        if (result.success) {
          MessagePlugin.success('注册成功，请登录')
          // 切换到登录模式
          isLogin.value = true
          // 清空表单
          formData.email = ''
          formData.username = ''
          formData.password = ''
          formData.confirmPassword = ''
        } else {
          MessagePlugin.error(result.error || '注册失败')
        }
      }
    } catch (error) {
      console.error('操作失败:', error)
      MessagePlugin.error('操作失败，请重试')
    }
  }
}
</script>

<style scoped>
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
  margin-left: -90px;
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
