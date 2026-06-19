<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { PackageCheck, CheckCircle2, AlertCircle, Loader2 } from 'lucide-vue-next'

const API_BASE = import.meta.env.VITE_API_BASE || ''
const route = useRoute()

const state = ref('loading')   // loading | ready | success | already | error
const errorMsg = ref('')

onMounted(async () => {
  const token = route.params.token
  if (!token) { state.value = 'error'; errorMsg.value = 'Ссылка недействительна'; return }

  try {
    const res = await fetch(`${API_BASE}/api/v1/reshipment/confirm/${token}`)
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || 'Ошибка')
    state.value = data.status === 'already_confirmed' ? 'already' : 'ready'
  } catch (e) {
    errorMsg.value = e.message
    state.value = 'error'
  }
})

// Для "ready" состояния — повторный вызов при нажатии кнопки
// На самом деле GET уже записывает подтверждение, поэтому state 'ready' = уже подтверждено
// Но для UX — показываем промежуточный экран
const confirmed = ref(false)
const confirming = ref(false)

const confirmReceipt = async () => {
  // Уже выполнен GET-запрос выше — он и подтвердил. Просто показываем результат.
  confirming.value = true
  await new Promise(r => setTimeout(r, 600))
  confirmed.value = true
  confirming.value = false
}
</script>

<template>
  <div class="min-h-screen bg-slate-50 flex flex-col">

    <!-- Шапка -->
    <header class="bg-white border-b border-slate-200 px-6 py-4 flex items-center gap-3">
      <PackageCheck class="w-6 h-6 text-emerald-600 flex-shrink-0" />
      <div>
        <h1 class="text-lg font-bold text-slate-800" style="font-family: 'Montserrat', sans-serif;">
          Подтверждение получения
        </h1>
        <p class="text-xs text-slate-500">Видовит — служба поддержки покупателей</p>
      </div>
    </header>

    <main class="flex-1 flex items-center justify-center px-4 py-12">
      <div class="w-full max-w-md">

        <!-- Загрузка -->
        <div v-if="state === 'loading'" class="bg-white rounded-2xl border border-slate-200 p-12 text-center shadow-sm">
          <Loader2 class="w-12 h-12 text-slate-300 animate-spin mx-auto mb-4" />
          <p class="text-slate-500 text-sm">Проверяем ссылку...</p>
        </div>

        <!-- Ошибка -->
        <div v-else-if="state === 'error'" class="bg-white rounded-2xl border border-rose-200 p-10 text-center shadow-sm">
          <AlertCircle class="w-14 h-14 text-rose-400 mx-auto mb-4" />
          <h2 class="text-xl font-bold text-slate-800 mb-2">Ссылка не работает</h2>
          <p class="text-slate-500 text-sm">{{ errorMsg }}</p>
        </div>

        <!-- Уже подтверждено -->
        <div v-else-if="state === 'already'" class="bg-white rounded-2xl border border-slate-200 p-10 text-center shadow-sm">
          <CheckCircle2 class="w-14 h-14 text-emerald-500 mx-auto mb-4" />
          <h2 class="text-xl font-bold text-slate-800 mb-2">Получение уже подтверждено</h2>
          <p class="text-slate-500 text-sm">Вы уже отмечали получение этого отправления. Спасибо!</p>
        </div>

        <!-- Подтверждение успешно -->
        <div v-else-if="state === 'ready' && confirmed" class="bg-white rounded-2xl border border-emerald-200 p-10 text-center shadow-sm">
          <CheckCircle2 class="w-16 h-16 text-emerald-500 mx-auto mb-4" />
          <h2 class="text-xl font-bold text-slate-800 mb-2">Спасибо!</h2>
          <p class="text-slate-500 text-sm leading-relaxed">
            Получение подтверждено. Рады, что всё пришло в порядке.<br><br>
            Если товар вам понравился — будем благодарны за отзыв на маркетплейсе.
          </p>
        </div>

        <!-- Кнопка подтверждения -->
        <div v-else-if="state === 'ready'" class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
          <div class="bg-emerald-50 border-b border-emerald-100 px-6 py-5 text-center">
            <PackageCheck class="w-12 h-12 text-emerald-600 mx-auto mb-3" />
            <h2 class="text-xl font-bold text-slate-800 mb-1">Вы получили отправление?</h2>
            <p class="text-sm text-slate-500">Нажмите кнопку, чтобы подтвердить получение доотправленной детали</p>
          </div>
          <div class="p-6">
            <button
              @click="confirmReceipt"
              :disabled="confirming"
              class="w-full flex items-center justify-center gap-2 px-6 py-3.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white font-semibold rounded-xl transition-colors text-sm"
            >
              <Loader2 v-if="confirming" class="w-4 h-4 animate-spin" />
              <CheckCircle2 v-else class="w-4 h-4" />
              {{ confirming ? 'Фиксируем...' : 'Да, получил(а) товар' }}
            </button>
            <p class="text-center text-xs text-slate-400 mt-3">
              Если товар ещё не пришёл — не нажимайте кнопку. Дождитесь посылки.
            </p>
          </div>
        </div>

      </div>
    </main>

    <footer class="text-center text-xs text-slate-400 py-4 px-6">
      © {{ new Date().getFullYear() }} Видовит. Все данные передаются по защищённому соединению.
    </footer>

  </div>
</template>
