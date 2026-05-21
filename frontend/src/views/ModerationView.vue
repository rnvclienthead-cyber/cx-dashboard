<script setup>
import { ref, onMounted, computed } from 'vue'

const claims = ref([])
const loading = ref(true)
const error = ref(null)
const currentPage = ref(1)
const itemsPerPage = 20

// ФИЛЬТРЫ: Используем строки YYYY-MM-DD для правильной работы <input type="date">
const today = new Date()
const weekAgo = new Date()
weekAgo.setDate(today.getDate() - 7)

const startDate = ref(weekAgo.toISOString().split('T')[0])
const endDate = ref(today.toISOString().split('T')[0])

const filterMode = ref('Все ожидающие')
const filterCategory = ref('Все категории')

const CATEGORIES = {
  1: "Некомплект: Фурнитура", 2: "Некомплект: Несущие детали", 3: "Состояние упаковки",
  4: "Производственный дефект", 5: "Механические повреждения", 6: "Инструкция и сборка",
  7: "Хлипкость / Устойчивость", 8: "Пересорт / Ошибка склада", 9: "Качество материалов",
  10: "Габариты и Размер", 11: "Несоответствие описанию", 12: "Субъективное 'Не подошло'",
  13: "Следы использования / Б/У"
}

const fetchClaims = async () => {
  loading.value = true
  error.value = null
  try {
    const response = await fetch('http://127.0.0.1:8001/api/v1/claims/pending')
    if (!response.ok) throw new Error('Ошибка сервера')
    const json = await response.json()
    // Защита от пустых данных
    claims.value = (json.data || []).map(c => ({ ...c, user_selected_cats: [] }))
  } catch (err) {
    error.value = 'Ошибка загрузки данных. Проверьте подключение.'
  } finally {
    loading.value = false
  }
}

// Умный парсинг даты из форматов базы (например, DD.MM.YYYY HH:MM)
const parseDate = (dateString) => {
  if (!dateString) return new Date(0)
  if (dateString.includes('.')) {
    const [datePart] = dateString.split(' ')
    const [d, m, y] = datePart.split('.')
    return new Date(`${y}-${m}-${d}`)
  }
  return new Date(dateString)
}

const filteredClaims = computed(() => {
  return claims.value.filter(c => {
    // 1. Статус "Одобрено" мы больше не проверяем — бэкенд уже прислал только нужные!

    // 2. Фильтр по дате
    const claimDate = parseDate(c['Дата и время оформления заявки на возврат'] || c['Дата заказа'])
    const filterStart = new Date(startDate.value)
    const filterEnd = new Date(endDate.value)
    filterEnd.setHours(23, 59, 59, 999) // Включаем конец дня
    
    // Если дата корректная, проверяем диапазон. 
    // Если дата кривая (например, в базе пусто) — лучше её показать, чем спрятать.
    if (claimDate.getTime() > 0) {
      if (claimDate < filterStart || claimDate > filterEnd) return false
    }

    // 3. Фильтр Аудитора
    if (filterMode.value === 'С замечаниями от Аудитора') {
      if (!String(c['Аудит'] || '').toLowerCase().includes('ошибка')) return false
    }

    // 4. Фильтр по категориям
    if (filterCategory.value !== 'Все категории') {
      const catId = Object.keys(CATEGORIES).find(k => CATEGORIES[k] === filterCategory.value)
      const val = String(c[catId] || '').toLowerCase()
      if (!['1', '1.0', '+', 'true', 'да'].includes(val)) return false
    }

    return true
  })
})

const paginatedClaims = computed(() => {
  const start = (currentPage.value - 1) * itemsPerPage
  return filteredClaims.value.slice(start, start + itemsPerPage)
})

const totalPages = computed(() => Math.max(1, Math.ceil(filteredClaims.value.length / itemsPerPage)))

