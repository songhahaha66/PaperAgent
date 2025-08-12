<template>
  <div class="introduction-page">
    <div class="header">
      <h1>PaperAgent 论文智能生成系统</h1>
      <p class="subtitle">基于AI技术的学术论文自动生成平台</p>
    </div>

    <div class="content">
      <t-card class="feature-card">
        <h2>项目介绍</h2>
        <p>
          PaperAgent是一个基于人工智能技术的学术论文自动生成系统，旨在帮助研究人员和学生快速生成高质量的学术论文。
          通过先进的自然语言处理技术和深度学习模型，系统能够根据用户提供的主题和要求自动生成结构完整、内容丰富的学术论文。
        </p>
      </t-card>

      <div class="features-grid">
        <t-card class="feature-card">
          <t-icon name="edit-1" class="feature-icon" />
          <h3>智能写作</h3>
          <p>基于AI技术自动生成论文内容，包括摘要、引言、正文和结论等部分</p>
        </t-card>

        <t-card class="feature-card">
          <t-icon name="book" class="feature-icon" />
          <h3>文献整合</h3>
          <p>自动检索和整合相关领域的学术文献，确保论文内容的权威性和时效性</p>
        </t-card>

        <t-card class="feature-card">
          <t-icon name="format-horizontal-align-bottom" class="feature-icon" />
          <h3>格式规范</h3>
          <p>支持多种学术期刊和会议的格式要求，自动生成符合规范的论文格式</p>
        </t-card>

        <t-card class="feature-card">
          <t-icon name="check-circle" class="feature-icon" />
          <h3>质量保证</h3>
          <p>内置查重和质量检测机制，确保论文原创性和学术水平</p>
        </t-card>
      </div>

      <t-card class="cta-card">
        <h2>开始使用PaperAgent</h2>
        <p>立即登录以体验智能论文生成服务</p>
        <div class="cta-buttons">
          <t-button theme="primary" size="large" @click="goToLogin">登录</t-button>
          <t-button theme="default" variant="outline" size="large" @click="goToRegister">注册</t-button>
        </div>
      </t-card>
    </div>

    <div class="footer">
      <p>&copy; 2024 PaperAgent. All rights reserved.</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

const goToLogin = () => {
  router.push('/login')
}

const goToRegister = () => {
  router.push('/login?mode=register')
}
</script>

<style scoped>
.introduction-page {
  min-height: 100vh;
  background: linear-gradient(135deg, #f5f7fa 0%, #e4edf5 100%);
  padding: 20px;
}

.header {
  text-align: center;
  padding: 60px 20px 40px;
}

.header h1 {
  font-size: 2.5rem;
  color: #2c3e50;
  margin-bottom: 16px;
}

.subtitle {
  font-size: 1.2rem;
  color: #7f8c8d;
  margin: 0;
}

.content {
  max-width: 1200px;
  margin: 0 auto;
}

.feature-card {
  margin-bottom: 24px;
  border-radius: 8px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
}

.feature-card h2 {
  color: #2c3e50;
  margin-top: 0;
}

.features-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 20px;
  margin: 30px 0;
}

.feature-icon {
  font-size: 2rem;
  color: #3498db;
  margin-bottom: 16px;
}

.feature-card h3 {
  color: #2c3e50;
  margin: 0 0 12px 0;
}

.cta-card {
  text-align: center;
  background: linear-gradient(135deg, #3498db 0%, #2c3e50 100%);
  color: white;
}

.cta-card h2 {
  color: white;
  margin-top: 0;
}

.cta-card p {
  font-size: 1.1rem;
  opacity: 0.9;
}

.cta-buttons {
  margin-top: 24px;
}

.cta-buttons .t-button {
  margin: 0 10px;
}

.footer {
  text-align: center;
  padding: 30px 0;
  color: #7f8c8d;
  margin-top: 40px;
}
</style>