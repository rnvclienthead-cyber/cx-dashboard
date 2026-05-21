<script setup>
import { ref, onMounted } from 'vue'
import { BrainCircuit, DatabaseZap, Search, BookOpen } from 'lucide-vue-next'

const knowledgeBase = ref([])
const loading = ref(true)
const searchQuery = ref('')

// Функция загрузки (если ты потом добавишь метод в FastAPI)
const fetchKnowledge = async () => {
  try {
    // Пока метода нет, делаем заглушку, чтобы интерфейс работал
    const res = await fetch('http://127.0.0.1:8001/api/v1/ai/knowledge').catch(() => null)
    if (res && res.ok) {
      const data = await res.json()
      knowledgeBase.value = data
    } else {
      // Демо-данные для визуала, пока бэк не отдает эту таблицу
      knowledgeBase.value = [
        { id: 1, content: "Не привезли болты для сборки стула", tags: "Некомплект: Фурнитура", source: "manual", date: "2026-05-18" },
        { id: 2, content: "Царапина на столешнице", tags: "Механические повреждения", source: "manual", date: "2026-05-19" },
        { id: 3, content: "Инструкция вообще от другой модели", tags: "Инструкция и сборка", source: "manual", date: "2026-05-20" }
      ]
    }
  } finally {
    loading.value = false
  }
}

onMounted(fetchKnowledge)
</script>

<template>
  <div class="p-6 max-w-6xl mx-auto">
    <div class="flex items-center gap-3 mb-8">
      <div class="p-3 bg-fuchsia-100 text-fuchsia-600 rounded-xl">
        <BrainCircuit class="w-6 h-6" />
      </div>
      <div>
        <h1 class="text-2xl font-bold text-slate-800">База знаний нейросетей</h1>
        <p class="text-sm text-slate-500">Алгоритмы самообучения на основе решений аудитора</p>
      </div>
    </div>

    <div class="bg-gradient-to-r from-fuchsia-600 to-indigo-600 rounded-2xl p-6 text-white mb-8 shadow-md">
      <h2 class="text-lg font-bold mb-2 flex items-center gap-2"><DatabaseZap class="w-5 h-5"/> Как это работает?</h2>
      <p class="text-fuchsia-100 text-sm leading-relaxed max-w-3xl">
        Каждый раз, когда вы исправляете ошибку ИИ в блоке <b>«Модерация»</b>, система автоматически извлекает текст клиента и ваши правильные теги. Эти данные записываются в SQL-таблицу <code class="bg-black/20 px-2 py-0.5 rounded">ai_knowledge_base</code>. При следующей разметке нейросеть будет опираться на этот опыт через механизм pg_trgm.
      </p>
    </div>

    <div class="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">
      <div class="p-5 border-b border-slate-200 flex justify-between items-center bg-slate-50">
        <h3 class="font-bold text-slate-700 flex items-center gap-2"><BookOpen class="w-5 h-5 text-slate-400"/> Накопленный опыт</h3>
        <div class="relative">
          <Search class="w-4 h-4 text-slate-400 absolute left-3 top-2.5" />
          <input type="text" v-model="searchQuery" placeholder="Поиск по опыту..." class="pl-9 pr-4 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-fuchsia-500 outline-none w-64" />
        </div>
      </div>
      
      <div class="p-0 overflow-x-auto">
        <table class="w-full text-left border-collapse">
          <thead>
            <tr class="bg-slate-50 text-slate-500 text-xs uppercase tracking-wider">
              <th class="p-4 font-semibold">Текст клиента (Ситуация)</th>
              <th class="p-4 font-semibold">Правильные теги</th>
              <th class="p-4 font-semibold">Источник</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-100 text-sm">
            <tr v-for="item in knowledgeBase" :key="item.id" class="hover:bg-slate-50 transition-colors">
              <td class="p-4 text-slate-700">{{ item.content }}</td>
              <td class="p-4"><span class="px-2.5 py-1 bg-fuchsia-50 text-fuchsia-700 rounded-md font-medium border border-fuchsia-100">{{ item.tags }}</span></td>
              <td class="p-4 text-slate-400 text-xs">{{ item.source === 'manual' ? '🧑‍💻 Ручная правка' : item.source }}</td>
            </tr>
            <tr v-if="knowledgeBase.length === 0">
              <td colspan="3" class="p-8 text-center text-slate-400">База знаний пока пуста.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>