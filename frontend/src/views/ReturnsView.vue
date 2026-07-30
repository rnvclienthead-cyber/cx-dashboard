<template>
  <div class="p-6 max-w-7xl mx-auto">
    <div class="flex items-center justify-between mb-6 gap-4 flex-wrap">
      <div>
        <div class="flex items-center gap-2">
          <h1 class="text-2xl font-bold text-slate-800">Возвраты</h1>
          <span class="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-purple-50 text-purple-700 border border-purple-200">Только WB</span>
        </div>
        <p class="text-sm text-slate-500 mt-0.5">
          Претензии Wildberries — одобрить или отказать прямо здесь.
          У Ozon и Яндекс Маркета пока нет интеграции по возвратам, поэтому раздел работает только с WB.
        </p>
      </div>
    </div>

    <!-- Вкладки активные / архив -->
    <div class="flex gap-1 mb-5 bg-slate-100 rounded-lg p-1 w-fit">
      <button
        v-for="tab in tabs"
        :key="String(tab.value)"
        @click="setTab(tab.value)"
        :class="[
          'px-4 py-1.5 rounded-md text-sm font-medium transition-colors',
          activeTab === tab.value
            ? 'bg-white text-slate-800 shadow-sm'
            : 'text-slate-500 hover:text-slate-700'
        ]"
      >{{ tab.label }}</button>
    </div>

    <!-- Фильтры -->
    <div class="flex flex-wrap gap-3 mb-5">
      <select v-model="filterStatus" @change="loadData(1)"
        class="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-400">
        <option value="">Все статусы</option>
        <option v-for="(label, val) in STATUS_FILTER" :key="val" :value="val">{{ label }}</option>
      </select>

      <select v-model="filterType" @change="loadData(1)"
        class="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-400">
        <option value="">Все типы</option>
        <option v-for="(label, val) in TYPE_MAP" :key="val" :value="val">{{ label }}</option>
      </select>

      <input
        v-model="searchQ"
        @keyup.enter="loadData(1)"
        type="search"
        placeholder="Артикул, заказ, комментарий…"
        class="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-400 min-w-[220px]"
      />
      <button @click="loadData(1)"
        class="text-sm px-3 py-2 rounded-lg bg-slate-800 text-white hover:bg-slate-700">Найти</button>

      <div class="ml-auto text-sm text-slate-500 self-center" v-if="total > 0">
        Найдено: <strong>{{ total }}</strong>
      </div>
    </div>

    <div class="flex gap-4 items-start">
      <!-- Список -->
      <div class="flex-1 min-w-0">
        <div v-if="loading" class="flex justify-center py-16">
          <div class="animate-spin rounded-full h-8 w-8 border-2 border-emerald-500 border-t-transparent"></div>
        </div>

        <div v-else-if="items.length === 0" class="text-center py-16 text-slate-400">
          <PackageX class="w-10 h-10 mx-auto mb-3 opacity-30" />
          <p>Возвратов не найдено</p>
        </div>

        <div v-else class="space-y-3">
          <div
            v-for="item in items"
            :key="item.claim_id || item.srid"
            @click="selectItem(item)"
            :class="[
              'bg-white rounded-xl border p-4 cursor-pointer transition-colors',
              selectedId === item.claim_id
                ? 'border-emerald-400 ring-2 ring-emerald-100'
                : 'border-slate-200 hover:border-slate-300'
            ]"
          >
            <div class="flex items-start gap-4 flex-wrap">
              <div v-if="item.product_photo" class="shrink-0 w-[78px] h-[105px] rounded-lg overflow-hidden bg-slate-100 border border-slate-200">
                <img :src="item.product_photo" class="w-full h-full object-cover" loading="lazy" />
              </div>

              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-2 mb-1.5">
                  <span class="font-medium text-slate-800 text-sm">{{ item.claim_type_label || '—' }}</span>
                  <span :class="['text-xs font-semibold px-2 py-0.5 rounded-full', statusClass(item.status_label || item.status)]">
                    {{ item.status_label || item.status || '—' }}
                  </span>
                </div>

                <div class="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-slate-500">
                  <span v-if="item.supplier_article">Артикул: <strong class="text-slate-700">{{ item.supplier_article }}</strong></span>
                  <span v-if="item.nm_id">nm_id: <strong class="text-slate-700">{{ item.nm_id }}</strong></span>
                  <span v-if="item.srid" class="font-mono truncate max-w-[220px]">{{ item.srid }}</span>
                </div>

                <p v-if="item.user_comment" class="mt-2 text-sm text-slate-600 leading-snug line-clamp-3">
                  {{ item.user_comment }}
                </p>
              </div>

              <div class="text-right flex-shrink-0">
                <div class="text-sm text-slate-500">{{ ruDate(item.claim_date) }}</div>
                <div v-if="item.order_date" class="text-xs text-slate-400 mt-0.5">
                  Заказ: {{ ruDate(item.order_date) }}
                </div>
              </div>
            </div>

            <div v-if="item.photos && item.photos.length" class="flex flex-wrap gap-2 mt-3">
              <button
                v-for="(photo, idx) in item.photos.slice(0, 6)"
                :key="idx"
                type="button"
                @click.stop="openLightbox(item.photos, idx)"
                class="block w-[78px] h-[105px] rounded-lg overflow-hidden border border-slate-200 hover:border-emerald-400 transition-colors flex-shrink-0"
              >
                <img :src="photo" class="w-full h-full object-cover" loading="lazy" />
              </button>
              <button
                v-if="item.photos.length > 6"
                type="button"
                @click.stop="openLightbox(item.photos, 6)"
                class="self-center text-xs text-slate-400 hover:text-emerald-600"
              >
                +{{ item.photos.length - 6 }} фото
              </button>
            </div>
          </div>
        </div>

        <div v-if="totalPages > 1" class="flex justify-center gap-2 mt-6">
          <button
            v-for="p in visiblePages"
            :key="p"
            @click="loadData(p)"
            :class="[
              'w-8 h-8 rounded-lg text-sm font-medium transition-colors',
              p === page
                ? 'bg-emerald-500 text-white'
                : 'bg-white border border-slate-200 text-slate-600 hover:border-emerald-400'
            ]"
          >{{ p }}</button>
        </div>
      </div>

      <!-- Панель решения -->
      <aside
        v-if="selected"
        class="w-full max-w-md shrink-0 sticky top-20 bg-white border border-slate-200 rounded-xl p-4 shadow-sm"
      >
        <div class="flex items-start justify-between gap-2 mb-3">
          <div>
            <div class="text-sm font-semibold text-slate-800">{{ selected.claim_type_label || 'Возврат' }}</div>
            <div :class="['text-xs font-semibold mt-0.5', statusTextClass(selected.status_label)]">
              {{ selected.status_label || selected.status }}
            </div>
          </div>
          <button @click="closePanel" class="text-slate-300 hover:text-slate-500 text-xl leading-none">×</button>
        </div>

        <div class="text-xs text-slate-500 space-y-0.5 mb-3">
          <div v-if="selected.supplier_article">Артикул: <strong class="text-slate-700">{{ selected.supplier_article }}</strong></div>
          <div v-if="selected.nm_id">nm_id: {{ selected.nm_id }}</div>
          <div v-if="selected.srid" class="font-mono break-all">{{ selected.srid }}</div>
          <div v-if="selected.claim_date">Дата: {{ ruDate(selected.claim_date) }}</div>
        </div>

        <p class="text-sm text-slate-700 leading-relaxed mb-3 whitespace-pre-wrap">{{ selected.user_comment || 'Комментарий не указан' }}</p>

        <div v-if="selected.photos?.length" class="flex flex-wrap gap-2 mb-3">
          <button
            v-for="(photo, idx) in selected.photos.slice(0, 8)"
            :key="idx"
            type="button"
            @click="openLightbox(selected.photos, idx)"
            class="block w-16 h-20 rounded-lg overflow-hidden border border-slate-200 hover:border-emerald-400"
          >
            <img :src="photo" class="w-full h-full object-cover" />
          </button>
        </div>

        <div v-if="selected.status_ex" class="mb-3 text-xs text-slate-500 bg-slate-50 rounded-lg px-2.5 py-2 whitespace-pre-wrap">
          {{ selected.status_ex }}
        </div>

        <div v-if="detailLoading" class="text-xs text-slate-400 py-2">Загружаем действия WB…</div>

        <div v-else-if="primaryActions.length" class="space-y-2 border-t border-slate-100 pt-3">
          <div class="text-xs font-semibold text-slate-600 mb-1">
            {{ selected.can_change_decision ? 'Изменить решение' : 'Решение' }}
          </div>
          <button
            v-for="a in primaryActions"
            :key="a.action"
            :disabled="deciding"
            @click="onActionClick(a)"
            :class="[
              'w-full text-sm font-semibold px-3 py-2.5 rounded-lg border transition-colors disabled:opacity-50 text-left',
              a.kind === 'approve_keep' ? 'bg-emerald-600 text-white border-emerald-600 hover:bg-emerald-700' :
              a.kind === 'approve_return' ? 'bg-white text-emerald-800 border-emerald-300 hover:bg-emerald-50' :
              'bg-white text-rose-700 border-rose-300 hover:bg-rose-50'
            ]"
          >{{ a.label }}</button>

          <div v-if="rejectOpen" class="space-y-2 pt-1">
            <textarea v-model="rejectComment" rows="3"
              placeholder="Причина отказа для покупателя (обязательно, от 10 символов)"
              class="w-full text-sm border border-rose-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-rose-300"></textarea>
            <div class="flex gap-2">
              <button :disabled="deciding" @click="submitDecision('rejectcustom', rejectComment)"
                class="flex-1 text-sm font-semibold px-3 py-2 rounded-lg bg-rose-600 text-white hover:bg-rose-700 disabled:opacity-50">
                Отправить отказ
              </button>
              <button @click="rejectOpen=false" class="text-sm px-3 py-2 rounded-lg text-slate-500 hover:bg-slate-50">Отмена</button>
            </div>
          </div>
          <p v-if="decisionError" class="text-xs text-rose-600">{{ decisionError }}</p>
        </div>
        <div v-else class="text-xs text-slate-500 border-t border-slate-100 pt-3">
          По этой заявке сейчас нельзя принять решение (уже закрыта или нет действий в WB).
        </div>

        <div class="mt-4 flex flex-col gap-2 border-t border-slate-100 pt-3">
          <button
            v-if="selected.chat?.chat_id"
            @click="openChat(selected.chat.chat_id)"
            class="w-full text-sm font-semibold px-3 py-2.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 text-left"
          >Открыть связанный чат →</button>
          <p v-else class="text-xs text-slate-400">Связанный чат не найден</p>
          <a
            v-if="selected.claim_id"
            :href="`https://seller.wildberries.ru/suppliers-returns`"
            target="_blank"
            rel="noopener"
            class="text-xs text-slate-400 hover:text-slate-600"
          >Открыть раздел возвратов в ЛК WB</a>
        </div>
      </aside>
    </div>

    <!-- Лайтбокс фото претензии -->
    <div v-if="lightbox.show" @click="closeLightbox"
      class="fixed inset-0 z-[70] bg-black/80 flex items-center justify-center p-4">
      <button type="button" @click.stop="closeLightbox"
        class="absolute top-4 right-5 text-white/80 hover:text-white text-4xl leading-none">×</button>
      <button v-if="lightbox.photos.length > 1" type="button" @click.stop="prevPhoto"
        class="absolute left-4 md:left-10 text-white/70 hover:text-white text-5xl leading-none select-none">‹</button>
      <img :src="lightbox.photos[lightbox.index]" @click.stop
        class="max-w-full max-h-[85vh] rounded-lg object-contain shadow-2xl" />
      <button v-if="lightbox.photos.length > 1" type="button" @click.stop="nextPhoto"
        class="absolute right-4 md:right-10 text-white/70 hover:text-white text-5xl leading-none select-none">›</button>
      <span v-if="lightbox.photos.length > 1"
        class="absolute bottom-5 left-1/2 -translate-x-1/2 text-white/80 text-sm bg-black/40 rounded-full px-3 py-1">
        {{ lightbox.index + 1 }} / {{ lightbox.photos.length }}
      </span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { PackageX } from 'lucide-vue-next'
