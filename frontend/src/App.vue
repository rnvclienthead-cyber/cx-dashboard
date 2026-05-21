<script setup>
import { RouterLink, RouterView } from 'vue-router'
import { 
  Database, Bot, ClipboardCheck, BrainCircuit, ScrollText, 
  LineChart, AlertTriangle, Star 
} from 'lucide-vue-next'
</script>

<template>
  <div class="flex h-screen bg-slate-50">
    <aside class="w-64 bg-white border-r border-slate-200 flex flex-col shadow-sm z-10">
      <div class="p-5 border-b border-slate-200">
        <h2 class="text-xl font-black text-slate-800 tracking-tight">CX Видовито</h2>
      </div>
      
      <nav class="flex-1 p-4 space-y-1 overflow-y-auto">
        <div class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3 mt-2 px-2">
          Технический блок
        </div>
        
        <RouterLink to="/" class="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors" active-class="bg-blue-50 text-blue-700 font-semibold">
          <Database class="w-4 h-4" /> Синхронизатор
        </RouterLink>
        <RouterLink to="/ai-tagging" class="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors" active-class="bg-blue-50 text-blue-700 font-semibold">
          <Bot class="w-4 h-4" /> ИИ Тегирование
        </RouterLink>
        <RouterLink to="/moderation" class="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors" active-class="bg-blue-50 text-blue-700 font-semibold">
          <ClipboardCheck class="w-4 h-4" /> Модерация
        </RouterLink>
        <RouterLink to="/ai-training" class="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors" active-class="bg-blue-50 text-blue-700 font-semibold">
          <BrainCircuit class="w-4 h-4" /> Обучение ИИ
        </RouterLink>
        <RouterLink to="/logs" class="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors" active-class="bg-blue-50 text-blue-700 font-semibold">
          <ScrollText class="w-4 h-4" /> Системный Журнал
        </RouterLink>

        <div class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3 mt-8 px-2">
          Операционный блок
        </div>

        <RouterLink to="/production" class="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors" active-class="bg-emerald-50 text-emerald-700 font-semibold">
          <LineChart class="w-4 h-4" /> Отчет производства
        </RouterLink>
        <RouterLink to="/ppm" class="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors" active-class="bg-emerald-50 text-emerald-700 font-semibold">
          <AlertTriangle class="w-4 h-4" /> Уровень PPM
        </RouterLink>
        <RouterLink to="/ratings" class="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors" active-class="bg-emerald-50 text-emerald-700 font-semibold">
          <Star class="w-4 h-4" /> Рейтинг товаров
        </RouterLink>
      </nav>
    </aside>

    <main class="flex-1 overflow-y-auto">
      <RouterView />
    </main>
  </div>
</template>