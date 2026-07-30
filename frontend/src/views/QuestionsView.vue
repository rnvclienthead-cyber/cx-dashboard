<template>
  <div class="min-h-screen bg-gray-50">

    <!-- ══ ШАПКА ══════════════════════════════════════════════════════════ -->
    <div class="bg-white border-b border-gray-200 sticky top-0 z-20">
      <div class="max-w-7xl mx-auto px-5">

        <!-- Строка 1: заголовок + счётчики -->
        <div class="flex items-center justify-between gap-4 py-3">
          <div class="flex items-center gap-3">
            <div class="w-9 h-9 rounded-xl bg-sky-50 flex items-center justify-center text-sky-600 shrink-0">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>
              </svg>
            </div>
            <div>
              <h1 class="text-sm font-medium text-gray-900 leading-tight">Вопросы</h1>
              <p class="text-xs text-gray-400 leading-tight mt-0.5">Wildberries · ЯМаркет</p>
            </div>
          </div>

          <div v-if="activeSection === 'queue' && counts.questions != null && counts.questions > 0"
            class="flex items-center gap-2">
            <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-sky-50 text-sky-700 border border-sky-200">
              <span class="w-1.5 h-1.5 rounded-full bg-sky-500 animate-pulse"></span>
              {{ counts.questions }} вопросов
            </span>
          </div>
        </div>

        <!-- Строка 2: вкладки секций -->
        <div class="flex -mb-px">
          <button v-for="s in sections" :key="s.key"
            @click="switchSection(s.key)"
            :class="['px-4 py-2.5 text-sm font-medium border-b-2 transition-all',
              activeSection === s.key
                ? 'border-sky-500 text-sky-600'
                : 'border-transparent text-gray-500 hover:text-gray-700']">
            {{ s.label }}
          </button>
        </div>
      </div>

      <!-- Строка 3: обновить (только очередь) -->
      <div v-if="activeSection === 'queue'" class="border-t border-gray-100 bg-gray-50/60">
        <div class="max-w-7xl mx-auto px-5 py-2 flex items-center justify-end gap-3">
          <button @click="loadQueue()" class="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1 transition-colors">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
            Обновить
          </button>
        </div>
      </div>
    </div>

    <!-- ══ ОЧЕРЕДЬ ════════════════════════════════════════════════════════ -->
    <div v-if="activeSection === 'queue'" class="max-w-7xl mx-auto px-4 py-4">

      <div v-if="queueLoading" class="flex items-center justify-center py-20">
        <div class="text-center">
          <div class="w-10 h-10 border-4 border-sky-500 border-t-transparent rounded-full animate-spin mx-auto mb-3"></div>
          <p class="text-gray-400 text-sm">Загружаем вопросы…</p>
        </div>
      </div>

      <div v-else-if="filteredItems.length === 0" class="flex flex-col items-center justify-center py-20">
        <div class="text-5xl mb-4">🎉</div>
        <p class="text-gray-500 font-medium">Вопросов нет</p>
        <p class="text-gray-400 text-sm mt-1">Все вопросы обработаны</p>
      </div>

      <div v-else class="flex flex-wrap items-center justify-between gap-3 mb-4">
        <p class="text-sm text-gray-500">
          Показано {{ pagedItems.length }} из {{ filteredItems.length }}
        </p>
        <div class="flex items-center gap-2">
          <button @click="toggleSort"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-sm text-gray-600 hover:border-gray-300 hover:text-gray-800 transition-all">
            <span>{{ sortDesc ? 'Сначала старые' : 'Сначала новые' }}</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path v-if="sortDesc" d="M12 5v14M19 12l-7 7-7-7"/>
              <path v-else d="M12 19V5M5 12l7-7 7 7"/>
            </svg>
          </button>
          <select v-model.number="perPage" @change="currentPage = 1"
            class="border border-gray-200 rounded-lg px-2 py-1.5 text-sm text-gray-600 focus:outline-none focus:ring-1 focus:ring-sky-400">
            <option v-for="n in [10,25,50,100]" :key="n" :value="n">{{ n }} на стр.</option>
          </select>
        </div>
      </div>

      <!-- Карточки -->
      <div v-if="pagedItems.length > 0" class="space-y-2.5">
        <div v-for="item in pagedItems" :key="item.mp + item.item_type + item.id"
          class="bg-white rounded-xl border border-gray-200 hover:border-gray-300 transition-colors overflow-hidden">

          <div class="flex gap-3 p-4">
            <!-- Превью товара -->
            <div class="shrink-0">
              <div v-if="item.product_photo"
                class="w-[72px] h-24 rounded-lg overflow-hidden bg-gray-100 border border-gray-200">
                <img :src="item.product_photo" class="w-full h-full object-cover" />
              </div>
              <div v-else class="w-[72px] h-24 rounded-lg bg-gray-100 border border-gray-200 flex items-center justify-center text-gray-300">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/></svg>
              </div>
            </div>

            <!-- Основной блок -->
            <div class="flex-1 min-w-0">
              <div class="flex items-center justify-between gap-2 mb-1.5">
                <div class="flex items-center gap-2 min-w-0">
                  <span :class="['px-1.5 py-0.5 rounded text-xs font-medium', mpBadgeCls[item.mp]]">{{ item.mp.toUpperCase() }}</span>
                  <span class="px-2 py-0.5 rounded-full text-xs font-medium bg-sky-50 text-sky-600">Вопрос</span>
                  <span v-if="item.contact" class="text-xs text-gray-400 truncate">· {{ item.contact }}</span>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                  <span :class="['text-xs px-2 py-0.5 rounded-full font-medium', ageCls(item.age_sec)]">{{ fmtAge(item.age_sec) }}</span>
                  <button @click="dismissItem(item)" title="В архив без ответа"
                    class="text-gray-300 hover:text-red-400 transition-colors">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
                  </button>
                </div>
              </div>

              <p v-if="item.sku" class="text-xs text-gray-400 mb-1">Арт. {{ item.sku }}</p>
              <p class="text-sm text-gray-700 leading-relaxed">{{ item.body }}</p>
            </div>
          </div>

          <!-- Модерация черновика -->
          <div v-if="item.pending_mod_id" class="border-t border-amber-200 bg-amber-50/70 px-4 py-3 space-y-2">
            <p class="text-xs font-medium text-amber-700">Черновик автоответа ожидает подтверждения</p>
            <p class="text-sm text-gray-700 whitespace-pre-wrap bg-white border border-amber-200 rounded-lg px-3 py-2">{{ item.pending_draft }}</p>
            <div class="flex gap-2">
              <button @click="confirmMod(item)"
                class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg transition-colors font-medium">
                Подтвердить и отправить
              </button>
              <button @click="rejectMod(item)"
                class="px-3 py-1.5 bg-white hover:bg-gray-50 text-gray-600 border border-gray-200 text-sm rounded-lg transition-colors font-medium">
                Отклонить
              </button>
            </div>
          </div>

          <!-- Форма ответа -->
          <div v-else class="border-t border-gray-100 px-4 py-3 bg-gray-50/40">
            <div v-if="relevantTemplates(item).length" class="mb-2 flex flex-wrap gap-1.5">
              <button v-for="t in relevantTemplates(item)" :key="t.id"
                @click="item._reply = t.body"
                class="px-2.5 py-1 bg-white hover:bg-sky-50 text-gray-600 hover:text-sky-700 border border-gray-200 hover:border-sky-200 rounded-full text-xs font-medium transition-colors">
                {{ t.title }}
              </button>
            </div>
            <textarea
              v-model="item._reply"
              placeholder="Написать ответ…"
              rows="2"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-sky-400 focus:border-transparent"
            ></textarea>
            <div class="flex justify-end mt-2">
              <button @click="sendReply(item)"
                :disabled="!item._reply?.trim()"
                :class="['px-4 py-2 rounded-lg text-sm font-medium transition-all',
                  item._reply?.trim()
                    ? 'bg-sky-600 hover:bg-sky-700 text-white'
                    : 'bg-gray-100 text-gray-400 cursor-not-allowed']">
                Опубликовать ответ
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Пагинация -->
      <div v-if="totalPages > 1" class="flex items-center justify-center gap-2 mt-6">
        <button @click="currentPage = Math.max(1, currentPage - 1)"
          :disabled="currentPage === 1"
          class="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed">
          ← Назад
        </button>
        <div class="flex items-center gap-1.5">
          <input type="number" :min="1" :max="totalPages"
            :value="currentPage"
            @change="e => currentPage = Math.min(totalPages, Math.max(1, parseInt(e.target.value) || 1))"
            @keydown.enter="e => e.target.blur()"
            class="w-14 text-center border border-gray-200 rounded-lg px-1 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-sky-400 focus:border-transparent [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none" />
          <span class="text-sm text-gray-400">/ {{ totalPages }}</span>
        </div>
        <button @click="currentPage = Math.min(totalPages, currentPage + 1)"
          :disabled="currentPage === totalPages"
          class="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed">
          Вперёд →
        </button>
      </div>
    </div>

    <!-- ══ ШАБЛОНЫ ══════════════════════════════════════════════════════════ -->
    <div v-if="activeSection === 'templates'" class="max-w-4xl mx-auto px-4 py-4">
      <div class="flex justify-between items-center mb-4">
        <h2 class="font-semibold text-gray-800">Шаблоны ответов</h2>
        <button @click="openTplForm()" class="px-3 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 transition-colors">
          + Создать шаблон
        </button>
      </div>

      <div v-if="tplFormOpen" class="bg-white border border-gray-200 rounded-xl shadow-sm p-5 mb-4">
        <h3 class="font-semibold text-gray-800 mb-4">{{ editTpl.id ? 'Редактировать' : 'Новый шаблон' }}</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div class="md:col-span-2">
            <label class="block text-xs font-medium text-gray-600 mb-1">Название</label>
            <input v-model="editTpl.title" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400" />
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Площадка</label>
            <select v-model="editTpl.mp" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm">
              <option value="all">Все</option>
              <option value="wb">WB</option>
              <option value="ym">ЯМ</option>
            </select>
          </div>
          <div class="md:col-span-2">
            <label class="block text-xs font-medium text-gray-600 mb-1">Текст шаблона</label>
            <textarea v-model="editTpl.body" rows="5"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-indigo-400"></textarea>
          </div>
          <div class="md:col-span-2">
            <label class="block text-xs font-medium text-gray-600 mb-1">Стоп-слова (по одному на строку)</label>
            <textarea v-model="editTpl.stop_words" rows="2" placeholder="брак&#10;возврат"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-indigo-400"></textarea>
          </div>
          <div class="md:col-span-2 flex flex-wrap gap-6">
            <label class="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input type="checkbox" v-model="editTpl.auto_reply" class="w-4 h-4 rounded accent-indigo-600" />
              Автоответ
            </label>
            <label class="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input type="checkbox" v-model="editTpl.requires_confirmation" class="w-4 h-4 rounded accent-amber-500" />
              Требует подтверждения
            </label>
          </div>
        </div>
        <div class="flex gap-2 mt-4">
          <button @click="saveTpl" class="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700">
            {{ editTpl.id ? 'Сохранить' : 'Создать' }}
          </button>
          <button @click="tplFormOpen = false" class="px-4 py-2 bg-white border border-gray-200 rounded-lg text-sm font-medium hover:bg-gray-50">
            Отмена
          </button>
        </div>
      </div>

      <div v-if="tplLoading" class="flex justify-center py-10">
        <div class="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
      </div>
      <div v-else-if="templates.length === 0" class="text-center py-16 text-gray-400">Шаблоны не добавлены</div>
      <div v-else class="space-y-2">
        <div v-for="t in templates" :key="t.id"
          class="bg-white rounded-xl border border-gray-200 shadow-sm px-4 py-3">
          <div class="flex items-start justify-between gap-3">
            <div class="flex-1 min-w-0">
              <div class="flex flex-wrap items-center gap-2 mb-1">
                <span class="font-medium text-gray-800 text-sm">{{ t.title }}</span>
                <span :class="['px-2 py-0.5 rounded text-xs font-bold uppercase', mpBadgeCls[t.mp] || 'bg-gray-100 text-gray-500']">{{ t.mp }}</span>
                <span v-if="t.auto_reply" class="text-xs bg-green-50 text-green-700 border border-green-200 rounded px-1.5">авто</span>
                <span v-if="t.requires_confirmation" class="text-xs bg-amber-50 text-amber-700 border border-amber-200 rounded px-1.5">подтверждение</span>
              </div>
              <p class="text-sm text-gray-500 line-clamp-2">{{ t.body }}</p>
            </div>
            <div class="flex gap-2 shrink-0">
              <button @click="openTplForm(t)" class="text-xs text-indigo-600 hover:underline">Ред.</button>
              <button @click="deleteTpl(t.id)" class="text-xs text-red-500 hover:underline">Удалить</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Toast -->
    <transition name="toast">
      <div v-if="toast" :class="['fixed bottom-6 right-6 z-50 px-4 py-3 rounded-xl shadow-lg text-sm font-medium text-white max-w-sm',
        toast.type === 'error' ? 'bg-red-600' : 'bg-green-600']">
        {{ toast.msg }}
      </div>
    </transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'
