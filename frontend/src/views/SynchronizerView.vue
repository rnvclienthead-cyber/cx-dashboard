<script setup>
import { ref, onMounted, watch } from 'vue'
import { Activity, Clock, Database, AlertCircle, CheckCircle2, ListFilter } from 'lucide-vue-next'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'

const syncData = ref(null)
const loading = ref(true)
const platformStore = usePlatformStore()

const fetchStatus = async () => {
  loading.value = true
  try {
    const res = await apiFetch(`/api/v1/system/sync-status?platform=${platformStore.platform}`)
    if (res.ok) {
      const data = await res.json()
      syncData.value = data.metrics ? data : null
    } else {
      syncData.value = null
    }
  } catch (err) {
    console.error("Ошибка загрузки метрик", err)
    syncData.value = null
  } finally {
    loading.value = false
  }
}

watch(() => platformStore.platform, () => fetchStatus())
onMounted(fetchStatus)
</script>

<template>
  <div class="p-6 max-w-6xl mx-auto">
    <div class="flex items-center gap-3 mb-6">
      <div class="p-3 bg-blue-100 text-blue-600 rounded-xl">
        <Activity class="w-6 h-6" />
      </div>
      <h1 class="text-2xl font-bold text-slate-800">Статус автоматизации</h1>
    </div>

    <div v-if="loading" class="text-slate-500 animate-pulse flex items-center gap-2">
      <Clock class="w-5 h-5 animate-spin" /> Обновление данных...
    </div>

    <div v-else-if="!syncData" class="text-center py-24 text-slate-400">
      <Database class="w-12 h-12 mx-auto mb-4 opacity-30" />
      <p class="font-semibold">Нет данных для выбранной платформы</p>
    </div>

    <div v-else class="space-y-6">
      
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm flex flex-col justify-between">
          <div>
            <div class="flex items-center gap-2 text-sm font-bold text-slate-500 uppercase tracking-wide mb-4">
              <Database class="w-4 h-4" /> Единый Синхронизатор
            </div>
            <div class="flex items-center gap-3 mb-2">
              <span class="relative flex h-4 w-4">
                <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span class="relative inline-flex rounded-full h-4 w-4 bg-emerald-500"></span>
              </span>
              <span class="text-xl font-black text-slate-800">Активен</span>
            </div>
          </div>
          <div class="mt-4 pt-4 border-t border-slate-100 text-sm text-slate-600">
            Последний запуск: <span class="font-bold">{{ syncData?.last_sync }}</span>
          </div>
        </div>

        <div class="lg:col-span-2 bg-slate-900 rounded-2xl p-4 shadow-sm border border-slate-800 flex flex-col">
          <div class="flex items-center gap-2 text-slate-400 text-xs font-bold uppercase tracking-wider mb-3 px-2">
            <ListFilter class="w-4 h-4" /> Журнал событий
          </div>
          <div class="flex-1 overflow-y-auto max-h-32 space-y-2 pr-2 custom-scrollbar">
            <div v-for="log in syncData?.logs" :key="log.id" class="flex gap-3 text-sm font-mono items-start">
              <span class="text-slate-500 shrink-0">[{{ log.time }}]</span>
              <span v-if="log.type === 'success'" class="text-emerald-400 flex items-center gap-1"><CheckCircle2 class="w-4 h-4"/></span>
              <span v-if="log.type === 'info'" class="text-blue-400 flex items-center gap-1"><Activity class="w-4 h-4"/></span>
              <span v-if="log.type === 'warning'" class="text-amber-400 flex items-center gap-1"><AlertCircle class="w-4 h-4"/></span>
              <span class="text-slate-300">{{ log.text }}</span>
            </div>
            <div v-if="!syncData?.logs?.length" class="text-slate-600 text-sm font-mono px-2">
              Логи пока пусты...
            </div>
          </div>
        </div>
      </div>

      <div class="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">
        <div class="p-5 border-b border-slate-100 bg-slate-50">
          <h2 class="text-lg font-bold text-slate-800">Объем данных в системе</h2>
        </div>
        
        <div class="grid grid-cols-1 sm:grid-cols-3 font-bold text-slate-400 text-xs uppercase tracking-wider p-4 border-b border-slate-100 bg-white">
          <div class="sm:col-span-1">Раздел данных</div>
          <div class="text-right">Добавлено вчера</div>
          <div class="text-right">Всего в базе</div>
        </div>

        <div class="divide-y divide-slate-100 bg-white">
          <div v-for="item in syncData?.metrics" :key="item.id" class="grid grid-cols-1 sm:grid-cols-3 p-4 items-center hover:bg-slate-50 transition-colors">
            <div class="font-medium text-slate-700 flex items-center gap-2 sm:col-span-1">
              {{ item.name }}
            </div>
            <div class="text-right font-bold text-sm">
              <span v-if="(item.yesterday ?? item.today) > 0" class="text-emerald-500 bg-emerald-50 px-2 py-1 rounded-md">
                +{{ (item.yesterday ?? item.today).toLocaleString() }}
              </span>
              <span v-else class="text-slate-400">—</span>
            </div>
            <div :class="['text-right font-black text-lg', item.color || 'text-slate-800']">
              {{ item.total.toLocaleString() }}
            </div>
          </div>
        </div>
      </div>

    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 6px; }
.custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
.custom-scrollbar::-webkit-scrollbar-thumb { background-color: #334155; border-radius: 10px; }
</style>