// Безопасный вызов прокрутки окна (вместо window.scrollTo в шаблоне)
const changePage = (step) => {
  currentPage.value += step
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

// --- ПАРСИНГ МЕДИА ---
const parsePhotos = (photoString) => {
  if (!photoString) return []
  return photoString.split(' ').map(group => {
    if (group.includes('|')) {
      const [s3, wb] = group.split('|')
      return { s3: s3.startsWith('//') ? 'https:' + s3 : s3, wb: wb.startsWith('//') ? 'https:' + wb : wb }
    }
    const url = group.startsWith('//') ? 'https:' + group : group
    return { s3: url, wb: url }
  }).slice(0, 6)
}

const parseVideos = (videoString) => {
  if (!videoString) return []
  const regex = /(?:https?:)?\/\/[^\s"'\]\[,<>]+/g
  return videoString.match(regex) || []
}

const getAiTags = (claim) => {
  const tags = []
  for (let i = 1; i <= 13; i++) {
    const val = String(claim[String(i)]).toLowerCase()
    if (['1', '1.0', '+', 'true', 'да'].includes(val)) {
      tags.push(CATEGORIES[i])
    }
  }
  return tags.length ? tags.join(', ') : 'Категории не определены'
}

onMounted(fetchClaims)
</script>

<template>
  <div class="p-6">
    <h1 class="text-2xl font-bold text-slate-800 mb-6">Модерация обращений</h1>

    <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm mb-6 grid grid-cols-1 md:grid-cols-4 gap-4">
      <div>
        <label class="block text-xs font-bold text-slate-500 uppercase mb-2">От</label>
        <input type="date" v-model="startDate" class="w-full border border-slate-200 rounded-lg p-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
      </div>
      <div>
        <label class="block text-xs font-bold text-slate-500 uppercase mb-2">До</label>
        <input type="date" v-model="endDate" class="w-full border border-slate-200 rounded-lg p-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
      </div>
      <div>
        <label class="block text-xs font-bold text-slate-500 uppercase mb-2">Статус</label>
        <select v-model="filterMode" class="w-full border border-slate-200 rounded-lg p-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none">
          <option>Все ожидающие</option>
          <option>С замечаниями от Аудитора</option>
        </select>
      </div>
      <div>
        <label class="block text-xs font-bold text-slate-500 uppercase mb-2">Категория</label>
        <select v-model="filterCategory" class="w-full border border-slate-200 rounded-lg p-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none">
          <option>Все категории</option>
          <option v-for="cat in CATEGORIES" :key="cat">{{ cat }}</option>
        </select>
      </div>
    </div>

    <div v-if="loading" class="text-slate-500 py-8 flex items-center gap-3">
      <div class="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin"></div>
      Загрузка массива заявок из базы...
    </div>

    <div v-else-if="error" class="bg-red-50 text-red-700 p-4 rounded-lg border border-red-200 mb-6">
      {{ error }}
    </div>

    <div v-else-if="filteredClaims.length === 0" class="bg-emerald-50 text-emerald-700 p-8 rounded-xl text-center font-medium border border-emerald-100">
      🎉 Очередь пуста! По заданным фильтрам нет заявок для модерации.
    </div>

    <div v-else class="space-y-6">
      <div v-for="claim in paginatedClaims" :key="claim.SRID" class="bg-white border border-slate-200 rounded-xl p-5 shadow-sm hover:shadow-md transition-shadow">
        
        <div class="flex flex-col lg:flex-row gap-6">
          <div class="flex-[1.2]">
            <div class="flex justify-between text-sm text-slate-500 mb-4 pb-3 border-b border-slate-100">
              <span>📦 <b>Артикул:</b> <span class="text-slate-800">{{ claim['Артикул продавца'] || '---' }}</span></span>
              <span>📅 <b>Дата:</b> {{ claim['Дата и время оформления заявки на возврат'] || claim['Дата заказа'] }}</span>
            </div>

            <div class="bg-slate-50 p-4 rounded-lg border-l-4 border-slate-300 mb-4 text-sm text-slate-700 leading-relaxed">
              {{ claim['Комментарий покупателя'] || 'Нет текста комментария' }}
            </div>

            <div class="bg-emerald-50 px-4 py-3 rounded-lg text-sm text-emerald-800 mb-5 border border-emerald-100 font-medium flex items-center gap-2">
              <span>🤖</span> 
              <span><b>Выбор ИИ:</b> {{ getAiTags(claim) }}</span>
            </div>

            <div class="mb-5">
              <label class="block text-xs font-bold text-slate-500 uppercase mb-3">Если ИИ ошибся, выберите правильные:</label>
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <div v-for="(name, id) in CATEGORIES" :key="id" class="flex items-center gap-2 text-sm text-slate-600 hover:text-slate-900 transition-colors">
                  <input 
                    type="checkbox" 
                    :id="'cat-' + claim.SRID + '-' + id" 
                    :value="Number(id)" 
                    v-model="claim.user_selected_cats"
                    class="w-4 h-4 text-blue-600 rounded border-slate-300 focus:ring-blue-500 cursor-pointer"
                  />
                  <label :for="'cat-' + claim.SRID + '-' + id" class="cursor-pointer select-none leading-tight">{{ name }}</label>
                </div>
              </div>
            </div>
          </div>

          <div class="flex-1 bg-slate-50 rounded-xl p-4 border border-slate-100">
            <h3 class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-4">Прикрепленные файлы</h3>
            
            <div v-if="parsePhotos(claim.photos).length" class="flex flex-wrap gap-3 mb-4">
              <div v-for="(photo, idx) in parsePhotos(claim.photos)" :key="idx" class="relative group">
                <a :href="photo.wb" target="_blank" class="block w-20 h-20 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
                  <img :src="photo.s3" class="w-full h-full object-cover group-hover:scale-110 transition-transform duration-300" />
                </a>
              </div>
            </div>
            
            <div v-if="parseVideos(claim.video_paths).length" class="space-y-2">
              <a v-for="(vid, idx) in parseVideos(claim.video_paths)" :key="idx" :href="vid" target="_blank" class="inline-flex items-center gap-2 bg-white border border-slate-200 text-slate-700 px-3 py-2 rounded-lg text-sm font-medium hover:bg-slate-50 transition-colors shadow-sm">
                🎥 Посмотреть видео {{ idx + 1 }}
              </a>
            </div>

            <div v-if="!parsePhotos(claim.photos).length && !parseVideos(claim.video_paths).length" class="text-sm text-slate-400 text-center py-8">
              Нет прикрепленных медиафайлов
            </div>
          </div>
        </div>
      </div>

      <div v-if="totalPages > 1" class="flex justify-center items-center gap-4 mt-8 pt-6 border-t border-slate-200">
        <button 
          :disabled="currentPage === 1" 
          @click="changePage(-1)"
          class="px-4 py-2 border border-slate-200 rounded-lg bg-white text-slate-600 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed font-medium text-sm transition-colors"
        >
          « Назад
        </button>
        <span class="text-slate-600 text-sm font-medium bg-white px-4 py-2 rounded-lg border border-slate-200">
          Стр. {{ currentPage }} из {{ totalPages }}
        </span>
        <button 
          :disabled="currentPage === totalPages" 
          @click="changePage(1)"
          class="px-4 py-2 border border-slate-200 rounded-lg bg-white text-slate-600 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed font-medium text-sm transition-colors"
        >
          Вперед »
        </button>
      </div>
    </div>
  </div>
</template>