import { useToast } from '../composables/useToast'
import { useCxTemplates } from '../composables/useCxTemplates'
import { useCxQueueActions } from '../composables/useCxQueueActions'
import { CX_BASE as BASE, MP_BADGE_CLASSES as mpBadgeCls, fmtAge, ageCls } from '../utils/cx'

const platformStore = usePlatformStore()
const currentMp = computed(() => platformStore.platform || 'all')

const { toast, showToast } = useToast()

// ── Навигация ──────────────────────────────────────────────────────────────
const sections = [
  { key: 'queue',     label: 'Очередь'  },
  { key: 'templates', label: 'Шаблоны'  },
]
const activeSection = ref('queue')

function switchSection(key) {
  activeSection.value = key
}

watch(currentMp, () => {
  if (activeSection.value === 'queue') loadQueue()
})

// ── Очередь ────────────────────────────────────────────────────────────────
const queueLoading = ref(false)
const queueItems   = ref([])
const counts       = ref({})
const perPage      = ref(25)
const currentPage  = ref(1)
const sortDesc     = ref(true)

function toggleSort() {
  sortDesc.value = !sortDesc.value
  currentPage.value = 1
}

const filteredItems = computed(() => {
  let list = (Array.isArray(queueItems.value) ? queueItems.value : [])
    .filter(i => i.item_type === 'question')
  const dir = sortDesc.value ? 1 : -1
  return [...list].sort((a, b) => ((a.age_sec || 0) - (b.age_sec || 0)) * dir)
})

