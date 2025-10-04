<template>
  <div class="mobile-warning">
    <div class="warning-content">
      <t-icon name="error-circle" size="64px" class="warning-icon" />
      <h1>请更换大屏设备使用</h1>
      <p>工作页面功能复杂，需要更大屏幕尺寸才能获得最佳使用体验</p>
      <p>请使用平板或电脑设备访问此页面</p>
      <t-button theme="primary" @click="goToHome">返回首页</t-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

// 返回首页
const goToHome = () => {
  router.push('/home')
}
</script>

<style scoped>
/* 手机端提示界面样式 */
.mobile-warning {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background-color: #f8f9fa;
  padding: 20px;
}

.warning-content {
  text-align: center;
  max-width: 400px;
  padding: 40px 20px;
  background: white;
  border-radius: 12px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.1);
}

.warning-icon {
  color: #ff4d4f;
  margin-bottom: 24px;
}

.warning-content h1 {
  font-size: 24px;
  color: #2c3e50;
  margin-bottom: 16px;
  font-weight: 600;
}

.warning-content p {
  font-size: 16px;
  color: #666;
  margin-bottom: 12px;
  line-height: 1.5;
}

.warning-content p:last-of-type {
  margin-bottom: 32px;
}
</style>