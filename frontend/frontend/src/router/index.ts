import { createRouter, createWebHistory } from 'vue-router'
import Introduction from '@/views/Introduction.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'Introduction',
      component: Introduction
    }
  ],
})

export default router