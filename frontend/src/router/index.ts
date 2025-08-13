import { createRouter, createWebHistory } from 'vue-router'
import Introduction from '@/views/Introduction.vue'
import Login from '@/views/Login.vue'
import Home from '@/views/Home.vue'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'Introduction',
      component: Introduction
    },
    {
      path: '/login',
      name: 'Login',
      component: Login,
      meta: { requiresGuest: true } // 只有未登录用户才能访问
    },
    {
      path: '/home',
      name: 'Home',
      component: Home,
      meta: { requiresAuth: true } // 需要登录才能访问
    }
  ],
})

// 路由守卫
router.beforeEach((to, from, next) => {
  const authStore = useAuthStore()
  
  // 需要认证的路由
  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    next('/login')
    return
  }
  
  // 只有未登录用户才能访问的路由（如登录页）
  if (to.meta.requiresGuest && authStore.isAuthenticated) {
    next('/home')
    return
  }
  
  next()
})

export default router