const totalPages = computed(() => Math.max(1, Math.ceil(filteredItems.value.length / perPage.value)))
const pagedItems = computed(() => {
  const start = (currentPage.value - 1) * perPage.value
  return filteredItems.value.slice(start, start + perPage.value)
})

async function loadQueue() {
  queueLoading.value = true
  currentPage.value  = 1
  try {
    const res = await apiFetch(`${BASE}/queue?mp=${currentMp.value}`)
    const data = await res.json()
    queueItems.value = (data.items || []).map(i => ({ ...i, _reply: '' }))
    counts.value = data.counts || {}
  } catch {
    showToast('Ошибка загрузки очереди', 'error')
  } finally {
    queueLoading.value = false
  }
}

// ── Отправка / модерация / dismiss (общая логика — см. composables/useCxQueueActions) ──
function replyEndpoint(item) {
  const id = encodeURIComponent(String(item.id))
  const map = {
    'wb:question': `/wb/questions/${id}/answer`,
    'ym:question': `/ym/questions/${id}/answer`,
  }
  return map[`${item.mp}:${item.item_type}`] || null
}

const { sendReply: _sendReply, confirmMod, rejectMod, dismissItem } = useCxQueueActions(queueItems, showToast)
function sendReply(item) { return _sendReply(item, replyEndpoint) }

// ── Шаблоны (общая логика — см. composables/useCxTemplates) ─────────────────
const {
  templates, tplLoading, tplFormOpen, editTpl,
  loadTemplates, openTplForm, saveTpl, deleteTpl, relevantTemplates,
} = useCxTemplates('question', showToast)

// ── Init ───────────────────────────────────────────────────────────────────
onMounted(async () => {
  await Promise.all([loadQueue(), loadTemplates()])
})
</script>

<style scoped>
.toast-enter-active, .toast-leave-active { transition: all .3s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(16px); }
.line-clamp-2 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
</style>
