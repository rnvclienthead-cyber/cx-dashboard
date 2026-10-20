import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { getAuthToken, takeAfterLogin } from './authStorage'
import { bootstrapTelegram, isProbablyTelegramShell } from './telegramWebApp'

const app = createApp(App)

app.use(createPinia())
app.use(router)

// Директива v-click-outside для закрытия дропдаунов
app.directive('click-outside', {
  mounted(el, binding) {
    el._clickOutside = (event) => {
      if (!el.contains(event.target)) binding.value(event)
    }
    document.addEventListener('click', el._clickOutside, true)
  },
  unmounted(el) {
    document.removeEventListener('click', el._clickOutside, true)
  }
})

async function start() {
  const boot = bootstrapTelegram().catch(() => {})
  if (isProbablyTelegramShell()) {
    await Promise.race([
      boot,
      new Promise((resolve) => setTimeout(resolve, 2000)),
    ])
  }
  app.mount('#app')
  await router.isReady()
  if (!getAuthToken()) return
  const here = router.currentRoute.value
  if (here.path === '/login' || here.name === 'login') {
    router.replace(takeAfterLogin() || '/chats')
  }
}

start()