<script setup>
import { ref, computed } from 'vue'
import { Tag, ShieldCheck, Play, Loader2, Cpu } from 'lucide-vue-next'

const activeTab = ref('tagging') // 'tagging' или 'audit'

// Настройки
const batchSize = ref(10)
const selectedModel = ref('YandexGPT Lite')
const models = ['YandexGPT Lite', 'YandexGPT Pro', 'Grok (xAI)']

// Состояния процесса
const isProcessing = ref(false)
const progress = ref(0)
const statusText = ref('')
const resultMessage = ref(null)

// Имитация расчетной стоимости (как было в Стримлите)
const estimatedCost = computed(() => {
  const baseCost = selectedModel.value.includes('Lite') ? 0.08 : selectedModel.value.includes('Pro') ? 0.40 : 0.50
  return (150 * baseCost).toFixed(2) // 150 - примерное кол-во неразмеченных (потом будем тянуть с бэка)
})

const startProcess = async () => {
  isProcessing.value = true
  progress.value = 0
  resultMessage.value = null
  statusText.value = 'Подготовка данных и прогрев нейросетей...'

  const endpoint = activeTab.value === 'tagging' 
    ? 'http://127.0.0.1:8001/api/v1/ai/start-tagging' 
    : 'http://127.0.0.1:8001/api/v1/ai/start-audit'

  // Формируем payload для бэкенда
  const payload = {
    batch_size: batchSize.value,
    model: selectedModel.value
  }

  try {
    // ВАЖНО: В реальности этот запрос может идти долго. 
    // Пока мы делаем простой запрос, позже можно будет прикрутить WebSocket для ползунка прогресса.
    statusText.value = `Отправка пачек по ${batchSize.value} шт. в ${selectedModel.value}...`
    
    // Имитация прогресс-бара для визуала (пока ждем ответ сервера)
    const interval = setInterval(() => {
      if (progress.value < 90) progress.value += 5
    }, 1000)

    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })

    clearInterval(interval)
    progress.value = 100

    if (response.ok) {
      const data = await response.json()
      statusText.value = 'Завершено!'
      resultMessage.value = { type: 'success', text: data.message || 'Процесс успешно завершен.' }
    } else {
      throw new Error('Ошибка сервера')
    }
  } catch (err) {
    progress.value = 0
    statusText.value = 'Сбой процесса'
    resultMessage.value = { type: 'error', text: 'Ошибка соединения с сервером ИИ. Проверьте логи бэкенда.' }
  } finally {
    isProcessing.value = false
  }
}
</script>

<template>
  <div class="p-6 max-w-5xl mx-auto">
    <div class="flex items-center gap-3 mb-8">
      <div class="p-3 bg-indigo-100 text-indigo-600 rounded-xl">
        <Cpu class="w-6 h-6" />
      </div>
      <h1 class="text-2xl font-bold text-slate-800">ИИ Тегирование и Проверка</h1>
    </div>

    <div class="flex gap-2 mb-6 border-b border-slate-200 pb-px">
      <button 
        @click="activeTab = 'tagging'"
        :class="['px-5 py-2.5 text-sm font-semibold rounded-t-lg transition-colors flex items-center gap-2', 
                 activeTab === 'tagging' ? 'bg-white text-indigo-600 border-t border-l border-r border-slate-200 shadow-[0_2px_0_0_white]' : 'text-slate-500 hover:bg-slate-100']"
      >
        <Tag class="w-4 h-4" /> Первичная разметка
      </button>
      <button 
        @click="activeTab = 'audit'"
        :class="['px-5 py-2.5 text-sm font-semibold rounded-t-lg transition-colors flex items-center gap-2', 
                 activeTab === 'audit' ? 'bg-white text-indigo-600 border-t border-l border-r border-slate-200 shadow-[0_2px_0_0_white]' : 'text-slate-500 hover:bg-slate-100']"
      >
        <ShieldCheck class="w-4 h-4" /> Перекрестный аудит
      </button>
    </div>

    <div class="bg-white border border-slate-200 rounded-2xl shadow-sm p-6">
      
      <div class="grid grid-cols-1 md:grid-cols-2 gap-8 mb-8">
        <div class="space-y-6">
          <div>
            <label class="block text-sm font-bold text-slate-700 mb-2">Размер пачки (Batch size): {{ batchSize }} шт.</label>
            <input type="range" min="5" max="50" step="5" v-model="batchSize" class="w-full accent-indigo-600" :disabled="isProcessing">
            <p class="text-xs text-slate-500 mt-1">Чем больше пачка, тем быстрее, но выше риск сбоя API.</p>
          </div>
          
          <div>
            <label class="block text-sm font-bold text-slate-700 mb-2">Модель нейросети:</label>
            <div class="flex flex-col gap-2">
              <label v-for="model in models" :key="model" class="flex items-center gap-3 p-3 border rounded-lg cursor-pointer hover:bg-slate-50 transition-colors" :class="{'border-indigo-500 bg-indigo-50': selectedModel === model}">
                <input type="radio" :value="model" v-model="selectedModel" class="w-4 h-4 text-indigo-600 border-slate-300 focus:ring-indigo-500" :disabled="isProcessing">
                <span class="text-sm font-medium text-slate-700">{{ model }}</span>
              </label>
            </div>
          </div>
        </div>

        <div class="flex flex-col justify-between bg-slate-50 p-6 rounded-xl border border-slate-100">
          <div>
            <h3 class="font-bold text-slate-700 mb-2">Аналитика задачи</h3>
            <p class="text-sm text-slate-600 mb-1">Ориентировочный расход: <span class="font-bold text-slate-800">~{{ estimatedCost }} руб.</span></p>
            <p class="text-xs text-slate-500">Точная сумма зависит от количества токенов в комментариях клиентов.</p>
          </div>

          <button 
            @click="startProcess" 
            :disabled="isProcessing"
            class="mt-6 w-full flex items-center justify-center gap-2 py-3 px-4 bg-indigo-600 hover:bg-indigo-700 text-white font-bold rounded-xl transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Loader2 v-if="isProcessing" class="w-5 h-5 animate-spin" />
            <Play v-else class="w-5 h-5" />
            {{ isProcessing ? 'Процесс запущен...' : (activeTab === 'tagging' ? 'ЗАПУСТИТЬ ТЕГИРОВАНИЕ' : 'ЗАПУСТИТЬ АУДИТ') }}
          </button>
        </div>
      </div>

      <div v-if="isProcessing || resultMessage" class="mt-8 pt-6 border-t border-slate-100">
        <div v-if="isProcessing" class="space-y-2">
          <div class="flex justify-between text-sm font-medium text-slate-600">
            <span>{{ statusText }}</span>
            <span>{{ progress }}%</span>
          </div>
          <div class="w-full bg-slate-100 rounded-full h-2.5 overflow-hidden">
            <div class="bg-indigo-600 h-2.5 rounded-full transition-all duration-500 ease-out" :style="{ width: `${progress}%` }"></div>
          </div>
        </div>

        <div v-if="resultMessage" :class="['p-4 rounded-xl flex items-start gap-3', resultMessage.type === 'success' ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-800']">
          <span class="text-lg">{{ resultMessage.type === 'success' ? '✅' : '❌' }}</span>
          <div>
            <h4 class="font-bold mb-1">{{ resultMessage.type === 'success' ? 'Успешно!' : 'Ошибка' }}</h4>
            <p class="text-sm">{{ resultMessage.text }}</p>
          </div>
        </div>
      </div>

    </div>
  </div>
</template>