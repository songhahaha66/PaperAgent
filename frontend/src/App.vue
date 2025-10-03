<script setup lang="ts">
// 这里只保留必要的应用设置
</script>

<template>
  <div class="app-container">
    <!-- 路由出口 -->
    <router-view />
  </div>
</template>

<style>
/* 全局样式确保页面可以正常滚动 */
html, body {
  margin: 0;
  padding: 0;
  overflow-x: hidden;
  overflow-y: auto;
  height: auto;
  min-height: 100vh;
}
</style>

<style scoped>
.app-container {
  min-height: 100vh;
  overflow-x: hidden;
}
</style>
