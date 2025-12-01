<template>
  <div class="introduction-page">
    <div class="custom-header">
      <div class="header-left">
        <img src="/logo.png" alt="Logo" width="36" />
        <h1 class="header-title">PaperAgent</h1>
      </div>
      <div class="header-right">
        <t-button
          theme="default"
          variant="outline"
          href="https://github.com/songhahaha66/PaperAgent"
          target="_blank"
          tag="a"
          style="background: white; color: #24292e; border: 1px solid #24292e"
        >
          <template #icon>
            <t-icon name="logo-github" />
          </template>
          GitHub
        </t-button>
      </div>
    </div>

    <!-- 主要内容区域 - 左右分栏 -->
    <div class="main-content">
      <!-- 左侧文字区域 -->
      <div class="left-section">
        <div class="hero-content">
          <h1 class="main-title">新一代文章写作助手</h1>
          <p class="main-subtitle">
            为你
            <span class="rotating-text-wrapper">
              <span
                v-for="(text, index) in rotatingTexts"
                :key="index"
                :class="[
                  'rotating-text',
                  {
                    active: currentTextIndex === index,
                    leaving: previousTextIndex === index
                  }
                ]"
              >
                {{ text }}
              </span>
            </span>
          </p>

          <div class="cta-buttons">
            <t-button theme="primary" size="large" @click="goToLogin"> 立即登陆 </t-button>
            <t-button theme="default" variant="outline" size="large" @click="goToRegister">
              注册账号
            </t-button>
          </div>
        </div>
      </div>

      <!-- 右侧特性展示区域 -->
      <div class="right-section">
        <div class="features-grid">
          <div class="feature-card">
            <h3>一键生成完整论文</h3>
            <p>从选题到成稿，AI全程辅助</p>
          </div>

          <div class="feature-card">
            <h3>支持多种大模型</h3>
            <p>兼容主流AI模型，自由选择</p>
          </div>

          <div class="feature-card">
            <h3>自定义论文模板</h3>
            <p>上传模板，按格式生成</p>
          </div>

          <div class="feature-card">
            <h3>真实数据可视化</h3>
            <p>AI自动生成图表和数据</p>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部 -->
    <div class="footer">
      <p>&copy; 2025 PaperAgent. All rights reserved.</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { onMounted, ref, onUnmounted } from 'vue'

const router = useRouter()
const authStore = useAuthStore()

// 副标题轮播文字
const rotatingTexts = ['排版', '生成图表','执行代码']
const currentTextIndex = ref(0)
const previousTextIndex = ref(-1)
let textRotateInterval: number | null = null

// 如果用户已登录，直接跳转到主页
onMounted(() => {
  if (authStore.isAuthenticated) {
    router.push('/home')
  }

  // 副标题文字轮播 - 每2秒切换
  textRotateInterval = window.setInterval(() => {
    previousTextIndex.value = currentTextIndex.value
    currentTextIndex.value = (currentTextIndex.value + 1) % rotatingTexts.length
  }, 2000)
})

onUnmounted(() => {
  if (textRotateInterval) {
    clearInterval(textRotateInterval)
  }
})

const goToLogin = () => {
  router.push('/login')
}

const goToRegister = () => {
  router.push('/login?mode=register')
}
</script>

<style scoped>
.introduction-page {
  height: 100vh;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  display: flex;
  flex-direction: column;
  background: white;
}

.custom-header {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 40px;
  background: white;
  width: 100%;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-title {
  font-size: 1.5rem;
  color: #2c3e50;
  margin: 0;
}

/* 主要内容区域 - 左右分栏 */
.main-content {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
  gap: 60px;
  max-width: 1400px;
  margin: 0 auto;
  width: 100%;
}

/* 左侧区域 */
.left-section {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.hero-content {
  text-align: center;
  max-width: 500px;
}

.main-title {
  font-size: 3.5rem;
  font-weight: 700;
  color: #2c3e50;
  margin: 0 0 16px 0;
  letter-spacing: -1px;
}

.main-subtitle {
  font-size: 1.25rem;
  color: #5a6c7d;
  margin: 0 0 40px 0;
  line-height: 1.6;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.rotating-text-wrapper {
  position: relative;
  display: inline-block;
  height: 1.6em;
  width: 120px;
  overflow: hidden;
  vertical-align: bottom;
}

.rotating-text {
  position: absolute;
  left: 0;
  right: 0;
  opacity: 0;
  transform: translateY(100%);
  transition: all 0.5s ease-in-out;
  color: #0052d9;
  font-weight: 600;
}

.rotating-text.active {
  opacity: 1;
  transform: translateY(0);
}

.rotating-text.leaving {
  opacity: 0;
  transform: translateY(-100%);
}

.cta-buttons {
  display: flex;
  gap: 16px;
  justify-content: center;
}

/* 右侧特性展示区域 */
.right-section {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  max-width: 600px;
}

.features-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 20px;
  width: 100%;
}

.feature-card {
  background: #f8f9fa;
  border-radius: 12px;
  padding: 30px 20px;
  text-align: center;
  transition: all 0.3s ease;
  border: 1px solid #e8e8e8;
}

.feature-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 8px 20px rgba(0, 82, 217, 0.1);
  border-color: #0052d9;
}

.feature-card h3 {
  color: #2c3e50;
  font-size: 1.25rem;
  margin: 0 0 12px 0;
  font-weight: 600;
}

.feature-card p {
  color: #5a6c7d;
  font-size: 0.95rem;
  line-height: 1.6;
  margin: 0;
}

/* 底部 */
.footer {
  text-align: center;
  padding: 20px;
  color: #7f8c8d;
  background: #f5f5f5;
}

/* 响应式设计 */
@media (max-width: 1024px) {
  .main-content {
    flex-direction: column;
    gap: 40px;
    padding: 30px 20px;
  }

  .custom-header {
    padding: 0 20px;
  }

  .left-section,
  .right-section {
    max-width: 100%;
    width: 70%;
  }

  .hero-content {
    max-width: 100%;
    width: 100%;
  }

  .main-title {
    font-size: 2.5rem;
  }

  .main-subtitle {
    font-size: 1.1rem;
  }
}

@media (max-width: 768px) {
  .custom-header {
    height: 56px;
    padding: 12px 16px 0 16px;
  }

  .main-content {
    padding: 20px 16px;
    justify-content: flex-start;
  }

  .main-title {
    font-size: 2rem;
  }

  .main-subtitle {
    font-size: 1rem;
  }

  .cta-buttons {
    flex-direction: column;
    width: 100%;
  }

  .cta-buttons .t-button {
    width: 100%;
  }

  .features-grid {
    grid-template-columns: 1fr;
    gap: 16px;
  }

  .feature-card {
    padding: 24px 16px;
  }

  .feature-card h3 {
    font-size: 1.1rem;
  }

  .feature-card p {
    font-size: 0.9rem;
  }
}
</style>
