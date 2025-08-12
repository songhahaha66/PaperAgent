<template>
  <div class="home-page">
    <div class="sidebar">
      <div class="sidebar-header">
        <h2>PaperAgent</h2>
      </div>
      
      <div class="sidebar-content">
        <t-menu theme="light" :default-value="'new'" class="menu">
          <t-menu-item value="new">
            <template #icon>
              <add-icon />
            </template>
            新建工作
          </t-menu-item>
          <t-menu-item value="history">
            <template #icon>
              <browse-icon />
            </template>
            历史工作
          </t-menu-item>
        </t-menu>
      </div>
      
      <div class="sidebar-footer">
        <t-button variant="outline" block>退出登录</t-button>
      </div>
    </div>
    
    <div class="main-content">
      <div class="workspace-header">
        <h1>论文生成工作区</h1>
        <p>在这里开始您的论文生成任务</p>
      </div>
      
      <div class="workspace-content">
        <t-card title="开始新任务" class="task-card">
          <t-input placeholder="请输入论文主题" />
          <t-button theme="primary" class="generate-button">生成论文</t-button>
        </t-card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { AddIcon, BrowseIcon } from 'tdesign-icons-vue-next'
</script>

<style scoped>
.home-page {
  display: flex;
  min-height: 100vh;
  background: #f5f7fa;
}

.sidebar {
  width: 240px;
  background: white;
  border-right: 1px solid #eee;
  display: flex;
  flex-direction: column;
  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.1);
}

.sidebar-header {
  padding: 20px;
  border-bottom: 1px solid #eee;
}

.sidebar-header h2 {
  margin: 0;
  color: #2c3e50;
}

.sidebar-content {
  flex: 1;
  padding: 20px 0;
}

.menu {
  border-right: none;
}

.sidebar-footer {
  padding: 20px;
  border-top: 1px solid #eee;
}

.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.workspace-header {
  padding: 30px;
  background: white;
  border-bottom: 1px solid #eee;
}

.workspace-header h1 {
  margin: 0 0 8px 0;
  color: #2c3e50;
}

.workspace-header p {
  margin: 0;
  color: #7f8c8d;
}

.workspace-content {
  flex: 1;
  padding: 30px;
  overflow: auto;
}

.task-card {
  max-width: 600px;
}

.generate-button {
  margin-top: 20px;
}
</style>