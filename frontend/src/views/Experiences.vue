<template>
  <LibraryShell
    title="个人经验"
    description="跨项目的方法提醒，由你验证与采用。每项研究仍需独立产生证据。"
    ><ResearchExperiences
  /></LibraryShell>
</template>
<script setup lang="ts">
import ResearchExperiences from '@/components/ResearchExperiences.vue'
import LibraryShell from '@/components/LibraryShell.vue'
</script>
<style scoped>
.personal-experiences {
  max-width: 1000px;
  margin: auto;
  padding: 24px;
}
</style>