import { apiFetch } from '../api'

const route = useRoute()
const router = useRouter()

const STATUS_FILTER = {
  'На рассмотрении': 'На рассмотрении',
  'Одобрено': 'Одобрено',
  'Отказ': 'Отказ',
  'Закрыта': 'Закрыта',
}
const TYPE_MAP = { '1': 'Брак', '2': 'Не то, что заказывали', '3': 'Недостача', '4': 'Другое', '5': 'Недовложение' }

const tabs = [
  { label: 'Активные', value: true },
  { label: 'Архив', value: false },
]

const activeTab    = ref(true)
const filterStatus = ref('На рассмотрении')
const filterType   = ref('')
const searchQ      = ref('')
const items        = ref([])
const total        = ref(0)
const page         = ref(1)
const totalPages   = ref(1)
const loading      = ref(false)

const selectedId     = ref('')
const selected       = ref(null)
const detailLoading  = ref(false)
const deciding       = ref(false)
const rejectOpen     = ref(false)
const rejectComment  = ref('')
const decisionError  = ref('')

const lightbox = ref({ show: false, photos: [], index: 0 })

function openLightbox(photos, index = 0) {
  const list = (photos || []).filter(Boolean)
  if (!list.length) return
  lightbox.value = { show: true, photos: list, index: Math.max(0, Math.min(index, list.length - 1)) }
}
function closeLightbox() {
  lightbox.value = { show: false, photos: [], index: 0 }
}
function prevPhoto() {
  const n = lightbox.value.photos.length
  if (n < 2) return
  lightbox.value.index = (lightbox.value.index - 1 + n) % n
}
function nextPhoto() {
  const n = lightbox.value.photos.length
  if (n < 2) return
  lightbox.value.index = (lightbox.value.index + 1) % n
}

