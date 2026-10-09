import { createApp } from 'vue'
import * as TDesign from 'tdesign-vue-next'
import App from './App.vue'
import router from './router'
import pinia from './stores'
import i18n from './lang'
import 'tdesign-vue-next/es/style/index.css'
import 'tdesign-icons-vue-next/esm/style/index.css'
import './theme.css'

const app = createApp(App)

app.use(pinia)
app.use(i18n)
app.use(router)
app.use(TDesign)

app.mount('#app')
