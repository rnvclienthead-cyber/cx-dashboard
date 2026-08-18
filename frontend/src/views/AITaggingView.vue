<script setup>
import { ref, computed, onMounted, watch, onBeforeUnmount } from 'vue'
import { Tag, Play, Loader2, Cpu, RefreshCw, MessageSquarePlus, Zap } from 'lucide-vue-next'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'

const platformStore = usePlatformStore()

const activeTab = ref('tagging') // 'tagging' | 'feedback'

// Настройки
const batchSize = ref(20)
const selectedModel = ref('qwen-free')
const CLAIM_MODELS = [
  { key: 'qwen-free',   label: 'Qwen (бесплатный лимит)', icon: Cpu, hint: 'Plus выгорел · ротация', cost: 0, currency: '₽' },
  { key: 'yandex-lite', label: 'YandexGPT Lite', icon: Zap,    hint: 'Запасной', cost: 0.08, currency: '₽' },
]
const FEEDBACK_MODELS = [
  { key: 'qwen-reviews', label: 'Qwen (бесплатный лимит)', icon: Cpu, hint: 'Ротация моделей', cost: 0, currency: '₽' },
  { key: 'yandex-lite',  label: 'YandexGPT Lite',          icon: Zap, hint: 'Запасной', cost: 0.08, currency: '₽' },
]
const models = computed(() => activeTab.value === 'feedback' ? FEEDBACK_MODELS : CLAIM_MODELS)

const activeModel = computed(() => models.value.find(m => m.key === selectedModel.value) || models.value[0])

const isProcessing = ref(false)
const progress = ref(0)
const statusText = ref('')
const resultMessage = ref(null)

const stats = ref({ untagged: 0, untaggedFeedbacks: 0, jobRunning: false })
const isStatsLoading = ref(true)

let pollingInterval = null
let pollTicks = 0
const initialCount = ref(0)

const BG_JOB_KEY = 'cx_ai_job'

function saveJob() {
  localStorage.setItem(BG_JOB_KEY, JSON.stringify({
    initialCount: initialCount.value,
    tab: activeTab.value,
    platform: platformStore.platform,
    startedAt: Date.now()
  }))
}

function clearJob() {
  localStorage.removeItem(BG_JOB_KEY)
}

const fetchStats = async () => {
  isStatsLoading.value = true
  try {
    const res = await apiFetch(`/api/v1/ai/stats?platform=${platformStore.platform}`)
    const data = await res.json()
    if (data.status === 'success') {
      stats.value.untagged = data.untagged_count
      stats.value.untaggedFeedbacks = data.untagged_feedbacks
      stats.value.jobRunning = !!data.feedback_job_running
      stats.value.last_log = data.last_log
    }
  } catch (e) {
    console.error('Ошибка:', e)
  } finally {
    isStatsLoading.value = false
  }
}

onMounted(async () => {
  await fetchStats()
  if (stats.value.jobRunning) {
    activeTab.value = 'feedback'
    isProcessing.value = true
    if (!initialCount.value) initialCount.value = currentCount.value
    startPolling()
    return
  }
  const saved = localStorage.getItem(BG_JOB_KEY)
  if (saved) {
    const job = JSON.parse(saved)
    const age = Date.now() - job.startedAt
    if (age < 12 * 3_600_000 && job.platform === platformStore.platform) {
      initialCount.value = job.initialCount
      isProcessing.value = true
      activeTab.value = job.tab === 'feedback' ? 'feedback' : 'tagging'
      startPolling()
    } else {
      clearJob()
    }
  }
})

watch(() => platformStore.platform, () => {
  stopPolling()
  clearJob()
  resultMessage.value = null
  activeTab.value = 'tagging'
  fetchStats()
})

watch(activeTab, (tab) => {
  if (isProcessing.value) return
  resultMessage.value = null
  selectedModel.value = tab === 'feedback' ? 'qwen-reviews' : 'qwen-free'
})

onBeforeUnmount(() => {
  if (pollingInterval) { clearInterval(pollingInterval); pollingInterval = null }
})

function stopPolling() {
  if (pollingInterval) { clearInterval(pollingInterval); pollingInterval = null }
  isProcessing.value = false
  progress.value = 0
}

