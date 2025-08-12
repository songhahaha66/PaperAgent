import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import TDesign from 'tdesign-vue-next'
import 'tdesign-vue-next/es/style/index.css'
import pinia from './stores'
import i18n from './lang'

const app = createApp(App)

app.use(router)
app.use(TDesign)
app.use(pinia)
app.use(i18n)

app.mount('#app')