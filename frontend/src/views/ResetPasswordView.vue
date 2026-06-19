<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Lock, AlertCircle, CheckCircle2 } from 'lucide-vue-next'

const route = useRoute()
const router = useRouter()

const token = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const errorMsg = ref('')
const successMsg = ref('')
const isLoading = ref(false)

onMounted(() => {
  token.value = route.query.token || ''
  if (!token.value) errorMsg.value = 'Неверная ссылка. Проверьте письмо.'
})

const handleSubmit = async () => {
  errorMsg.value = ''
  if (newPassword.value !== confirmPassword.value) {
    errorMsg.value = 'Пароли не совпадают.'
    return
  }
  if (newPassword.value.length < 6) {
    errorMsg.value = 'Пароль должен быть минимум 6 символов.'
    return
  }
  isLoading.value = true
  try {
    const res = await fetch('/api/auth/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: token.value, new_password: newPassword.value })
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || 'Ошибка сброса пароля')
    successMsg.value = data.message
    setTimeout(() => router.push('/login'), 2500)
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
        <h2 class="text-2xl font-black text-slate-900 tracking-tight">Новый пароль</h2>
        <p class="text-sm text-slate-500 mt-2 font-medium">Придумайте надёжный пароль для входа</p>
      </div>

      <div v-if="errorMsg" class="mb-6 p-4 bg-red-50 text-red-600 text-sm font-bold rounded-xl flex items-center gap-3">
        <AlertCircle class="w-5 h-5 flex-shrink-0" /> {{ errorMsg }}
      </div>
      <div v-if="successMsg" class="mb-6 p-4 bg-green-50 text-green-700 text-sm font-bold rounded-xl flex items-center gap-3">
        <CheckCircle2 class="w-5 h-5 flex-shrink-0" /> {{ successMsg }}
      </div>

      <form v-if="!successMsg && token" @submit.prevent="handleSubmit" class="space-y-5">
        <div>
          <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Новый пароль</label>
          <div class="relative">
            <Lock class="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" />
            <input v-model="newPassword" type="password" required minlength="6"
              class="w-full bg-slate-50 border border-slate-200 text-slate-900 rounded-xl px-4 py-3 pl-12 font-semibold focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all"
              placeholder="Минимум 6 символов">
          </div>
        </div>
        <div>
          <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Подтвердите пароль</label>
          <div class="relative">
            <Lock class="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" />
            <input v-model="confirmPassword" type="password" required minlength="6"
              class="w-full bg-slate-50 border border-slate-200 text-slate-900 rounded-xl px-4 py-3 pl-12 font-semibold focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all"
              placeholder="Повторите пароль">
          </div>
        </div>

        <button type="submit" :disabled="isLoading"
          class="w-full bg-blue-600 hover:bg-blue-700 text-white font-black py-4 rounded-xl shadow-lg shadow-blue-200 transition-all">
          {{ isLoading ? 'Сохраняем...' : 'Сохранить новый пароль' }}
        </button>
      </form>

      <div class="mt-6 text-center">
        <button @click="$router.push('/login')" class="text-sm font-bold text-slate-400 hover:text-blue-600 transition-colors">
          ← Вернуться к входу
        </button>
      </div>

    </div>
  </div>
</template>
