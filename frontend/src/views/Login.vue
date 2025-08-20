<template>
  <div class="login-page">
    <div class="login-container">
      <div class="login-header">
        <h1>PaperAgent</h1>
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
            clearable
          >
            <template #prefix-icon>
              <MailIcon />
            </template>
          </t-input>
        </t-form-item>

        <t-form-item v-if="!isLogin" name="username">
          <t-input
            v-model="formData.username"
            placeholder="用户名"
            clearable
          >
            <template #prefix-icon>
              <UserIcon />
            </template>
          </t-input>
        </t-form-item>

        <t-form-item name="password">
          <t-input
            v-model="formData.password"
            placeholder="密码"
            type="password"
          >
            <template #prefix-icon>
              <LockOnIcon />
            </template>
          </t-input>
        </t-form-item>

        <t-form-item v-if="!isLogin" name="confirmPassword">
          <t-input
            v-model="formData.confirmPassword"
            placeholder="确认密码"
            type="password"
          >
            <template #prefix-icon>
              <LockOnIcon />
            </template>
          </t-input>
        </t-form-item>

        <t-form-item>
          <t-button
            type="submit"
            theme="primary"
            size="large"
            block
            :loading="authStore.loading"
          >
            {{ isLogin ? '登录' : '注册' }}
          </t-button>
        </t-form-item>
      </t-form>

      <div class="form-footer">
        <p v-if="isLogin">
          还没有账户？
          <t-link theme="primary" @click="switchMode">立即注册</t-link>
        </p>
        <p v-else>
          已有账户？
          <t-link theme="primary" @click="switchMode">立即登录</t-link>
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { MessagePlugin } from 'tdesign-vue-next'
import { MailIcon, LockOnIcon, UserIcon } from 'tdesign-icons-vue-next'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const isLogin = ref(true)

// 检查URL参数，如果是注册模式则自动切换
if (route.query.mode === 'register') {
  isLogin.value = false
}

const formData = reactive({
  email: '',
  username: '',
  password: '',
  confirmPassword: ''
})

const rules = {
  email: [
    { required: true, message: '邮箱地址必填', type: 'error' },
    { email: true, message: '请输入正确的邮箱地址', type: 'error' }
  ],
  username: [
    { required: true, message: '用户名必填', type: 'error' },
    { min: 2, message: '用户名至少2位', type: 'error' },
    { max: 50, message: '用户名最多50位', type: 'error' }
  ],
  password: [
    { required: true, message: '密码必填', type: 'error' },
    { min: 6, message: '密码至少6位', type: 'error' }
  ],
  confirmPassword: [
    { required: true, message: '请确认密码', type: 'error' },
    {
      validator: (val: string) => val === formData.password,
      message: '两次输入的密码不一致',
      type: 'error'
    }
  ]
}

const switchMode = () => {
  isLogin.value = !isLogin.value
  // 清空表单数据
  formData.email = ''
  formData.username = ''
  formData.password = ''
  formData.confirmPassword = ''
}

const onSubmit = async ({ validateResult }: { validateResult: any }) => {
  if (validateResult === true) {
    try {
      if (isLogin.value) {
        // 登录逻辑
        const result = await authStore.login({
          email: formData.email,
          password: formData.password
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
          password: formData.password
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

.login-header h1 {
  font-size: 2rem;
  color: #2c3e50;
  margin-bottom: 8px;
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
</style>