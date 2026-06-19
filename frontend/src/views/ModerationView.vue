<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { ClipboardCheck, Search, Filter, CheckCircle2, Edit3, X, ChevronLeft, ChevronRight, Download, Image as ImageIcon, Calendar, Bot, MessageSquare } from 'lucide-vue-next'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'

const platformStore = usePlatformStore()

const CATEGORIES = {
  1: "Некомплект: Фурнитура", 2: "Некомплект: Несущие детали", 3: "Состояние упаковки",
  4: "Производственный дефект", 5: "Механические повреждения", 6: "Инструкция и сборка",
  7: "Хлипкость / Устойчивость", 8: "Пересорт / Ошибка склада", 9: "Качество материалов",
  10: "Габариты и Размер", 11: "Несоответствие описанию", 12: "Субъективное 'Не подошло'",
  13: "Следы использования / Б/У"
}

const VOC_TAGS = [
  "КОНСТРУКЦИЯ: Хлипкость и Неустойчивость",
  "КОНСТРУКЦИЯ: Слабые узлы и Соединения",
  "КОНСТРУКЦИЯ: Тонкий металл / Пластик",
  "ЭРГОНОМИКА: Мало места / Вместимость",
  "ЭРГОНОМИКА: Несоответствие размерам вещей",
  "ЭРГОНОМИКА: Неудобная форма изделия",
  "ФУНКЦИОНАЛ: Нет стопоров на колесах",
  "ФУНКЦИОНАЛ: Нехватка бортов/защиты",
  "ФУНКЦИОНАЛ: Запрос нового элемента",
  "СБОРКА: Непонятная инструкция",
  "СБОРКА: Несовпадение пазов/отверстий",
  "СБОРКА: Тяжелый физический монтаж",
  "ОЖИДАНИЯ: Отличие цвета от фото",
  "ОЖИДАНИЯ: Ощущение дешевизны вживую",
]

// ─── ВКЛАДКИ ─────────────────────────────────────────────────────────────────
const activeTab = ref('returns') // 'returns' | 'feedbacks'

// ─── ВОЗВРАТЫ / ПРЕТЕНЗИИ ────────────────────────────────────────────────────
const queue = ref([])
const loading = ref(true)

const todayObj = new Date()
const endOfTodayStr = `${todayObj.getFullYear()}-${String(todayObj.getMonth() + 1).padStart(2, '0')}-${String(todayObj.getDate()).padStart(2, '0')}`
const lastWeekObj = new Date(todayObj.getTime() - 7 * 24 * 60 * 60 * 1000)
const startOfWeekStr = `${lastWeekObj.getFullYear()}-${String(lastWeekObj.getMonth() + 1).padStart(2, '0')}-${String(lastWeekObj.getDate()).padStart(2, '0')}`

const startDate = ref(startOfWeekStr)
const endDate = ref(endOfTodayStr)
const showCalendarPopover = ref(false)
const calendarYear = ref(todayObj.getFullYear())
const calendarMonth = ref(todayObj.getMonth())
const monthNames = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

const changeCalendarMonth = (dir) => {
  let newM = calendarMonth.value + dir
  let newY = calendarYear.value
  if (newM > 11) { newM = 0; newY++ }
  else if (newM < 0) { newM = 11; newY-- }
  calendarMonth.value = newM
  calendarYear.value = newY
}

const handleCalendarDayClick = (d) => {
  if (!d.isCurrentMonth || !d.dateStr) return
  if (!startDate.value || (startDate.value && endDate.value)) {
    startDate.value = d.dateStr
    endDate.value = ''
  } else {
    if (d.dateStr < startDate.value) {
      startDate.value = d.dateStr
      endDate.value = ''
    } else {
      endDate.value = d.dateStr
      showCalendarPopover.value = false
    }
  }
}

const calendarDays = computed(() => {
  const firstDay = new Date(calendarYear.value, calendarMonth.value, 1).getDay()
  const padding = firstDay === 0 ? 6 : firstDay - 1
  const totalDays = new Date(calendarYear.value, calendarMonth.value + 1, 0).getDate()
  const days = []
  const prevTotal = new Date(calendarYear.value, calendarMonth.value, 0).getDate()
  for (let i = padding - 1; i >= 0; i--) days.push({ day: prevTotal - i, isCurrentMonth: false, dateStr: null })
  for (let i = 1; i <= totalDays; i++) {
    const mStr = String(calendarMonth.value + 1).padStart(2, '0')
    const dStr = String(i).padStart(2, '0')
    days.push({ day: i, isCurrentMonth: true, dateStr: `${calendarYear.value}-${mStr}-${dStr}` })
  }
  return days
})

