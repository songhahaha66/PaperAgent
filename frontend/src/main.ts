import { createApp } from 'vue'
import { createPinia } from 'pinia'
import * as TDesign from 'tdesign-vue-next'
import App from './App.vue'
import router from './router'
import 'tdesign-vue-next/es/style/index.css'
import 'tdesign-icons-vue-next/esm/style/index.css'

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(TDesign)

app.mount('#app')