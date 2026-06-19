<script setup>
import { ref, computed, onMounted, watch, onBeforeUnmount } from 'vue'
import { Tag, ShieldCheck, Play, Loader2, Cpu, RefreshCw, MessageSquarePlus, Zap, Target } from 'lucide-vue-next'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'

const platformStore = usePlatformStore()

const activeTab = ref('tagging') // 'tagging', 'audit', 'feedback'

// Настройки
const batchSize = ref(10)
const selectedModel = ref('yandex-lite')
const models = [
  { key: 'yandex-lite',   label: 'YandexGPT Lite',         icon: Zap,    hint: 'Быстрый · дешевле', cost: 0.08, currency: '₽' },
  { key: 'yandex-pro',    label: 'YandexGPT Pro',          icon: Target, hint: 'Точный · качество',  cost: 0.40, currency: '₽' },
  { key: 'claude-haiku',  label: 'Claude Haiku (Быстрый)', icon: Zap,    hint: 'Быстрый · дешевле', cost: 0.05, currency: '$' },
  { key: 'claude-sonnet', label: 'Claude Sonnet (Точный)', icon: Target, hint: 'Точный · качество',  cost: 0.25, currency: '$' },
]

const activeModel = computed(() => models.find(m => m.key === selectedModel.value) || models[0])

const isProcessing = ref(false)
const progress = ref(0)
const statusText = ref('')
const resultMessage = ref(null)

const stats = ref({ untagged: 0, unaudited: 0, untaggedFeedbacks: 0 })
const isStatsLoading = ref(true)

let pollingInterval = null
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
      stats.value.unaudited = data.unaudited_count
      stats.value.untaggedFeedbacks = data.untagged_feedbacks
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
  const saved = localStorage.getItem(BG_JOB_KEY)
  if (saved) {
    const job = JSON.parse(saved)
    const age = Date.now() - job.startedAt
    if (age < 3_600_000 && job.platform === platformStore.platform) {
      initialCount.value = job.initialCount
      isProcessing.value = true
      activeTab.value = job.tab
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

watch(activeTab, () => {
  resultMessage.value = null
  stopPolling()
  clearJob()
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
  pollingInterval = setInterval(async () => {
    await fetchStats()
    const processed = initialCount.value - currentCount.value
    progress.value = Math.min(100, processed > 0 ? Math.floor((processed / initialCount.value) * 100) : 0)
    statusText.value = `Успешно обработано: ${processed} из ${initialCount.value} шт.`
    if (currentCount.value === 0 || progress.value >= 100) {
      stopPolling()
      clearJob()
      progress.value = 100
      statusText.value = 'Завершено!'
      resultMessage.value = { type: 'success', text: 'Все доступные данные были успешно обработаны ИИ.' }
    }
  }, 5000)
}

const currentCount = computed(() => {
  if (activeTab.value === 'tagging') return stats.value.untagged
  if (activeTab.value === 'audit') return stats.value.unaudited
  return stats.value.untaggedFeedbacks
})

const estimatedCost = computed(() => {
  const { cost, currency } = activeModel.value
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

  let endpoint = ''
  if (activeTab.value === 'tagging')
    endpoint = `/api/v1/ai/start-tagging?platform=${platformStore.platform}&model=${modelKey}&batch_size=${batchSize.value}`
  else if (activeTab.value === 'audit')
    endpoint = `/api/v1/ai/start-audit?model=${modelKey}&batch_size=${batchSize.value}`
  else
    endpoint = `/api/v1/ai/start-feedback-tagging?platform=${platformStore.platform}&model=${modelKey}&batch_size=${batchSize.value}`

  try {
    statusText.value = 'Запрос к нейросети...'
    const response = await apiFetch(endpoint, { method: 'POST' })

    if (response.ok) {
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
      <button @click="activeTab = 'audit'" :class="['px-5 py-2.5 text-sm font-semibold rounded-t-lg transition-colors flex items-center gap-2', activeTab === 'audit' ? 'bg-white text-indigo-600 border-t border-l border-r border-slate-200 shadow-[0_2px_0_0_white]' : 'text-slate-500 hover:bg-slate-100']">
        <ShieldCheck class="w-4 h-4" /> Аудит брака
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
                <span class="text-xs font-semibold text-slate-500">{{ model.cost }} {{ model.currency }}/шт.</span>
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
                  {{ activeTab === 'tagging' ? 'Без тегов (в очереди):' : activeTab === 'audit' ? 'Ждут ручного аудита:' : 'Очередь отзывов:' }}
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
              <div v-if="activeTab === 'audit'" class="text-[11px] text-slate-500 mb-3 leading-relaxed">
                Записи прошли автотегирование. Перейдите в раздел <b>Модерация тегов</b>, чтобы проверить и подтвердить результат вручную.
              </div>

              <div v-if="activeTab === 'feedback'" class="flex justify-between items-center mb-2">
                <span class="text-sm font-medium text-slate-600">К отправке в ИИ (анализ болей):</span>
                <span class="font-bold text-slate-800">~{{ Math.round(currentCount * 0.25) }} шт.</span>
              </div>
              <p v-if="activeTab === 'feedback'" class="text-[11px] text-rose-500 mt-1 font-semibold leading-relaxed">
                * Локальный скрипт отбрасывает пустые и короткие отзывы на 5 звезд (~75%). Нейросеть проанализирует все отзывы 1-4 звезды и развернутые комментарии.
              </p>

              <div class="flex justify-between items-center pb-3 border-b border-slate-200 mt-2">
                <span class="text-sm font-medium text-slate-600">Ориентировочный расход:</span>
                <span class="font-bold text-slate-800">~{{ estimatedCost.value }} {{ estimatedCost.currency }}</span>
              </div>

              <p v-if="activeTab === 'audit'" class="text-[11px] text-slate-400 mt-3 leading-relaxed">
                Запуск в этой вкладке не требуется — аудит выполняется вручную в разделе Модерации.
              </p>
              <p v-else-if="activeTab !== 'feedback'" class="text-[11px] text-slate-400 mt-3 leading-relaxed">
                Точная сумма зависит от количества токенов в комментариях клиентов. Фоновый процесс работает партиями.
              </p>
              <p v-else class="text-[11px] text-rose-500 mt-3 font-semibold leading-relaxed">
                * Основной массив (~95%) обрабатывается бесплатным алгоритмом. Платная нейросеть анализирует только сложные тексты.
              </p>
            </div>
          </div>

          <button @click="startProcess"
            :disabled="isProcessing || currentCount === 0 || isStatsLoading || activeTab === 'audit'"
            :class="['mt-6 w-full flex items-center justify-center gap-2 py-3 px-4 text-white font-bold rounded-xl transition-all shadow-sm disabled:opacity-50',
              activeTab === 'feedback' ? 'bg-rose-600 hover:bg-rose-700' : 'bg-indigo-600 hover:bg-indigo-700']">
            <Loader2 v-if="isProcessing" class="w-5 h-5 animate-spin" />
            <Play v-else class="w-5 h-5" />
            <span v-if="activeTab === 'audit'">ПЕРЕЙТИ В МОДЕРАЦИЮ</span>
            <span v-else-if="currentCount === 0 && !isStatsLoading">НЕТ ЗАДАЧ</span>
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
