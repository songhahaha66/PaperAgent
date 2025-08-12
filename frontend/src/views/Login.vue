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
            :loading="loading"
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
import { useRouter } from 'vue-router'
import { MessagePlugin } from 'tdesign-vue-next'
import { MailIcon, LockOnIcon } from 'tdesign-icons-vue-next'

const router = useRouter()
const isLogin = ref(true)
const loading = ref(false)

const formData = reactive({
  email: '',
  password: '',
  confirmPassword: ''
})

const rules = {
  email: [
    { required: true, message: '邮箱地址必填', type: 'error' },
    { email: true, message: '请输入正确的邮箱地址', type: 'error' }
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
  formData.password = ''
  formData.confirmPassword = ''
}

const onSubmit = ({ validateResult }: { validateResult: any }) => {
  if (validateResult === true) {
    loading.value = true
    
    // 模拟登录/注册过程
    setTimeout(() => {
      loading.value = false
      if (isLogin.value) {
        MessagePlugin.success('登录成功')
        router.push('/')
      } else {
        MessagePlugin.success('注册成功，请登录')
        isLogin.value = true
      }
    }, 1000)
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

.form-footer {
  border-top: 1px solid #eee;
  padding-top: 20px;
}

.form-footer p {
  margin: 0;
  color: #7f8c8d;
}

</style>