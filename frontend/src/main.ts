import { createApp } from 'vue'
import * as TDesign from 'tdesign-vue-next'
import App from './App.vue'
import router from './router'
import pinia from './stores'
import 'tdesign-vue-next/es/style/index.css'
import 'tdesign-icons-vue-next/esm/style/index.css'

const app = createApp(App)

app.use(pinia)
app.use(router)
app.use(TDesign)

app.mount('#app')