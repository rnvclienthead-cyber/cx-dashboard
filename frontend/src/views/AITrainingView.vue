<script setup>
import { ref, computed, onMounted } from 'vue'
import { BrainCircuit, DatabaseZap, Search, BookOpen, Plus, Trash2, X, CheckCircle2, UploadCloud, MessageSquarePlus } from 'lucide-vue-next'
import { apiFetch } from '../api'

const CATEGORIES = {
  1: "Некомплект: Фурнитура", 2: "Некомплект: Несущие детали", 3: "Состояние упаковки",
  4: "Производственный дефект", 5: "Механические повреждения", 6: "Инструкция и сборка",
  7: "Хлипкость / Устойчивость", 8: "Пересорт / Ошибка склада", 9: "Качество материалов",
  10: "Габариты и Размер", 11: "Несоответствие описанию", 12: "Субъективное 'Не подошло'",
  13: "Следы использования / Б/У"
}

const activeTab = ref('claims') // 'claims' или 'feedbacks'

const knowledgeBase = ref([])
const fbKnowledgeBase = ref([])
const loading = ref(true)
const searchQuery = ref('')

const showAddModal = ref(false)
const newRuleText = ref('')
const newRuleCategories = ref([])
const isAdding = ref(false)

// Для загрузки файла
const isUploading = ref(false)
const uploadMessage = ref('')

const fetchData = async () => {
  loading.value = true
  try {
    const res1 = await apiFetch('/api/v1/ai/knowledge')
    if (res1.ok) {
      const json = await res1.json()
      knowledgeBase.value = json.data || []
    }
    const res2 = await apiFetch('/api/v1/ai/knowledge/feedback')
    if (res2.ok) {
      const json = await res2.json()
      fbKnowledgeBase.value = json.data || []
    }
  } catch (e) {
    console.error("Ошибка загрузки:", e)
  } finally {
    loading.value = false
  }
}

const filteredClaims = computed(() => {
  if (!searchQuery.value) return knowledgeBase.value
  const q = searchQuery.value.toLowerCase()
  return knowledgeBase.value.filter(item => (item.content && item.content.toLowerCase().includes(q)) || (item.tags && item.tags.toLowerCase().includes(q)))
})

const filteredFeedbacks = computed(() => {
  if (!searchQuery.value) return fbKnowledgeBase.value
  const q = searchQuery.value.toLowerCase()
  return fbKnowledgeBase.value.filter(item => item.content && item.content.toLowerCase().includes(q))
})

const deleteEntry = async (id, type) => {
  if (!confirm("Удалить этот пример?")) return
  try {
    const url = type === 'claims' ? `/api/v1/ai/knowledge/${id}` : `/api/v1/ai/knowledge/feedback/${id}`
    const res = await apiFetch(url, { method: 'DELETE' })
    if (res.ok) await fetchData()
  } catch (e) { console.error(e) }
}

const toggleCategory = (id) => {
  const idx = newRuleCategories.value.indexOf(id)
  if (idx > -1) newRuleCategories.value.splice(idx, 1)
  else newRuleCategories.value.push(id)
}

const addEntry = async () => {
  if (!newRuleText.value.trim() || newRuleCategories.value.length === 0) return alert("Заполните все поля!")
  isAdding.value = true
  try {
    const tagsString = newRuleCategories.value.map(id => CATEGORIES[id]).join('; ')
    const res = await apiFetch('/api/v1/ai/knowledge', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: newRuleText.value.trim(), tags: tagsString })
    })
    if (res.ok) { await fetchData(); showAddModal.value = false }
  } finally { isAdding.value = false }
}

// ПАРСИНГ ФАЙЛА С ОТЗЫВАМИ
const handleFileUpload = (event) => {
  const file = event.target.files[0]
  if (!file) return

  isUploading.value = true
  uploadMessage.value = 'Чтение файла...'

  const reader = new FileReader()
  reader.onload = async (e) => {
    try {
      const text = e.target.result
      const lines = text.split('\n')
      const payload = []

      // Парсим 3 колонки: Текст; Теги; Идея
      lines.forEach((line, index) => {
        if (!line.trim() || index === 0) return // Пропускаем пустые и заголовок
        
        const parts = line.split(';') 
        if (parts.length >= 2) {
            // Очищаем кавычки
            const content = parts[0]?.trim().replace(/^"|"$/g, '') || ''
            const tags = parts[1]?.trim().replace(/^"|"$/g, '') || ''
            const suggestion = parts[2]?.trim().replace(/^"|"$/g, '') || ''
            
            if (content) {
                payload.push({ content, tags, suggestion })
            }
        }
      })

      uploadMessage.value = `Найдено ${payload.length} строк. Отправка в базу...`

      const res = await apiFetch('/api/v1/ai/knowledge/feedback/bulk', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: payload })
      })

      if (res.ok) {
        uploadMessage.value = 'Успешно загружено!'
        await fetchData()
      } else {
        uploadMessage.value = 'Ошибка сервера при загрузке.'
      }
    } catch (err) {
      uploadMessage.value = 'Ошибка формата файла.'
    } finally {
      setTimeout(() => { isUploading.value = false; uploadMessage.value = '' }, 3000)
      event.target.value = '' 
    }
  }
  reader.readAsText(file)
}