function onLightboxKey(e) {
  if (!lightbox.value.show) return
  if (e.key === 'Escape') closeLightbox()
  if (e.key === 'ArrowLeft') prevPhoto()
  if (e.key === 'ArrowRight') nextPhoto()
}

const primaryActions = computed(() =>
  (selected.value?.actions || []).filter(a => a.primary)
)

const visiblePages = computed(() => {
  const all = []
  for (let i = 1; i <= totalPages.value; i++) all.push(i)
  if (all.length <= 9) return all
  const cur = page.value
  const tp  = totalPages.value
  const set = new Set([1, 2, cur - 1, cur, cur + 1, tp - 1, tp].filter(p => p >= 1 && p <= tp))
  return [...set].sort((a, b) => a - b)
})

function statusClass(status) {
  const s = String(status || '')
  if (s === 'На рассмотрении' || s === '0') return 'bg-yellow-100 text-yellow-700'
  if (s === 'Одобрено' || s === '2') return 'bg-green-100 text-green-700'
  if (s === 'Отказ' || s === 'Отклонено' || s === '1') return 'bg-red-100 text-red-700'
  return 'bg-slate-100 text-slate-600'
}

function statusTextClass(label) {
  if (label === 'Одобрено') return 'text-emerald-600'
  if (['Отказ', 'Закрыта', 'Отклонено'].includes(label)) return 'text-slate-600'
  return 'text-orange-600'
}

