<script setup>
import { ref, onMounted } from 'vue'
import { Activity, Server, HardDrive, Cpu, RefreshCw, ScrollText } from 'lucide-vue-next'
import { apiFetch } from '../api'

const sysMetrics = ref(null)
const logs = ref([])
const loading = ref(true)

const fetchData = async () => {
  loading.value = true
  try {
    const [resMetrics, resLogs] = await Promise.all([
      apiFetch('/api/v1/system/monitor').catch(() => null),
      apiFetch('/api/v1/system/logs').catch(() => null)
    ])
    
    if (resMetrics && resMetrics.ok) sysMetrics.value = await resMetrics.json()
    if (resLogs && resLogs.ok) {
      const data = await resLogs.json()
      logs.value = data.data || [] // Бэкенд отдает { count: X, data: [...] }
    }
  } catch (err) {
    console.error("Ошибка загрузки логов:", err)
  } finally {
    loading.value = false
  }
}

const getStatusStyle = (status) => {
  const s = String(status || '').toUpperCase()
  if (s.includes('ERR') || s.includes('FAIL') || s.includes('ОШИБ')) return 'bg-red-50 text-red-700 border-red-200'
  if (s.includes('WARN')) return 'bg-amber-50 text-amber-700 border-amber-200'
  if (s.includes('INFO')) return 'bg-blue-50 text-blue-700 border-blue-200'
  return 'bg-emerald-50 text-emerald-700 border-emerald-200'
}

onMounted(fetchData)
</script>

<template>
  <div class="p-6 max-w-6xl mx-auto">
    <div class="flex justify-between items-end mb-8">
      <div class="flex items-center gap-3">
        <div class="p-3 bg-slate-800 text-white rounded-xl">
          <Activity class="w-6 h-6" />
        </div>
        <h1 class="text-2xl font-bold text-slate-800">Системный Журнал</h1>
      </div>
      <button @click="fetchData" class="flex items-center gap-2 px-4 py-2 bg-white border border-slate-200 text-slate-600 rounded-lg hover:bg-slate-50 transition-colors shadow-sm text-sm font-medium">
        <RefreshCw class="w-4 h-4" :class="{'animate-spin': loading}" /> Обновить
      </button>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
      <div class="bg-white border border-slate-200 p-5 rounded-xl shadow-sm flex items-center gap-4">
        <div class="p-3 bg-blue-50 text-blue-600 rounded-full"><Cpu class="w-6 h-6"/></div>
        <div>
          <div class="text-xs font-bold text-slate-400 uppercase tracking-wider">Нагрузка CPU</div>
          <div class="text-xl font-black text-slate-800">{{ sysMetrics?.cpu?.percent || '0' }}%</div>
        </div>
      </div>
      <div class="bg-white border border-slate-200 p-5 rounded-xl shadow-sm flex items-center gap-4">
        <div class="p-3 bg-purple-50 text-purple-600 rounded-full"><Server class="w-6 h-6"/></div>
        <div>
          <div class="text-xs font-bold text-slate-400 uppercase tracking-wider">RAM (Использовано)</div>
          <div class="text-xl font-black text-slate-800">{{ sysMetrics?.ram?.used_gb || '0' }} GB <span class="text-sm text-slate-400 font-medium">/ {{ sysMetrics?.ram?.total_gb || '0' }} GB</span></div>
        </div>
      </div>
      <div class="bg-white border border-slate-200 p-5 rounded-xl shadow-sm flex items-center gap-4">
        <div class="p-3 bg-emerald-50 text-emerald-600 rounded-full"><HardDrive class="w-6 h-6"/></div>
        <div>
          <div class="text-xs font-bold text-slate-400 uppercase tracking-wider">Диск (Свободно)</div>
          <div class="text-xl font-black text-slate-800">{{ sysMetrics?.disk?.free_gb || '0' }} GB</div>
        </div>
      </div>
    </div>

    <div class="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">
      <div class="p-4 bg-slate-50 border-b border-slate-200 flex items-center gap-2">
        <ScrollText class="w-5 h-5 text-slate-500" />
        <h3 class="font-bold text-slate-700">Журнал событий сервера (Воркер, ИИ, API)</h3>
      </div>
      
      <div class="overflow-x-auto max-h-[600px] overflow-y-auto custom-scrollbar">
        <table class="w-full text-left border-collapse relative">
          <thead class="sticky top-0 bg-slate-50 shadow-sm z-10">
            <tr class="text-slate-500 text-xs uppercase tracking-wider">
              <th class="p-4 font-semibold w-48">Дата и время</th>
              <th class="p-4 font-semibold w-48">Процесс / Действие</th>
              <th class="p-4 font-semibold w-32">Статус</th>
              <th class="p-4 font-semibold">Детали</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-100 text-sm bg-white">
            <tr v-for="(log, idx) in logs" :key="idx" class="hover:bg-slate-50 transition-colors">
              <td class="p-4 text-slate-500 whitespace-nowrap">{{ log.date }}</td>
              <td class="p-4 font-bold text-slate-700">{{ log.action }}</td>
              <td class="p-4">
                <span :class="['px-2.5 py-1 border rounded-md text-xs font-bold tracking-wide', getStatusStyle(log.status)]">
                  {{ log.status }}
                </span>
              </td>
              <td class="p-4 text-slate-600 whitespace-pre-wrap font-mono text-xs">{{ log.details }}</td>
            </tr>
            <tr v-if="!loading && logs.length === 0">
              <td colspan="4" class="p-8 text-center text-slate-400">Журнал пуст. Записи появятся после работы алгоритмов.</td>
            </tr>
            <tr v-if="loading">
              <td colspan="4" class="p-8 text-center text-slate-400 animate-pulse">Загрузка логов из базы данных...</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 6px; }
.custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
.custom-scrollbar::-webkit-scrollbar-thumb { background-color: #cbd5e1; border-radius: 10px; }
</style>