onMounted(fetchData)
</script>

<template>
  <div class="p-6 max-w-6xl mx-auto pb-24">
    
    <div class="flex items-center justify-between mb-8 pb-5 border-b border-slate-200">
      <div class="flex items-center gap-4">
        <div :class="['p-3 text-white rounded-xl shadow-lg', activeTab === 'claims' ? 'bg-fuchsia-600 shadow-fuchsia-200' : 'bg-rose-600 shadow-rose-200']">
          <BrainCircuit class="w-6 h-6" />
        </div>
        <div>
          <h1 class="text-xl font-black tracking-tight text-slate-900">База знаний нейросети</h1>
          <p class="text-sm text-slate-500 font-medium">Управление накопленным опытом и правилами тегирования</p>
        </div>
      </div>
      
      <button v-if="activeTab === 'claims'" @click="showAddModal = true" class="flex items-center gap-2 bg-fuchsia-600 hover:bg-fuchsia-700 text-white px-5 py-3 rounded-2xl font-bold shadow-lg shadow-fuchsia-200 transition-colors">
        <Plus class="w-5 h-5" /> Добавить правило
      </button>
      
      <div v-else class="relative">
        <input type="file" id="file-upload" accept=".csv,.txt,.tsv" class="hidden" @change="handleFileUpload" />
        <label for="file-upload" class="flex items-center gap-2 bg-rose-600 hover:bg-rose-700 cursor-pointer text-white px-5 py-3 rounded-2xl font-bold shadow-lg shadow-rose-200 transition-colors">
          <UploadCloud class="w-5 h-5" />
          {{ isUploading ? uploadMessage : 'Загрузить CSV файл' }}
        </label>
      </div>
    </div>

    <div class="flex gap-2 mb-6">
      <button @click="activeTab = 'claims'" :class="['px-6 py-2.5 text-sm font-semibold rounded-t-lg transition-colors flex items-center gap-2', activeTab === 'claims' ? 'bg-white text-fuchsia-600 border-t border-l border-r border-slate-200 shadow-[0_2px_0_0_white]' : 'text-slate-500 hover:bg-slate-100']">
        <BrainCircuit class="w-4 h-4" /> Тегирование Брака
      </button>
      <button @click="activeTab = 'feedbacks'" :class="['px-6 py-2.5 text-sm font-semibold rounded-t-lg transition-colors flex items-center gap-2', activeTab === 'feedbacks' ? 'bg-white text-rose-600 border-t border-l border-r border-slate-200 shadow-[0_2px_0_0_white]' : 'text-slate-500 hover:bg-slate-100']">
        <MessageSquarePlus class="w-4 h-4" /> Аналитика Отзывов (VOC)
      </button>
    </div>

    <div class="bg-white border border-slate-200 rounded-b-3xl rounded-tr-3xl shadow-sm overflow-hidden -mt-px">
      <div class="p-5 border-b border-slate-100 bg-slate-50/50 flex flex-wrap gap-4 items-center justify-between">
        <div class="flex items-center gap-3">
          <BookOpen :class="['w-5 h-5', activeTab === 'claims' ? 'text-fuchsia-600' : 'text-rose-600']" />
          <h2 class="text-sm font-black text-slate-800 uppercase tracking-wide">Память ИИ: 
            <span :class="['ml-1', activeTab === 'claims' ? 'text-fuchsia-600' : 'text-rose-600']">
              {{ activeTab === 'claims' ? filteredClaims.length : filteredFeedbacks.length }} записей
            </span>
          </h2>
        </div>
        
        <div class="relative w-full md:w-80">
          <Search class="w-4 h-4 text-slate-400 absolute left-4 top-3.5" />
          <input type="text" v-model="searchQuery" placeholder="Поиск по тексту..." class="w-full pl-10 pr-4 py-2.5 bg-white border border-slate-200 rounded-xl text-sm font-medium focus:ring-2 focus:border-transparent outline-none transition-all shadow-sm" />
        </div>
      </div>
      
      <div v-if="loading" class="p-16 text-center text-slate-400 animate-pulse">Чтение базы знаний...</div>

      <div v-else-if="activeTab === 'claims'" class="overflow-x-auto max-h-[600px] custom-scroll">
        <table class="w-full text-left border-collapse">
          <thead>
            <tr class="bg-white text-slate-400 border-b border-slate-200 uppercase font-bold text-[10px] sticky top-0 z-10">
              <th class="p-4 w-[50%]">Текст клиента</th>
              <th class="p-4 w-[35%]">Эталонный тег</th>
              <th class="p-4 w-[15%] text-right">Удалить</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-100 text-sm font-medium">
            <tr v-for="item in filteredClaims" :key="item.id" class="hover:bg-slate-50 group">
              <td class="p-4 leading-relaxed">"{{ item.content }}"</td>
              <td class="p-4"><span class="px-2.5 py-1 bg-fuchsia-50 text-fuchsia-700 text-xs rounded-lg">{{ item.tags }}</span></td>
              <td class="p-4 text-right">
                <button @click="deleteEntry(item.id, 'claims')" class="p-2 text-slate-300 hover:text-red-500 opacity-0 group-hover:opacity-100"><Trash2 class="w-4 h-4" /></button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-else class="overflow-x-auto max-h-[600px] custom-scroll">
        <table class="w-full text-left border-collapse">
          <thead>
            <tr class="bg-white text-slate-400 border-b border-slate-200 uppercase font-bold text-[10px] sticky top-0 z-10">
              <th class="p-4 w-[45%]">Текст отзыва (Пример)</th>
              <th class="p-4 w-[25%]">Эталонные Теги (Матрица)</th>
              <th class="p-4 w-[20%]">Извлеченная Идея</th>
              <th class="p-4 w-[10%] text-right">Удалить</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-100 text-sm font-medium">
            <tr v-for="item in filteredFeedbacks" :key="item.id" class="hover:bg-slate-50 group align-top">
              <td class="p-4 leading-relaxed text-xs text-slate-700 italic">"{{ item.content }}"</td>
              <td class="p-4">
                <div class="flex flex-col gap-1">
                  <span v-for="tag in (item.tags ? item.tags.split(',') : [])" :key="tag" class="px-2 py-1 bg-blue-50 text-blue-700 text-[10px] font-bold rounded border border-blue-100 w-fit">
                    {{ tag.trim() }}
                  </span>
                  <span v-if="!item.tags" class="text-xs text-slate-400">Пусто (Игнор)</span>
                </div>
              </td>
              <td class="p-4 text-xs text-emerald-700 font-semibold">
                {{ item.suggestion || '—' }}
              </td>
              <td class="p-4 text-right">
                <button @click="deleteEntry(item.id, 'feedbacks')" class="p-2 text-slate-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"><Trash2 class="w-4 h-4" /></button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

    </div>

    <div v-if="showAddModal" class="fixed inset-0 z-[500] bg-slate-900/60 flex items-center justify-center p-4">
      <div class="bg-white rounded-3xl w-full max-w-3xl overflow-hidden flex flex-col">
        <div class="p-6 border-b border-slate-100 flex justify-between">
          <h2 class="text-lg font-black">Добавить правило</h2>
          <button @click="showAddModal = false"><X class="w-5 h-5 text-slate-400"/></button>
        </div>
        <div class="p-6 space-y-6">
          <textarea v-model="newRuleText" rows="3" class="w-full p-4 bg-slate-50 border border-slate-200 rounded-xl outline-none" placeholder="Текст..."></textarea>
          <div class="flex flex-wrap gap-2">
            <button v-for="(name, id) in CATEGORIES" :key="id" @click="toggleCategory(parseInt(id))"
                    :class="['px-3 py-2 text-xs font-bold rounded-xl border', newRuleCategories.includes(parseInt(id)) ? 'bg-fuchsia-600 text-white' : 'text-slate-500']">
              {{ name }}
            </button>
          </div>
        </div>
        <div class="p-6 bg-slate-50 text-right">
          <button @click="addEntry" class="px-6 py-2.5 bg-fuchsia-600 text-white font-bold rounded-xl">Сохранить</button>
        </div>
      </div>
    </div>

  </div>
</template>