function ruDate(d) {
  if (!d) return ''
  const months = ['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек']
  try {
    const dt = new Date(d)
    return `${dt.getDate()} ${months[dt.getMonth()]} ${dt.getFullYear()}`
  } catch { return String(d).slice(0, 10) }
}

async function loadData(p = 1) {
  loading.value = true
  page.value = p
  try {
    const params = new URLSearchParams({
      active: activeTab.value,
      page: p,
      per_page: 25,
    })
    if (filterStatus.value) params.set('status', filterStatus.value)
    if (filterType.value)   params.set('type', filterType.value)
    if (searchQ.value.trim()) params.set('q', searchQ.value.trim())
    const res  = await apiFetch(`/api/v1/cx-responses/claims?${params}`)
    const data = await res.json()
    items.value      = data.items || []
    total.value      = data.total || 0
    totalPages.value = data.total_pages || 1
  } catch (e) {
    console.error('ReturnsView load error:', e)
  } finally {
    loading.value = false
  }
}

function setTab(val) {
  activeTab.value = val
  filterStatus.value = val ? 'На рассмотрении' : ''
  filterType.value = ''
  closePanel()
  loadData(1)
}

function closePanel() {
  selectedId.value = ''
  selected.value = null
  rejectOpen.value = false
  rejectComment.value = ''
  decisionError.value = ''
}