const formatDateDisplay = (dateStr) => {
  if (!dateStr) return '...'
  const p = dateStr.split('-')
  return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : dateStr
}

const checkDateRange = (claimDateStr, startYMD, endYMD) => {
  if (!claimDateStr) return true
  let dStr = claimDateStr.substring(0, 10)
  if (dStr.includes('.')) {
    const parts = dStr.split('.')
    if (parts.length === 3) dStr = `${parts[2]}-${parts[1]}-${parts[0]}`
  }
  if (startYMD && dStr < startYMD) return false
  if (endYMD && dStr > endYMD) return false
  return true
}

const filterMode = ref('Все ожидающие')
const categoryFilter = ref('Все категории')
const currentPage = ref(1)
const itemsPerPage = 20

const processingSrids = ref(new Set())
const lightbox = ref({ isOpen: false, photos: [], index: 0 })

const fetchQueue = async () => {
  loading.value = true
  try {
    const res = await apiFetch(`/api/v1/ai/moderation/queue?platform=${platformStore.platform}`)
    const json = await res.json()
    queue.value = (json.data || []).map(item => {
      const activeCatIds = []
      for (let i = 1; i <= 13; i++) {
        if (item[`cat_${i}`]) activeCatIds.push(i)
      }
      // ЯМ: фото уже в данных; WB: ленивая загрузка через claim-media
      const photos = platformStore.platform === 'ym' ? (item.photos || '') : null
      return { ...item, activeCatIds, editedCatIds: [...activeCatIds], photos }
    })
  } catch (e) {
    console.error("Ошибка загрузки очереди:", e)
  } finally {
    loading.value = false
  }
}

watch([startDate, endDate, filterMode, categoryFilter], () => { currentPage.value = 1 })
watch(() => platformStore.platform, fetchQueue)

const filteredQueue = computed(() => {
  return queue.value.filter(c => {
    const startYMD = startDate.value
    const endYMD = endDate.value || startDate.value
    if (!checkDateRange(c.claim_date, startYMD, endYMD)) return false
    if (filterMode.value === 'С ошибками аудита') {
      if (!c.audit_status || !c.audit_status.toLowerCase().includes('ошибка')) return false
    }
    if (categoryFilter.value !== 'Все категории') {
      const targetId = Object.keys(CATEGORIES).find(key => CATEGORIES[key] === categoryFilter.value)
      if (!c.activeCatIds.includes(parseInt(targetId))) return false
    }
    return true
  })
})

const paginatedQueue = computed(() => {
  const start = (currentPage.value - 1) * itemsPerPage
  return filteredQueue.value.slice(start, start + itemsPerPage)
})

const totalPages = computed(() => Math.ceil(filteredQueue.value.length / itemsPerPage))

watch(paginatedQueue, async (newItems) => {
  for (const claim of newItems) {
    if (claim.photos === null) {
      try {
        const res = await apiFetch(`/api/v1/analytics/claim-media/${claim.srid}`)
        if (res.ok) {
          const media = await res.json()
          claim.photos = media.photos || ""
        } else {
          claim.photos = ""
        }
      } catch {
        claim.photos = ""
      }
    }
  }
}, { immediate: true })

const toggleCategory = (claim, catId) => {
  const idx = claim.editedCatIds.indexOf(catId)
  if (idx > -1) claim.editedCatIds.splice(idx, 1)
  else claim.editedCatIds.push(catId)
}

const isEdited = (claim) => {
  const orig = [...claim.activeCatIds].sort()
  const curr = [...claim.editedCatIds].sort()
  return JSON.stringify(orig) !== JSON.stringify(curr)
}

const submitAction = async (claim, action) => {
  if (processingSrids.value.has(claim.srid)) return
  processingSrids.value.add(claim.srid)
  try {
    const payload = {
      srid: claim.srid,
      action,
      categories: action === 'correct' ? claim.editedCatIds : [],
      platform: platformStore.platform
    }
    const res = await apiFetch('/api/v1/ai/moderation/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    if (res.ok) {
      queue.value = queue.value.filter(c => c.srid !== claim.srid)
      if (paginatedQueue.value.length === 0 && currentPage.value > 1) currentPage.value--
    }
  } catch (e) {
    console.error("Ошибка сохранения:", e)
  } finally {
    processingSrids.value.delete(claim.srid)
  }
}

