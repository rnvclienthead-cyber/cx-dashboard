<script setup>
import { ref, onMounted } from 'vue'

const metrics = ref(null)
const loading = ref(true)

const fetchStatus = async () => {
  try {
    const res = await fetch('http://127.0.0.1:8001/api/v1/system/sync-status')
    if (res.ok) {
      metrics.value = await res.json()
    }
  } catch (err) {
    console.error("Ошибка загрузки метрик", err)
  } finally {
    loading.value = false
  }
}

onMounted(fetchStatus)
</script>

<template>
  <div class="p-6 max-w-6xl mx-auto">
    <h1 class="text-2xl font-bold text-slate-800 mb-6 flex items-center gap-2">
      <span class="text-blue-600">📊</span> Статус синхронизаторов
    </h1>

    <div v-if="loading" class="text-slate-500 animate-pulse">Обновление данных...</div>

    <div v-else>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
        <div class="bg-emerald-50 border border-emerald-200 rounded-xl p-5 flex flex-col justify-center">
          <div class="text-sm font-bold text-emerald-800 uppercase tracking-wide mb-1">Главный (Логистика/Возвраты)</div>
          <div class="text-emerald-600 font-medium">Последняя синхронизация: {{ metrics?.main_sync || 'Только что' }}</div>
        </div>
        <div class="bg-blue-50 border border-blue-200 rounded-xl p-5 flex flex-col justify-center">
          <div class="text-sm font-bold text-blue-800 uppercase tracking-wide mb-1">Синхронизатор рейтингов</div>
          <div class="text-blue-600 font-medium">Последняя синхронизация: {{ metrics?.rating_sync || 'Только что' }}</div>
        </div>
      </div>

      <h2 class="text-lg font-bold text-slate-700 mb-4">Общие данные в базе</h2>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <div class="bg-white border border-slate-200 p-4 rounded-xl shadow-sm">
          <div class="text-xs text-slate-500 font-semibold uppercase mb-1">Общее кол-во возвратов</div>
          <div class="text-2xl font-black text-slate-800">{{ metrics?.claims_count || '12 543' }}</div>
        </div>
        <div class="bg-white border border-slate-200 p-4 rounded-xl shadow-sm">
          <div class="text-xs text-slate-500 font-semibold uppercase mb-1">Одобренных возвратов</div>
          <div class="text-2xl font-black text-slate-800 text-emerald-600">{{ metrics?.approved_count || '1 850' }}</div>
        </div>
        <div class="bg-white border border-slate-200 p-4 rounded-xl shadow-sm">
          <div class="text-xs text-slate-500 font-semibold uppercase mb-1">Отказов</div>
          <div class="text-2xl font-black text-slate-800 text-red-500">{{ metrics?.rejected_count || '10 693' }}</div>
        </div>
        <div class="bg-white border border-slate-200 p-4 rounded-xl shadow-sm">
          <div class="text-xs text-slate-500 font-semibold uppercase mb-1">Инвойсы (Поставки)</div>
          <div class="text-2xl font-black text-slate-800">{{ metrics?.invoices_count || '842' }}</div>
        </div>
      </div>
      
      <div class="bg-slate-100 p-4 rounded-lg text-center text-sm text-slate-500 border border-slate-200">
        MVP-версия. Если FastAPI еще не отдает полные метрики на <code>/sync-status</code>, здесь отображаются демо-данные.
      </div>
    </div>
  </div>
</template>