async function selectItem(item) {
  if (!item?.claim_id) return
  selectedId.value = item.claim_id
  selected.value = { ...item, actions: [] }
  rejectOpen.value = false
  rejectComment.value = ''
  decisionError.value = ''
  detailLoading.value = true
  try {
    const r = await apiFetch(`/api/v1/cx-responses/claims/${encodeURIComponent(item.claim_id)}`)
    if (r.ok) {
      selected.value = await r.json()
    }
  } catch (e) {
    decisionError.value = e.message || 'Ошибка загрузки'
  } finally {
    detailLoading.value = false
  }
}

function onActionClick(a) {
  decisionError.value = ''
  if (a.kind === 'reject' || a.action === 'rejectcustom') {
    rejectOpen.value = true
    return
  }
  if (!confirm(`${a.label}?\n\nРешение уйдёт в Wildberries сразу.`)) return
  submitDecision(a.action, '')
}

async function submitDecision(action, comment) {
  const claimId = selected.value?.claim_id
  if (!claimId || !action) return
  if (action === 'rejectcustom' && String(comment || '').trim().length < 10) {
    decisionError.value = 'Укажите причину отказа (не короче 10 символов)'
    return
  }
  deciding.value = true
  decisionError.value = ''
  try {
    const r = await apiFetch(`/api/v1/cx-responses/claims/${encodeURIComponent(claimId)}/decision`, {
      method: 'POST',
      body: JSON.stringify({ action, comment: comment || '' }),
    })
    const d = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(d.detail || d.error || 'Не удалось отправить решение')
    rejectOpen.value = false
    await selectItem({ claim_id: claimId })
    await loadData(page.value)
  } catch (e) {
    decisionError.value = e.message || 'Ошибка'
  } finally {
    deciding.value = false
  }
}

function openChat(chatId) {
  router.push({ path: '/chats', query: { chat: chatId } })
}

async function openFromQuery() {
  const claim = route.query.claim ? String(route.query.claim) : ''
  const srid = route.query.srid ? String(route.query.srid) : ''
  const tabHint = String(route.query.tab || '')

  let detail = null
  if (claim) {
    try {
      const r = await apiFetch(`/api/v1/cx-responses/claims/${encodeURIComponent(claim)}`)
      if (r.ok) detail = await r.json()
    } catch { /* ignore */ }
  }

  // Какую вкладку открыть
  let useArchive = false
  if (tabHint === 'archive') useArchive = true
  else if (tabHint === 'active') useArchive = false
  else if (detail) useArchive = !!detail.is_archive
  else useArchive = false

  activeTab.value = !useArchive
  filterStatus.value = ''
  filterType.value = ''
  searchQ.value = claim || srid || ''

  await loadData(1)

  if (claim) {
    let found = items.value.find(i => i.claim_id === claim)
    // Если не нашли в выбранной вкладке — пробуем другую
    if (!found) {
      activeTab.value = !activeTab.value
      filterStatus.value = ''
      await loadData(1)
      found = items.value.find(i => i.claim_id === claim)
    }
    if (found) {
      await selectItem(found)
    } else if (detail) {
      // Карточка всё равно показывается, даже если список пуст
      if (detail.is_archive) activeTab.value = false
      selectedId.value = claim
      selected.value = detail
    } else {
      await selectItem({ claim_id: claim })
    }
    return
  }

  if (srid) {
    let found = items.value.find(i => i.srid === srid)
    if (!found) {
      activeTab.value = !activeTab.value
      filterStatus.value = ''
      await loadData(1)
      found = items.value.find(i => i.srid === srid)
    }
    if (found) await selectItem(found)
  }
}

onMounted(async () => {
  window.addEventListener('keydown', onLightboxKey)
  if (route.query.claim || route.query.srid) {
    await openFromQuery()
  } else {
    await loadData(1)
  }
})

onUnmounted(() => {
  window.removeEventListener('keydown', onLightboxKey)
})

watch(() => [route.query.claim, route.query.srid, route.query.tab], () => {
  if (route.query.claim || route.query.srid) openFromQuery()
})
</script>