const parsePhotos = (str) => str ? str.split(' ').map(g => g.split('|').pop().replace(/^\/\//, 'https://')).slice(0, 8) : []
const downloadImg = async (url, filename = 'photo.jpg') => {
  try {
    const response = await apiFetch(url)
    const blob = await response.blob()
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = filename
    link.click()
  } catch { window.open(url, '_blank') }
}
const openLightbox = (photos, startIndex) => { lightbox.value = { isOpen: true, photos, index: startIndex } }
const nextPhoto = () => { lightbox.value.index = (lightbox.value.index + 1) % lightbox.value.photos.length }
const prevPhoto = () => { lightbox.value.index = (lightbox.value.index - 1 + lightbox.value.photos.length) % lightbox.value.photos.length }

// ─── ОТЗЫВЫ (VOC) ────────────────────────────────────────────────────────────
const fbQueue = ref([])
const fbLoading = ref(false)
const fbCurrentPage = ref(1)
const fbItemsPerPage = 15
const processingFbIds = ref(new Set())

const fetchFbQueue = async () => {
  fbLoading.value = true
  try {
    const res = await apiFetch(`/api/v1/ai/feedback-moderation/queue?platform=${platformStore.platform}`)
    const json = await res.json()
    fbQueue.value = (json.data || []).map(item => ({
      ...item,
      editedTags: [...(item.ai_tags?.tags || [])],
      editedSuggestion: item.ai_tags?.suggestion || '',
    }))
  } catch (e) {
    console.error("Ошибка загрузки отзывов:", e)
  } finally {
    fbLoading.value = false
  }
}

const fbPaginated = computed(() => {
  const start = (fbCurrentPage.value - 1) * fbItemsPerPage
  return fbQueue.value.slice(start, start + fbItemsPerPage)
})
const fbTotalPages = computed(() => Math.ceil(fbQueue.value.length / fbItemsPerPage))

const fbIsEdited = (item) => {
  const orig = JSON.stringify([...( item.ai_tags?.tags || [])].sort())
  const curr = JSON.stringify([...item.editedTags].sort())
  return orig !== curr || item.editedSuggestion !== (item.ai_tags?.suggestion || '')
}

const toggleVocTag = (item, tag) => {
  const idx = item.editedTags.indexOf(tag)
  if (idx > -1) item.editedTags.splice(idx, 1)
  else item.editedTags.push(tag)
}

const submitFbAction = async (item, action) => {
  if (processingFbIds.value.has(item.id)) return
  processingFbIds.value.add(item.id)
  try {
    const payload = {
      id: item.id,
      action,
      tags: action === 'correct' ? item.editedTags : (item.ai_tags?.tags || []),
      suggestion: action === 'correct' ? item.editedSuggestion : (item.ai_tags?.suggestion || ''),
      platform: platformStore.platform
    }
    const res = await apiFetch('/api/v1/ai/feedback-moderation/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    if (res.ok) {
      fbQueue.value = fbQueue.value.filter(f => f.id !== item.id)
      if (fbPaginated.value.length === 0 && fbCurrentPage.value > 1) fbCurrentPage.value--
    }
  } catch (e) {
    console.error("Ошибка сохранения отзыва:", e)
  } finally {
    processingFbIds.value.delete(item.id)
  }
}

const starColor = (val) => {
  if (val <= 2) return 'text-red-500'
  if (val === 3) return 'text-amber-500'
  return 'text-emerald-500'
}

// ─── ИНИЦИАЛИЗАЦИЯ ────────────────────────────────────────────────────────────
onMounted(() => {
  fetchQueue()
  fetchFbQueue()
})

watch(() => platformStore.platform, () => {
  fetchQueue()
  fetchFbQueue()
})
</script>

<template>
  <div class="p-6 w-full mx-auto pb-24 bg-slate-50 min-h-screen font-sans max-w-[1600px] text-slate-800 antialiased">

    <!-- Заголовок -->
    <div class="flex items-center justify-between mb-6 pb-5 border-b border-slate-200">
      <div class="flex items-center gap-4">
        <div class="p-3 bg-indigo-600 text-white rounded-xl shadow-lg shadow-indigo-200/50">
          <ClipboardCheck class="w-6 h-6" />
        </div>
        <div>
          <h1 class="text-xl font-black tracking-tight text-slate-900">Модерация (Ручная проверка)</h1>
          <p class="text-sm text-slate-500 font-medium">Обучение нейросети на основе ваших исправлений</p>
        </div>
      </div>
    </div>

    <!-- Вкладки -->
    <div class="flex gap-2 mb-6 border-b border-slate-200 pb-px">
      <button @click="activeTab = 'returns'"
              :class="['px-5 py-2.5 text-sm font-bold rounded-t-lg transition-colors flex items-center gap-2',
                       activeTab === 'returns' ? 'bg-white text-indigo-600 border-t border-l border-r border-slate-200 shadow-[0_2px_0_0_#f8fafc]' : 'text-slate-500 hover:bg-slate-100']">
        <ClipboardCheck class="w-4 h-4" />
        {{ platformStore.platform === 'ym' ? 'Возвраты ЯМ' : 'Претензии WB' }}
        <span class="ml-1 px-2 py-0.5 rounded-full text-xs font-black"
              :class="activeTab === 'returns' ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-500'">
          {{ filteredQueue.length }}
        </span>
      </button>
      <button @click="activeTab = 'feedbacks'"
              :class="['px-5 py-2.5 text-sm font-bold rounded-t-lg transition-colors flex items-center gap-2',
                       activeTab === 'feedbacks' ? 'bg-white text-rose-600 border-t border-l border-r border-slate-200 shadow-[0_2px_0_0_#f8fafc]' : 'text-slate-500 hover:bg-slate-100']">
        <MessageSquare class="w-4 h-4" />
        Отзывы (VOC)
        <span class="ml-1 px-2 py-0.5 rounded-full text-xs font-black"
              :class="activeTab === 'feedbacks' ? 'bg-rose-100 text-rose-700' : 'bg-slate-100 text-slate-500'">
          {{ fbQueue.length }}
        </span>
      </button>
    </div>

    <!-- ═══════════════════════════════════════════════════════════════ -->
    <!-- ВКЛАДКА: ВОЗВРАТЫ / ПРЕТЕНЗИИ                                  -->
    <!-- ═══════════════════════════════════════════════════════════════ -->
    <div v-if="activeTab === 'returns'">

      <!-- Панель фильтров -->
      <div class="bg-white border border-slate-200 rounded-2xl p-5 mb-8 shadow-sm flex flex-wrap gap-4 items-center">

        <!-- Календарь -->
        <div class="relative z-50">
          <div class="flex items-center bg-slate-50 border border-slate-200 rounded-xl px-4 py-2.5 cursor-pointer hover:border-indigo-400 transition-colors"
               @click="showCalendarPopover = !showCalendarPopover">
            <Calendar class="w-4 h-4 text-indigo-500 mr-2" />
            <span class="text-sm font-bold text-slate-700">{{ formatDateDisplay(startDate) }} — {{ formatDateDisplay(endDate) }}</span>
          </div>
          <div v-if="showCalendarPopover" class="absolute left-0 mt-2 bg-white border border-slate-100 rounded-3xl shadow-2xl p-5 w-80 animate-in zoom-in-95 duration-200">
            <div class="flex justify-between items-center mb-4">
              <button @click="changeCalendarMonth(-1)" class="p-2 hover:bg-slate-100 rounded-xl text-slate-500 transition-colors"><ChevronLeft class="w-4 h-4"/></button>
              <span class="text-sm font-black text-slate-800 tracking-tight">{{ monthNames[calendarMonth] }} {{ calendarYear }}</span>
              <button @click="changeCalendarMonth(1)" class="p-2 hover:bg-slate-100 rounded-xl text-slate-500 transition-colors"><ChevronRight class="w-4 h-4"/></button>
            </div>
            <div class="grid grid-cols-7 gap-1 text-center text-[10px] font-black text-slate-400 uppercase mb-2">
              <div>Пн</div><div>Вт</div><div>Ср</div><div>Чт</div><div>Пт</div><div>Сб</div><div>Вс</div>
            </div>
            <div class="grid grid-cols-7 gap-1">
              <div v-for="(d, idx) in calendarDays" :key="idx" @click="handleCalendarDayClick(d)"
                   :class="['h-9 flex items-center justify-center text-sm font-bold rounded-xl transition-all select-none',
                            !d.isCurrentMonth ? 'text-slate-200 pointer-events-none' : 'cursor-pointer',
                            d.dateStr === startDate || d.dateStr === endDate ? 'bg-indigo-600 text-white shadow-md' : '',
                            d.dateStr > startDate && d.dateStr < endDate && endDate ? 'bg-indigo-50 text-indigo-700' : '',
                            d.isCurrentMonth && d.dateStr !== startDate && d.dateStr !== endDate && !(d.dateStr > startDate && d.dateStr < endDate) ? 'hover:bg-slate-100 text-slate-700' : '']">
                {{ d.day }}
              </div>
            </div>
          </div>
        </div>

        <div class="h-8 w-px bg-slate-200 mx-1 hidden lg:block"></div>

        <div class="flex items-center gap-2 bg-slate-50 p-1.5 rounded-xl border border-slate-100">
          <button @click="filterMode = 'Все ожидающие'"
                  :class="['px-4 py-2 text-sm font-bold rounded-lg transition-colors', filterMode === 'Все ожидающие' ? 'bg-white shadow-sm text-indigo-600' : 'text-slate-500 hover:text-slate-700']">
            Все
          </button>
          <button @click="filterMode = 'С ошибками аудита'"
                  :class="['px-4 py-2 text-sm font-bold rounded-lg transition-colors', filterMode === 'С ошибками аудита' ? 'bg-white shadow-sm text-red-600' : 'text-slate-500 hover:text-slate-700']">
            С замечаниями ИИ
          </button>
        </div>

        <div class="h-8 w-px bg-slate-200 mx-1 hidden lg:block"></div>

        <div class="relative flex-1 min-w-[200px]">
          <Filter class="w-4 h-4 text-slate-400 absolute left-4 top-3" />
          <select v-model="categoryFilter" class="w-full appearance-none pl-10 pr-10 py-2.5 bg-slate-50 hover:bg-white border border-slate-200 rounded-xl text-sm font-semibold text-slate-700 focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none cursor-pointer transition-colors">
            <option>Все категории</option>
            <option v-for="(name, id) in CATEGORIES" :key="id" :value="name">{{ name }}</option>
          </select>
        </div>

        <div class="text-sm font-bold text-slate-400 px-4">
          В очереди: <span class="text-indigo-600 font-black">{{ filteredQueue.length }}</span>
        </div>
      </div>

      <div v-if="loading" class="text-center py-24 text-slate-400 font-bold tracking-wide animate-pulse">⚙️ Загрузка очереди на модерацию...</div>
      <div v-else-if="filteredQueue.length === 0" class="text-center py-24 bg-white border border-slate-200 rounded-3xl shadow-sm">
        <div class="text-6xl mb-4">🎉</div>
        <h3 class="text-xl font-black text-slate-800 mb-2">Очередь пуста</h3>
        <p class="text-slate-500">За выбранный период нет записей с тегами ИИ, ожидающих проверки.</p>
      </div>

      <div v-else class="space-y-6">
        <div v-for="claim in paginatedQueue" :key="claim.srid" class="bg-white border border-slate-200 rounded-3xl shadow-sm overflow-hidden">
          <div class="p-6">
            <div class="flex flex-col lg:flex-row gap-8">

              <div class="w-full lg:w-[50%] space-y-5">
                <div class="flex items-center gap-4 mb-2">
                  <div class="text-xs font-black text-slate-400 uppercase tracking-wider">Артикул: <span class="text-indigo-600 ml-1 text-sm">{{ claim.sku || '---' }}</span></div>
                </div>

                <div class="bg-slate-50 p-5 rounded-2xl border border-slate-100 text-slate-700 font-medium text-sm leading-relaxed shadow-inner">
                  "{{ claim.comment || 'Нет текста комментария' }}"
                </div>

                <div class="bg-emerald-50 border-l-4 border-emerald-500 p-4 rounded-r-xl">
                  <div class="text-xs font-bold text-emerald-800 uppercase tracking-wider mb-2 flex items-center gap-2">
                    <Bot class="w-4 h-4" /> Решение нейросети:
                  </div>
                  <div class="flex flex-wrap gap-2">
                    <span v-for="catId in claim.activeCatIds" :key="catId" class="px-2.5 py-1 bg-white text-emerald-700 text-xs font-bold rounded-lg shadow-sm border border-emerald-200">
                      {{ CATEGORIES[catId] }}
                    </span>
                    <span v-if="claim.activeCatIds.length === 0" class="text-emerald-600/70 text-sm font-semibold italic">Категории не определены</span>
                  </div>
                  <div v-if="claim.audit_status" class="mt-3 text-xs font-bold text-red-600 flex items-center gap-1">
                    ⚠️ Аудит: {{ claim.audit_status }}
                  </div>
                </div>

                <div>
                  <div class="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3">Выберите правильные теги (Клик для выбора):</div>
                  <div class="flex flex-wrap gap-2">
                    <button v-for="(name, id) in CATEGORIES" :key="id"
                            @click="toggleCategory(claim, parseInt(id))"
                            :class="['px-3 py-1.5 text-xs font-bold rounded-xl border transition-all',
                                     claim.editedCatIds.includes(parseInt(id)) ? 'bg-indigo-600 border-indigo-700 text-white shadow-md' : 'bg-white border-slate-200 text-slate-500 hover:border-indigo-300 hover:text-indigo-600']">
                      {{ name }}
                    </button>
                  </div>
                </div>

                <div class="flex gap-4 pt-4 border-t border-slate-100">
                  <button v-if="isEdited(claim)" @click="submitAction(claim, 'correct')" :disabled="processingSrids.has(claim.srid)"
                          class="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white py-3 rounded-xl font-black text-sm flex items-center justify-center gap-2 transition-colors shadow-lg shadow-indigo-200 disabled:opacity-50">
                    <Edit3 class="w-4 h-4" /> Исправить и Обучить ИИ
                  </button>
                  <button v-else @click="submitAction(claim, 'confirm')" :disabled="processingSrids.has(claim.srid)"
                          class="flex-1 bg-emerald-500 hover:bg-emerald-600 text-white py-3 rounded-xl font-black text-sm flex items-center justify-center gap-2 transition-colors shadow-lg shadow-emerald-200 disabled:opacity-50">
                    <CheckCircle2 class="w-4 h-4" /> Подтвердить выбор ИИ
                  </button>
                </div>
              </div>

              <div class="w-full lg:w-[50%] border-t lg:border-t-0 lg:border-l border-slate-100 pt-6 lg:pt-0 lg:pl-8">
                <div class="text-xs font-black text-slate-400 uppercase tracking-wider mb-4 flex items-center gap-2">
                  <ImageIcon class="w-4 h-4" /> Фотофиксация
                </div>
                <div v-if="claim.photos === null" class="h-48 flex items-center justify-center bg-slate-50 rounded-2xl border border-dashed border-slate-200 text-slate-400 text-xs font-bold animate-pulse">
                  ⏳ Загрузка фото...
                </div>
                <div v-else-if="parsePhotos(claim.photos).length === 0" class="h-48 flex items-center justify-center bg-slate-50 rounded-2xl border border-dashed border-slate-200 text-slate-400 text-xs font-bold">
                  Фотографий нет
                </div>
                <div v-else class="grid grid-cols-2 gap-3">
                  <div v-for="(img, idx) in parsePhotos(claim.photos).slice(0, 6)" :key="idx"
                       class="relative group aspect-square rounded-xl overflow-hidden border border-slate-200 shadow-sm cursor-zoom-in"
                       @click="openLightbox(parsePhotos(claim.photos), idx)">
                    <img :src="img" class="w-full h-full object-cover group-hover:scale-110 transition-transform duration-300" />
                    <button @click.stop="downloadImg(img, `claim_${claim.srid}_${idx+1}.jpg`)"
                            class="absolute bottom-2 right-2 p-2 bg-black/60 text-white rounded-lg opacity-0 group-hover:opacity-100 transition-opacity">
                      <Download class="w-4 h-4" />
                    </button>
                  </div>
                </div>
              </div>

            </div>
          </div>
        </div>

        <div v-if="totalPages > 1" class="flex justify-center items-center gap-4 mt-8 pb-8">
          <button @click="currentPage--" :disabled="currentPage === 1" class="p-2 bg-white border border-slate-200 rounded-xl text-slate-600 hover:text-indigo-600 disabled:opacity-50 transition-colors shadow-sm"><ChevronLeft class="w-5 h-5"/></button>
          <span class="text-sm font-black text-slate-700">Страница {{ currentPage }} из {{ totalPages }}</span>
          <button @click="currentPage++" :disabled="currentPage === totalPages" class="p-2 bg-white border border-slate-200 rounded-xl text-slate-600 hover:text-indigo-600 disabled:opacity-50 transition-colors shadow-sm"><ChevronRight class="w-5 h-5"/></button>
        </div>
      </div>
    </div>

    <!-- ═══════════════════════════════════════════════════════════════ -->
    <!-- ВКЛАДКА: ОТЗЫВЫ (VOC)                                          -->
    <!-- ═══════════════════════════════════════════════════════════════ -->
    <div v-if="activeTab === 'feedbacks'">

      <div class="bg-rose-50 border border-rose-200 rounded-2xl p-4 mb-6 flex items-start gap-3">
        <MessageSquare class="w-5 h-5 text-rose-500 mt-0.5 shrink-0" />
        <div class="text-sm text-rose-800">
          <span class="font-black">Модерация VOC-тегов.</span> Здесь отображаются отзывы, которые ИИ уже разметил. Подтвердите теги или исправьте их — ваши правки сохраняются в базу знаний и обучают нейросеть.
        </div>
      </div>

      <div v-if="fbLoading" class="text-center py-24 text-slate-400 font-bold animate-pulse">⚙️ Загрузка очереди отзывов...</div>
      <div v-else-if="fbQueue.length === 0" class="text-center py-24 bg-white border border-slate-200 rounded-3xl shadow-sm">
        <div class="text-6xl mb-4">🎉</div>
        <h3 class="text-xl font-black text-slate-800 mb-2">Очередь пуста</h3>
        <p class="text-slate-500">Нет отзывов с VOC-тегами, ожидающих проверки. Сначала запустите ИИ-анализ отзывов.</p>
      </div>

      <div v-else class="space-y-5">
        <div v-for="item in fbPaginated" :key="item.id" class="bg-white border border-slate-200 rounded-3xl shadow-sm overflow-hidden">
          <div class="p-6">
            <div class="flex flex-col lg:flex-row gap-8">

              <!-- Текст отзыва + звёзды -->
              <div class="w-full lg:w-[45%] space-y-4">
                <div class="flex items-center gap-3">
                  <div class="flex gap-0.5">
                    <span v-for="s in 5" :key="s" :class="['text-lg', s <= item.valuation ? starColor(item.valuation) : 'text-slate-200']">★</span>
                  </div>
                  <span class="text-xs font-black text-slate-400 uppercase tracking-wider">Оценка: {{ item.valuation }} / 5</span>
                </div>

                <div class="bg-slate-50 p-5 rounded-2xl border border-slate-100 text-slate-700 font-medium text-sm leading-relaxed shadow-inner max-h-48 overflow-y-auto">
                  "{{ item.text || 'Нет текста' }}"
                </div>

                <div class="bg-rose-50 border-l-4 border-rose-400 p-4 rounded-r-xl">
                  <div class="text-xs font-bold text-rose-800 uppercase tracking-wider mb-2 flex items-center gap-2">
                    <Bot class="w-4 h-4" /> Теги ИИ:
                  </div>
                  <div class="flex flex-wrap gap-2 mb-2">
                    <span v-for="tag in (item.ai_tags?.tags || [])" :key="tag"
                          class="px-2.5 py-1 bg-white text-rose-700 text-xs font-bold rounded-lg shadow-sm border border-rose-200">
                      {{ tag }}
                    </span>
                  </div>
                  <div v-if="item.ai_tags?.suggestion" class="text-xs text-rose-600 font-semibold italic">
                    💡 Идея: {{ item.ai_tags.suggestion }}
                  </div>
                </div>
              </div>

              <!-- Редактирование тегов -->
              <div class="w-full lg:w-[55%] border-t lg:border-t-0 lg:border-l border-slate-100 pt-6 lg:pt-0 lg:pl-8 space-y-4">
                <div class="text-xs font-bold text-slate-500 uppercase tracking-wider">Выберите правильные теги:</div>
                <div class="flex flex-wrap gap-2">
                  <button v-for="tag in VOC_TAGS" :key="tag"
                          @click="toggleVocTag(item, tag)"
                          :class="['px-3 py-1.5 text-xs font-bold rounded-xl border transition-all',
                                   item.editedTags.includes(tag) ? 'bg-rose-600 border-rose-700 text-white shadow-md' : 'bg-white border-slate-200 text-slate-500 hover:border-rose-300 hover:text-rose-600']">
                    {{ tag }}
                  </button>
                </div>

                <div>
                  <div class="text-xs font-bold text-slate-500 uppercase tracking-wider mb-1">Идея / Предложение по улучшению:</div>
                  <input v-model="item.editedSuggestion" type="text" placeholder="Например: Увеличить толщину стенок"
                         class="w-full px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:ring-2 focus:ring-rose-500/20 focus:border-rose-400 outline-none transition-colors" />
                </div>

                <div class="flex gap-3 pt-2 border-t border-slate-100">
                  <button v-if="fbIsEdited(item)" @click="submitFbAction(item, 'correct')" :disabled="processingFbIds.has(item.id)"
                          class="flex-1 bg-rose-600 hover:bg-rose-700 text-white py-3 rounded-xl font-black text-sm flex items-center justify-center gap-2 transition-colors shadow-lg shadow-rose-200 disabled:opacity-50">
                    <Edit3 class="w-4 h-4" /> Исправить и Обучить ИИ
                  </button>
                  <button v-else @click="submitFbAction(item, 'confirm')" :disabled="processingFbIds.has(item.id)"
                          class="flex-1 bg-emerald-500 hover:bg-emerald-600 text-white py-3 rounded-xl font-black text-sm flex items-center justify-center gap-2 transition-colors shadow-lg shadow-emerald-200 disabled:opacity-50">
                    <CheckCircle2 class="w-4 h-4" /> Подтвердить теги ИИ
                  </button>
                </div>
              </div>

            </div>
          </div>
        </div>

        <div v-if="fbTotalPages > 1" class="flex justify-center items-center gap-4 mt-8 pb-8">
          <button @click="fbCurrentPage--" :disabled="fbCurrentPage === 1" class="p-2 bg-white border border-slate-200 rounded-xl text-slate-600 hover:text-rose-600 disabled:opacity-50 transition-colors shadow-sm"><ChevronLeft class="w-5 h-5"/></button>
          <span class="text-sm font-black text-slate-700">Страница {{ fbCurrentPage }} из {{ fbTotalPages }}</span>
          <button @click="fbCurrentPage++" :disabled="fbCurrentPage === fbTotalPages" class="p-2 bg-white border border-slate-200 rounded-xl text-slate-600 hover:text-rose-600 disabled:opacity-50 transition-colors shadow-sm"><ChevronRight class="w-5 h-5"/></button>
        </div>
      </div>
    </div>

    <!-- ─── Лайтбокс ──────────────────────────────────────────────── -->
    <div v-if="lightbox.isOpen" class="fixed inset-0 z-[500] bg-slate-900/95 backdrop-blur-sm flex flex-col animate-in fade-in duration-200">
      <div class="flex justify-between items-center p-6 text-white">
        <span class="text-sm font-black text-slate-400 uppercase tracking-widest">{{ lightbox.index + 1 }} / {{ lightbox.photos.length }}</span>
        <button @click="lightbox.isOpen = false" class="p-3 bg-white/10 hover:bg-red-500 rounded-xl text-white transition-colors"><X class="w-6 h-6"/></button>
      </div>
      <div class="flex-1 flex items-center justify-between px-6 pb-6">
        <button @click="prevPhoto" class="p-4 bg-white/10 hover:bg-white/20 text-white rounded-2xl transition-colors backdrop-blur-md"><ChevronLeft class="w-8 h-8"/></button>
        <div class="max-w-5xl max-h-[85vh] flex items-center justify-center p-4">
          <img :src="lightbox.photos[lightbox.index]" class="max-w-full max-h-full object-contain rounded-2xl shadow-2xl" />
        </div>
        <button @click="nextPhoto" class="p-4 bg-white/10 hover:bg-white/20 text-white rounded-2xl transition-colors backdrop-blur-md"><ChevronRight class="w-8 h-8"/></button>
      </div>
      <div class="h-24 flex items-center justify-center gap-3 overflow-x-auto p-4 mb-4">
        <div v-for="(img, idx) in lightbox.photos" :key="idx" @click="lightbox.index = idx"
             :class="['w-16 h-16 rounded-xl overflow-hidden cursor-pointer border-2 transition-all', lightbox.index === idx ? 'border-indigo-500 scale-110 shadow-lg' : 'border-transparent opacity-50 hover:opacity-100']">
          <img :src="img" class="w-full h-full object-cover" />
        </div>
      </div>
    </div>

  </div>
</template>

<style scoped>
.custom-scroll::-webkit-scrollbar { width: 6px; height: 6px; }
.custom-scroll::-webkit-scrollbar-track { background: transparent; }
.custom-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 10px; }
.custom-scroll::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
</style>
