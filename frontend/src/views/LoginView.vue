<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Lock, Mail, AlertCircle, CheckCircle2 } from 'lucide-vue-next'
import { usePermissionsStore } from '../stores/permissions'
import { MODULE_ROUTES } from '../router/index'

const router = useRouter()
const permissionsStore = usePermissionsStore()
const isRegisterMode = ref(false)
const email = ref('')
const password = ref('')
const errorMsg = ref('')
const successMsg = ref('')
const isLoading = ref(false)

const handleSubmit = async () => {
  errorMsg.value = ''; successMsg.value = ''; isLoading.value = true

  try {
    if (isRegisterMode.value) {
      const res = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: email.value, password: password.value })
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Ошибка регистрации')
      successMsg.value = 'Регистрация успешна! Теперь выполните вход.'
      isRegisterMode.value = false; password.value = ''

    } else {
      const formData = new URLSearchParams()
      formData.append('username', email.value)
      formData.append('password', password.value)

      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: formData
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Неверный логин или пароль')

      localStorage.setItem('token', data.access_token)
      localStorage.setItem('role', data.role)
      localStorage.setItem('username', data.username)

      // Загружаем права и редиректим на первую доступную страницу
      permissionsStore.clear()
      await permissionsStore.load()
      const first = MODULE_ROUTES.find(r => permissionsStore.can(r.module))
      router.push(first ? first.path : '/ratings')
    }
  } catch (e) {
    errorMsg.value = e.message
  } finally {
    isLoading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-slate-50 p-4 font-sans absolute inset-0 z-[999]">
    <div class="max-w-md w-full bg-white rounded-3xl shadow-xl p-8 border border-slate-100">

      <div class="text-center mb-8">
        <div class="w-16 h-16 bg-blue-600 rounded-2xl mx-auto flex items-center justify-center mb-4 shadow-lg shadow-blue-200">
          <Lock class="w-8 h-8 text-white" />
        </div>
        <h2 class="text-2xl font-black text-slate-900 tracking-tight">{{ isRegisterMode ? 'Активация доступа' : 'Вход в систему' }}</h2>
        <p class="text-sm text-slate-500 mt-2 font-medium">
          {{ isRegisterMode ? 'Придумайте пароль для вашего Email' : 'Аналитический центр Data Lake' }}
        </p>
      </div>

      <div v-if="errorMsg" class="mb-6 p-4 bg-red-50 text-red-600 text-sm font-bold rounded-xl flex items-center gap-3">
        <AlertCircle class="w-5 h-5 flex-shrink-0" /> {{ errorMsg }}
      </div>
      <div v-if="successMsg" class="mb-6 p-4 bg-green-50 text-green-700 text-sm font-bold rounded-xl flex items-center gap-3">
        <CheckCircle2 class="w-5 h-5 flex-shrink-0" /> {{ successMsg }}
      </div>

      <form @submit.prevent="handleSubmit" class="space-y-5">
        <div>
          <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Ваш Email</label>
          <div class="relative">
            <Mail class="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" />
            <input v-model="email" type="email" required class="w-full bg-slate-50 border border-slate-200 text-slate-900 rounded-xl px-4 py-3 pl-12 font-semibold focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all" placeholder="name@company.com">
          </div>
        </div>
        <div>
          <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Пароль</label>
          <div class="relative">
            <Lock class="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" />
            <input v-model="password" type="password" required class="w-full bg-slate-50 border border-slate-200 text-slate-900 rounded-xl px-4 py-3 pl-12 font-semibold focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all" placeholder="••••••••">
          </div>
        </div>

        <button type="submit" :disabled="isLoading" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-black py-4 rounded-xl shadow-lg shadow-blue-200 transition-all flex items-center justify-center gap-2">
          {{ isLoading ? 'Загрузка...' : (isRegisterMode ? 'Зарегистрироваться' : 'Войти') }}
        </button>
      </form>

      <div class="mt-8 space-y-3 text-center">
        <button @click="isRegisterMode = !isRegisterMode; errorMsg = ''; successMsg = ''" class="text-sm font-bold text-slate-400 hover:text-blue-600 transition-colors">
          {{ isRegisterMode ? 'Уже есть аккаунт? Войти' : 'Есть приглашение? Активировать' }}
        </button>
        <p v-if="!isRegisterMode" class="text-xs text-slate-400">
          Забыли пароль? Обратитесь к администратору.
        </p>
      </div>

    </div>
  </div>
</template>