function startPolling() {
  pollTicks = 0
  if (pollingInterval) { clearInterval(pollingInterval); pollingInterval = null }
  pollingInterval = setInterval(async () => {
    await fetchStats()
    pollTicks += 1
    const processed = Math.max(0, initialCount.value - currentCount.value)
    progress.value = initialCount.value > 0
      ? Math.min(100, Math.floor((processed / initialCount.value) * 100))
      : 0

    if (activeTab.value === 'feedback') {
      statusText.value = stats.value.jobRunning
        ? `Идёт на сервере: готово ${processed} из ${initialCount.value}. Страницу можно закрыть.`
        : `Обработано ${processed} из ${initialCount.value}.`
      if (currentCount.value === 0) {
        finishJob('Очередь отзывов разобрана.')
        return
      }
      if (!stats.value.jobRunning && pollTicks >= 3) {
        if (processed > 0) finishJob('Фоновая разметка остановилась. Можно запустить ещё раз для остатка.')
        else finishJob('Фоновый процесс не найден. Запустите ещё раз.', true)
      }
      return
    }

    statusText.value = `Успешно обработано: ${processed} из ${initialCount.value} шт.`
    if (currentCount.value === 0 || progress.value >= 100) {
      finishJob('Все доступные данные были успешно обработаны ИИ.')
    }
  }, 5000)
}

function finishJob(text, isError = false) {
  stopPolling()
  clearJob()
  progress.value = isError ? progress.value : 100
  statusText.value = isError ? 'Остановка' : 'Завершено!'
  resultMessage.value = { type: isError ? 'error' : 'success', text }
}

const currentCount = computed(() => {
  if (activeTab.value === 'feedback') return stats.value.untaggedFeedbacks
  return stats.value.untagged
})

const estimatedCost = computed(() => {
  const { cost, currency } = activeModel.value
  if (cost == null) return { value: 'по факту', currency: '' }
  const count = activeTab.value === 'feedback' ? currentCount.value * 0.25 : currentCount.value
  const decimals = currency === '$' ? 3 : 2
  return { value: (count * cost).toFixed(decimals), currency }
})

const startProcess = async () => {
  if (currentCount.value === 0) return

  isProcessing.value = true
  progress.value = 0
  resultMessage.value = null
  initialCount.value = currentCount.value
  saveJob()

  const modelKey = selectedModel.value
  const endpoint = activeTab.value === 'feedback'
    ? `/api/v1/ai/start-feedback-tagging?platform=${platformStore.platform}&model=${modelKey}&batch_size=${batchSize.value}`
    : `/api/v1/ai/start-tagging?platform=${platformStore.platform}&model=${modelKey}&batch_size=${batchSize.value}`

  try {
    statusText.value = 'Запрос к нейросети...'
    const response = await apiFetch(endpoint, { method: 'POST' })

    if (response.ok) {
      const payload = await response.json().catch(() => ({}))
      statusText.value = payload.message || 'Работает на сервере. Страницу можно закрыть.'
      startPolling()
    } else {
      throw new Error('Ошибка сервера')
    }
  } catch (err) {
    stopPolling()
    clearJob()
    resultMessage.value = { type: 'error', text: 'Сбой соединения с сервером ИИ.' }
  }
}
</script>

