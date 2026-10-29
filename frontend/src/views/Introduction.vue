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
          class="repository-button"
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
          <h1 class="main-title">{{ t('introduction.title') }}</h1>
          <p class="main-subtitle">{{ t('introduction.subtitle') }}</p>

          <div class="cta-buttons">
            <t-button theme="primary" size="large" @click="goToLogin">
              {{ t('introduction.signIn') }}
            </t-button>
            <t-button theme="default" variant="outline" size="large" @click="goToRegister">
              {{ t('introduction.signUp') }}
            </t-button>
          </div>
        </div>
      </div>

      <!-- 右侧特性展示区域 -->
      <div class="right-section">
        <div class="features-grid">
          <div class="feature-card card-blue">
            <h3>{{ t('introduction.paperTitle') }}</h3>
            <p>{{ t('introduction.paperDescription') }}</p>
          </div>

          <div class="feature-card card-purple">
            <h3>{{ t('introduction.modelsTitle') }}</h3>
            <p>{{ t('introduction.modelsDescription') }}</p>
          </div>

          <div class="feature-card card-green">
            <h3>{{ t('introduction.templatesTitle') }}</h3>
            <p>{{ t('introduction.templatesDescription') }}</p>
          </div>

          <div class="feature-card card-orange">
            <h3>{{ t('introduction.dataTitle') }}</h3>
            <p>{{ t('introduction.dataDescription') }}</p>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部 -->
    <div class="footer">
      <p>{{ t('introduction.copyright') }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { onMounted } from 'vue'

const { t } = useI18n()
const router = useRouter()
const authStore = useAuthStore()

onMounted(() => {
  if (authStore.isAuthenticated) {
    router.push('/home')
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
.repository-button {
  background: var(--pa-surface);
  color: var(--pa-ink);
  border: 1px solid var(--pa-line-strong);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.introduction-page {
  height: 100vh;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  display: flex;
  flex-direction: column;
  background: var(--pa-page);
}

.custom-header {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 40px;
  background: var(--pa-surface);
  width: 100%;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-title {
  font-size: 17px;
  color: var(--pa-ink);
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
  font-size: 22px;
  font-weight: 700;
  color: var(--pa-ink);
  margin: 0 0 16px 0;
  letter-spacing: -1px;
}

.main-subtitle {
  font-size: 15px;
  color: var(--pa-muted);
  margin: 0 0 40px 0;
  line-height: 1.6;
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
  border-radius: var(--pa-radius-card);
  padding: 30px 20px;
  text-align: center;
}

/* 四种不同颜色的卡片 - 浅色调 */
.card-blue {
  background: var(--pa-info-soft);
  border: 1px solid var(--pa-info-border);
}

.card-purple {
  background: var(--pa-note-soft);
  border: 1px solid var(--pa-note-border);
}

.card-green {
  background: var(--pa-accent-soft);
  border: 1px solid var(--pa-accent-border);
}

.card-orange {
  background: var(--pa-warning-soft);
  border: 1px solid var(--pa-warning-border);
}

.feature-card h3 {
  color: var(--pa-ink);
  font-size: 17px;
  margin: 0 0 12px 0;
  font-weight: 600;
}

.feature-card p {
  color: var(--pa-muted);
  font-size: 15px;
  line-height: 1.6;
  margin: 0;
}

/* 底部 */
.footer {
  text-align: center;
  padding: 20px;
  color: var(--pa-muted);
  background: var(--pa-surface-muted);
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
    font-size: 22px;
  }

  .main-subtitle {
    font-size: 15px;
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
    font-size: 22px;
  }

  .main-subtitle {
    font-size: 15px;
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
    font-size: 15px;
  }

  .feature-card p {
    font-size: 15px;
  }
}
</style>
