<script setup lang="ts">
// 这里只保留必要的应用设置
</script>

<template>
  <div class="app-container">
    <!-- 路由出口 -->
    <router-view />
  </div>
</template>

<style scoped>
.app-container {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}
</style>