<template>
  <div class="p-6 max-w-5xl mx-auto">
    <div class="flex items-center justify-between mb-8">
      <div class="flex items-center gap-3">
        <div class="p-3 bg-indigo-100 text-indigo-600 rounded-xl">
          <Cpu class="w-6 h-6" />
        </div>
        <h1 class="text-2xl font-bold text-slate-800">ИИ Тегирование (Претензии и Отзывы)</h1>
      </div>
      <button @click="fetchStats" :disabled="isStatsLoading" class="flex items-center gap-2 px-4 py-2 bg-white border border-slate-200 text-slate-600 font-semibold rounded-xl shadow-sm hover:bg-slate-50 transition-colors disabled:opacity-50">
        <RefreshCw :class="['w-4 h-4', isStatsLoading ? 'animate-spin' : '']" /> Обновить
      </button>
    </div>

    <div class="flex flex-wrap gap-2 mb-6 border-b border-slate-200 pb-px">
      <button @click="activeTab = 'tagging'" :class="['px-5 py-2.5 text-sm font-semibold rounded-t-lg transition-colors flex items-center gap-2', activeTab === 'tagging' ? 'bg-white text-indigo-600 border-t border-l border-r border-slate-200 shadow-[0_2px_0_0_white]' : 'text-slate-500 hover:bg-slate-100']">
        <Tag class="w-4 h-4" />
        {{ platformStore.platform === 'ym' ? 'Разметка брака (Возвраты ЯМ)' : platformStore.platform === 'ozon' ? 'Разметка брака (Возвраты Ozon)' : 'Разметка брака (Претензии)' }}
      </button>
      <button @click="activeTab = 'feedback'" :class="['px-5 py-2.5 text-sm font-semibold rounded-t-lg transition-colors flex items-center gap-2', activeTab === 'feedback' ? 'bg-white text-rose-600 border-t border-l border-r border-slate-200 shadow-[0_2px_0_0_white]' : 'text-slate-500 hover:bg-slate-100']">
        <MessageSquarePlus class="w-4 h-4" />
        {{ platformStore.platform === 'ym' ? 'Отзывы ЯМ: Поиск идей (VOC)' : platformStore.platform === 'ozon' ? 'Отзывы Ozon (недоступно)' : 'Отзывы: Поиск идей (VOC)' }}
      </button>
    </div>

    <div class="bg-white border border-slate-200 rounded-2xl shadow-sm p-6">
      <div class="grid grid-cols-1 md:grid-cols-2 gap-8 mb-8">
        <div class="space-y-6">
          <div>
            <label class="block text-sm font-bold text-slate-700 mb-2">Размер пачки (Batch size): {{ batchSize }} шт.</label>
            <input type="range" min="5" max="50" step="5" v-model="batchSize" class="w-full accent-indigo-600" :disabled="isProcessing">
          </div>
          <div>
            <label class="block text-sm font-bold text-slate-700 mb-2">Модель нейросети:</label>
            <div class="flex flex-col gap-2">
              <label v-for="model in models" :key="model.key" class="flex items-center gap-3 p-3 border rounded-lg cursor-pointer hover:bg-slate-50 transition-colors" :class="{'border-indigo-500 bg-indigo-50': selectedModel === model.key}">
                <input type="radio" :value="model.key" v-model="selectedModel" class="w-4 h-4 text-indigo-600 border-slate-300">
                <component :is="model.icon" class="w-4 h-4 text-slate-400 flex-shrink-0" />
                <div class="flex-1 min-w-0">
                  <span class="text-sm font-medium text-slate-700">{{ model.label }}</span>
                  <span class="ml-2 text-xs text-slate-400">{{ model.hint }}</span>
                </div>
                <span v-if="model.cost != null" class="text-xs font-semibold text-slate-500">{{ model.cost }} {{ model.currency }}/шт.</span>
              </label>
            </div>
          </div>
        </div>

        <div class="flex flex-col justify-between bg-slate-50 p-6 rounded-xl border border-slate-100">
          <div>
            <h3 class="font-bold text-slate-700 mb-3">Аналитика задачи</h3>
            <div v-if="isStatsLoading" class="text-sm text-slate-500 animate-pulse">Подсчет базы данных...</div>
            <div v-else>
              <div class="flex justify-between items-center mb-2">
                <span class="text-sm font-medium text-slate-600">
                  {{ activeTab === 'tagging' ? 'Без тегов (в очереди):' : 'Очередь отзывов:' }}
                </span>
                <span :class="['font-black text-lg', currentCount > 0 ? (activeTab==='feedback'?'text-rose-600':'text-indigo-600') : 'text-emerald-600']">{{ currentCount }} шт.</span>
              </div>

              <div v-if="activeTab === 'tagging'" class="text-[11px] text-slate-500 mb-3 leading-relaxed">
                <span v-if="platformStore.platform === 'ym'">
                  ИИ разберёт комментарии к возвратам и проставит категории дефектов (1–13) в таблицу ym_returns.
                </span>
                <span v-else-if="platformStore.platform === 'ozon'">
                  ИИ классифицирует причины возвратов Ozon FBO и проставит категории дефектов (1–13) в таблицу ozon_returns.
                </span>
                <span v-else>
                  ИИ проанализирует комментарии покупателей и проставит категории дефектов (1–13) по претензиям WB.
                </span>
              </div>

              <div v-if="activeTab === 'feedback'" class="flex justify-between items-center mb-2">
                <span class="text-sm font-medium text-slate-600">К отправке в ИИ (анализ болей):</span>
                <span class="font-bold text-slate-800">~{{ Math.round(currentCount * 0.25) }} шт.</span>
              </div>
              <p v-if="activeTab === 'feedback'" class="text-[11px] text-rose-500 mt-1 font-semibold leading-relaxed">
                После запуска работает на сервере до конца очереди — страницу можно закрыть. Короткие отзывы на 5★ закрываются сразу, без нейросети.
              </p>

              <div v-if="activeModel.cost != null" class="flex justify-between items-center pb-3 border-b border-slate-200 mt-2">
                <span class="text-sm font-medium text-slate-600">Ориентировочный расход:</span>
                <span class="font-bold text-slate-800">~{{ estimatedCost.value }} {{ estimatedCost.currency }}</span>
              </div>

              <p v-if="activeTab !== 'feedback'" class="text-[11px] text-slate-400 mt-3 leading-relaxed">
                Точная сумма зависит от количества токенов в комментариях клиентов. Фоновый процесс работает партиями.
              </p>
              <p v-else class="text-[11px] text-slate-500 mt-3 leading-relaxed">
                Короткие пятёрки закрываются бесплатно. Для отзывов берём модели с полным лимитом, Qwen Plus оставляем браку.
              </p>
            </div>
          </div>

          <button @click="startProcess"
            :disabled="isProcessing || currentCount === 0 || isStatsLoading"
            :class="['mt-6 w-full flex items-center justify-center gap-2 py-3 px-4 text-white font-bold rounded-xl transition-all shadow-sm disabled:opacity-50',
              activeTab === 'feedback' ? 'bg-rose-600 hover:bg-rose-700' : 'bg-indigo-600 hover:bg-indigo-700']">
            <Loader2 v-if="isProcessing" class="w-5 h-5 animate-spin" />
            <Play v-else class="w-5 h-5" />
            <span v-if="currentCount === 0 && !isStatsLoading">НЕТ ЗАДАЧ</span>
            <span v-else>{{ isProcessing ? 'Отправка...' : 'ЗАПУСТИТЬ АНАЛИЗ' }}</span>
          </button>
        </div>
      </div>

      <div v-if="isProcessing || resultMessage" class="mt-8 pt-6 border-t border-slate-100">
        <div v-if="isProcessing" class="space-y-2">
          <div class="flex justify-between text-sm font-medium text-slate-600">
            <span :class="['font-semibold animate-pulse', activeTab==='feedback'?'text-rose-600':'text-indigo-600']">{{ statusText }}</span>
            <span>{{ progress }}%</span>
          </div>
          <div class="w-full bg-slate-100 rounded-full h-2.5 overflow-hidden">
            <div :class="['h-2.5 rounded-full transition-all duration-500', activeTab==='feedback'?'bg-rose-600':'bg-indigo-600']" :style="{ width: `${progress}%` }"></div>
          </div>
          <p v-if="stats.last_log" class="text-xs text-slate-400 bg-slate-50 p-2 rounded-lg border border-slate-100 font-mono mt-1">Лог: {{ stats.last_log.details }}</p>
        </div>

        <div v-if="resultMessage" :class="['p-4 rounded-xl flex gap-3', resultMessage.type === 'success' ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-800']">
          <span class="text-lg">{{ resultMessage.type === 'success' ? '✅' : '❌' }}</span>
          <div>
            <h4 class="font-bold mb-1">{{ resultMessage.type === 'success' ? 'Процесс завершен' : 'Ошибка' }}</h4>
            <p class="text-sm">{{ resultMessage.text }}</p>
          </div>
        </div>
      </div>

    </div>
  </div>
</template>
