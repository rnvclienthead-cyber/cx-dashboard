<template>
  <div class="p-6 max-w-7xl mx-auto">
    <div class="flex items-center justify-between mb-6 gap-4 flex-wrap">
      <div>
        <div class="flex items-center gap-2 flex-wrap">
          <h1 class="text-2xl font-bold text-slate-800">Возвраты</h1>
          <span class="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 border border-slate-200">
            {{ mpLabel }}
          </span>
        </div>
        <p class="text-sm text-slate-500 mt-0.5">{{ mpHint }}</p>
      </div>
      <!-- При «все МП» в сайдбаре — локальный выбор площадки (очередь возвратов per-MP) -->
      <div v-if="platformStore.platform === 'all'" class="flex gap-1 bg-slate-100 rounded-lg p-1">
        <button
          v-for="p in ['wb', 'ym', 'ozon']"
          :key="p"
          @click="setReturnsMp(p)"
          :class="[
            'px-3 py-1.5 rounded-md text-xs font-semibold uppercase transition-colors',
            currentMp === p ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
          ]"
        >{{ { wb: 'WB', ym: 'ЯМ', ozon: 'Ozon' }[p] }}</button>
      </div>
    </div>

    <!-- Вкладки -->
    <div class="flex gap-1 mb-5 bg-slate-100 rounded-lg p-1 w-fit flex-wrap">
      <button
        v-for="tab in tabs"
        :key="String(tab.value)"
        @click="setTab(tab.value)"
        :class="[
          'px-4 py-1.5 rounded-md text-sm font-medium transition-colors',
          String(activeTab) === String(tab.value)
            ? 'bg-white text-slate-800 shadow-sm'
            : 'text-slate-500 hover:text-slate-700'
        ]"
      >{{ tab.label }}</button>
    </div>

    <!-- Фильтры -->
    <div class="flex flex-wrap gap-3 mb-5">
      <select v-if="currentMp !== 'ozon'" v-model="filterStatus" @change="loadData(1)"
        class="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-400">
        <option value="">Все статусы</option>
        <option v-for="(label, val) in statusFilter" :key="val" :value="val">{{ label }}</option>
      </select>

      <select v-if="currentMp === 'wb'" v-model="filterType" @change="loadData(1)"
        class="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-400">
        <option value="">Все типы</option>
        <option v-for="(label, val) in TYPE_MAP" :key="val" :value="val">{{ label }}</option>
      </select>

      <select v-if="currentMp === 'ozon'" v-model="filterType" @change="loadData(1)"
        class="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-400">
        <option value="">Все типы</option>
        <option value="ClientReturn">Возврат покупателя</option>
        <option value="Cancellation">Невыкуп / отмена</option>
      </select>

      <input
        v-model="searchQ"
        @keyup.enter="loadData(1)"
        type="search"
        :placeholder="currentMp === 'ozon' ? 'Артикул, отправление, штрихкод…' : 'Артикул, заказ, комментарий…'"
        class="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-400 min-w-[220px]"
      />
      <button @click="loadData(1)"
        class="text-sm px-3 py-2 rounded-lg bg-slate-800 text-white hover:bg-slate-700">Найти</button>

      <div class="ml-auto text-sm text-slate-500 self-center" v-if="total > 0">
        Найдено: <strong>{{ total }}</strong>
      </div>
    </div>

    <p v-if="currentMp === 'wb'" class="text-[11px] text-slate-400 mb-4 -mt-2">
      «Нет в API WB» — заявка ушла из окна API WB (локальный архив, не статус площадки).
      «Отменена» — отмена по данным WB. Подробности — в блоке status_ex карточки.
    </p>

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
                  <span v-if="currentMp === 'ozon' && item.schema_code"
                    class="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-100">{{ item.schema_code }}</span>
                  <span v-if="currentMp === 'ozon' && item.return_type_label"
                    class="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-slate-50 text-slate-600 border border-slate-200">{{ item.return_type_label }}</span>
                  <span class="font-medium text-slate-800 text-sm">{{ currentMp === 'ozon' ? (item.return_reason || item.claim_type_label) : (item.claim_type_label || '—') }}</span>
                  <span :class="['text-xs font-semibold px-2 py-0.5 rounded-full', statusClass(item.status_label || item.status)]">
                    {{ item.status_label || item.status || '—' }}
                  </span>
                </div>

                <div class="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-slate-500">
                  <span v-if="item.supplier_article">Артикул: <strong class="text-slate-700">{{ item.supplier_article }}</strong></span>
                  <span v-if="item.nm_id && currentMp === 'wb'">nm_id: <strong class="text-slate-700">{{ item.nm_id }}</strong></span>
                  <span v-if="item.srid" class="font-mono truncate max-w-[220px]">{{ item.srid }}</span>
                  <span v-if="currentMp === 'ozon' && item.target_place_name" class="truncate max-w-[240px]">→ {{ item.target_place_name }}</span>
                  <span v-if="currentMp === 'ozon' && item.price">{{ Number(item.price).toLocaleString('ru-RU') }} ₽</span>
                </div>

                <p v-if="item.user_comment && currentMp !== 'ozon'" class="mt-2 text-sm text-slate-600 leading-snug line-clamp-3">
                  {{ item.user_comment }}
                </p>
                <p v-else-if="currentMp === 'ozon' && item.product_name" class="mt-2 text-sm text-slate-600 leading-snug line-clamp-2">
                  {{ item.product_name }}
                </p>
              </div>

              <div class="text-right flex-shrink-0">
                <div class="text-sm text-slate-500">{{ ruDate(item.claim_date) }}</div>
                <div v-if="item.order_date" class="text-xs text-slate-400 mt-0.5">
                  Заказ: {{ ruDate(item.order_date) }}
                </div>
                <div v-if="item.needs_cabinet_decision" class="mt-1 text-[10px] font-bold text-amber-700 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5">
                  Ответ в ЛК
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
            <p v-else-if="currentMp === 'ozon'" class="mt-2 text-[11px] text-slate-400">Фото покупателя у FBO в API нет</p>
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
            <div class="text-sm font-semibold text-slate-800">
              <template v-if="currentMp === 'ozon'">{{ selected.return_reason || selected.claim_type_label || 'Возврат' }}</template>
              <template v-else>{{ selected.claim_type_label || 'Возврат' }}</template>
            </div>
            <div class="flex flex-wrap items-center gap-1.5 mt-1">
              <span v-if="selected.schema_code" class="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-blue-50 text-blue-700">{{ selected.schema_code }}</span>
              <span v-if="selected.return_type_label" class="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-slate-50 text-slate-600">{{ selected.return_type_label }}</span>
              <span :class="['text-xs font-semibold', statusTextClass(selected.status_label)]">
                {{ selected.status_label || selected.status }}
              </span>
            </div>
          </div>
          <button @click="closePanel" class="text-slate-300 hover:text-slate-500 text-xl leading-none">×</button>
        </div>

        <div class="text-xs text-slate-500 space-y-0.5 mb-3">
          <div v-if="selected.supplier_article">Артикул: <strong class="text-slate-700">{{ selected.supplier_article }}</strong></div>
          <div v-if="selected.product_name" class="text-slate-600 leading-snug">{{ selected.product_name }}</div>
          <div v-if="selected.nm_id && currentMp === 'wb'">nm_id: {{ selected.nm_id }}</div>
          <div v-if="selected.nm_id && currentMp === 'ozon'">SKU: {{ selected.nm_id }}</div>
          <div v-if="selected.posting_number" class="font-mono break-all">Отправление: {{ selected.posting_number }}</div>
          <div v-else-if="selected.srid" class="font-mono break-all">{{ selected.srid }}</div>
          <div v-if="selected.barcode">Штрихкод возврата: <span class="font-mono">{{ selected.barcode }}</span></div>
          <div v-if="selected.price != null">Цена: <strong class="text-slate-700">{{ Number(selected.price).toLocaleString('ru-RU') }} ₽</strong></div>
          <div v-if="selected.place_name">Сейчас: {{ selected.place_name }}</div>
          <div v-if="selected.target_place_name">Куда едет: {{ selected.target_place_name }}</div>
          <div v-if="selected.claim_date">Дата: {{ ruDate(selected.claim_date) }}</div>
          <div v-if="selected.status_changed_at">Статус обновлён: {{ ruDate(selected.status_changed_at) }}</div>
          <div v-if="selected.claim_id && currentMp === 'ozon'" class="text-slate-400">ID заявки: {{ selected.claim_id }}</div>
        </div>

        <p v-if="currentMp !== 'ozon'" class="text-sm text-slate-700 leading-relaxed mb-3 whitespace-pre-wrap">{{ selected.user_comment || 'Комментарий не указан' }}</p>

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
        <p v-else-if="currentMp === 'ozon'" class="mb-3 text-[11px] text-amber-700 bg-amber-50 border border-amber-100 rounded-lg px-2.5 py-2">
          {{ selected.api_limits_note || selected.no_photos_note || 'Фото/комментарий/история — только в ЛК Ozon' }}
        </p>

        <div v-if="selected.status_ex && currentMp !== 'ozon'" class="mb-3 text-xs text-slate-600 bg-slate-50 border border-slate-100 rounded-lg px-2.5 py-2 whitespace-pre-wrap">
          <div class="font-semibold text-slate-500 mb-0.5">Расшифровка статуса (WB)</div>
          {{ selected.status_ex }}
        </div>

        <div v-if="detailLoading" class="text-xs text-slate-400 py-2">Загружаем действия…</div>

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
            <select v-if="rejectReasons.length" v-model="rejectReason"
              class="w-full text-sm border border-rose-200 rounded-lg px-3 py-2 bg-white">
              <option value="">Причина отказа</option>
              <option v-for="r in rejectReasons" :key="r.value" :value="r.value">{{ r.label }}</option>
            </select>
            <textarea v-model="rejectComment" rows="3"
              :placeholder="rejectPlaceholder"
              class="w-full text-sm border border-rose-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-rose-300"></textarea>
            <div class="flex gap-2">
              <button :disabled="deciding" @click="submitReject"
                class="flex-1 text-sm font-semibold px-3 py-2 rounded-lg bg-rose-600 text-white hover:bg-rose-700 disabled:opacity-50">
                Отправить отказ
              </button>
              <button @click="rejectOpen=false" class="text-sm px-3 py-2 rounded-lg text-slate-500 hover:bg-slate-50">Отмена</button>
            </div>
          </div>
          <p v-if="decisionError" class="text-xs text-rose-600">{{ decisionError }}</p>
        </div>
        <div v-else class="text-xs text-slate-500 border-t border-slate-100 pt-3 space-y-2">
          <p>{{ selected.decision_note || emptyActionsText }}</p>
          <a
            v-if="selected.needs_cabinet_decision && selected.cabinet_url"
            :href="selected.cabinet_url"
            target="_blank"
            rel="noopener"
            class="inline-flex w-full items-center justify-center text-sm font-semibold px-3 py-2.5 rounded-lg bg-amber-500 text-white hover:bg-amber-600"
          >Открыть в ЛК Ozon →</a>
          <p v-if="selected.needs_cabinet_decision" class="text-[11px] text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-2.5 py-2">
            Лимит срока ответа Seller API не отдаёт — смотрите таймер на карточке в кабинете Ozon.
          </p>
        </div>

        <div class="mt-4 flex flex-col gap-2 border-t border-slate-100 pt-3">
          <button
            v-if="selected.chat?.chat_id"
            @click="openChat(selected.chat.chat_id)"
            class="w-full text-sm font-semibold px-3 py-2.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 text-left"
          >Открыть связанный чат →</button>
          <button
            v-else
            :disabled="openingChat"
            @click="openOrCreateChat"
            class="w-full text-sm font-semibold px-3 py-2.5 rounded-lg bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50 text-left"
          >{{ openingChat ? 'Открываем…' : 'Чат с покупателем' }}</button>
          <p v-if="chatHint" class="text-xs text-slate-500 leading-snug">{{ chatHint }}</p>
          <p v-if="chatError" class="text-xs text-rose-600 leading-snug">{{ chatError }}</p>
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
import { usePlatformStore } from '../stores/platform'

const route = useRoute()
const router = useRouter()
const platformStore = usePlatformStore()

const TYPE_MAP = { '1': 'Брак', '2': 'Не то, что заказывали', '3': 'Недостача', '4': 'Другое', '5': 'Недовложение' }

const STATUS_FILTER_WB = {
  'На рассмотрении': 'На рассмотрении',
  'Одобрено': 'Одобрено',
  'Отказ': 'Отказ',
  'Отменена': 'Отменена',
  'Закрыта': 'Нет в API WB',
}
const STATUS_FILTER_YM = {
  'На рассмотрении': 'На рассмотрении',
  'Одобрено': 'Одобрено',
  'Отказ': 'Отказ',
  'Отменено': 'Отменено',
}
const STATUS_FILTER_OZON = {
  'На согласовании': 'На согласовании',
  'Ожидает отправки': 'Ожидает отправки',
  'Едет на склад Ozon': 'Едет на склад Ozon',
  'На складе Ozon': 'На складе Ozon',
  'Утилизирован': 'Утилизирован',
}

const OZON_TABS = [
  { label: 'В пути', value: 'transit' },
  { label: 'На согласовании', value: 'approval' },
  { label: 'Получены', value: 'received' },
  { label: 'Решённые', value: 'resolved' },
  { label: 'Утилизация', value: 'utilization' },
  { label: 'Все', value: 'all' },
]
const WB_YM_TABS = [
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
const rejectReason   = ref('')
const rejectAction   = ref('')
const decisionError  = ref('')

const lightbox = ref({ show: false, photos: [], index: 0 })
const openingChat = ref(false)
const chatError = ref('')

const currentMp = computed(() => {
  const q = String(route.query.mp || '').toLowerCase()
  if (['wb', 'ym', 'ozon'].includes(q)) return q
  const p = platformStore.platform
  if (['wb', 'ym', 'ozon'].includes(p)) return p
  // «все МП»: без mp в URL держим последнюю конкретную (по умолчанию wb)
  return 'wb'
})

const chatHint = computed(() => {
  if (selected.value?.chat?.chat_id) return ''
  if (currentMp.value === 'wb') {
    return 'WB API не умеет открывать чат — только покупатель. Ищем уже существующий по заказу; иначе пишите через ЛК «Чат с покупателем».'
  }
  if (currentMp.value === 'ym') {
    return 'ЯМ: создадим чат по возврату (или заказу), если его ещё нет.'
  }
  return 'Ozon: создадим чат по отправлению (нужен Premium Plus).'
})

const tabs = computed(() => currentMp.value === 'ozon' ? OZON_TABS : WB_YM_TABS)

const mpLabel = computed(() => ({ wb: 'WB', ym: 'ЯМ', ozon: 'Ozon' }[currentMp.value] || 'WB'))
const mpHint = computed(() => {
  if (currentMp.value === 'ym') {
    return 'Возвраты Яндекс Маркета — одобрить без возврата товара, с возвратом или отказать.'
  }
  if (currentMp.value === 'ozon') {
    return 'Возвраты Ozon FBO: подробная очередь + ссылка в ЛК. Одобрить/отказать и фото покупателя API не даёт.'
  }
  return 'Претензии Wildberries — одобрить или отказать прямо здесь.'
})
const statusFilter = computed(() => {
  if (currentMp.value === 'ym') return STATUS_FILTER_YM
  if (currentMp.value === 'ozon') return STATUS_FILTER_OZON
  return STATUS_FILTER_WB
})
const emptyActionsText = computed(() => {
  if (currentMp.value === 'ozon') return 'По FBO решение принимает Ozon. Полная карточка и спор — в ЛК.'
  if (currentMp.value === 'ym') return 'По этой заявке сейчас нельзя принять решение в ЯМ.'
  return 'По этой заявке сейчас нельзя принять решение (уже закрыта или нет действий в WB).'
})
const rejectPlaceholder = computed(() =>
  currentMp.value === 'wb'
    ? 'Причина отказа для покупателя (обязательно, от 10 символов)'
    : 'Причина отказа для покупателя (обязательно)'
)

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
const rejectReasons = computed(() => {
  const a = primaryActions.value.find(x => x.kind === 'reject' || x.action === rejectAction.value)
  return a?.reason_types || []
})

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
  if (s === 'На рассмотрении' || s === 'На согласовании' || s === '0') return 'bg-yellow-100 text-yellow-700'
  if (s === 'Одобрено' || s === '2') return 'bg-green-100 text-green-700'
  if (s === 'Отказ' || s === 'Отклонено' || s === '1') return 'bg-red-100 text-red-700'
  if (s === 'Отменена' || s === 'Отменено') return 'bg-slate-200 text-slate-700'
  if (s === 'Нет в API WB' || s === 'Закрыта') return 'bg-slate-100 text-slate-500'
  return 'bg-slate-100 text-slate-600'
}

function statusTextClass(label) {
  if (label === 'Одобрено') return 'text-emerald-600'
  if (['Отказ', 'Закрыта', 'Нет в API WB', 'Отклонено', 'Отменено', 'Отменена'].includes(label)) return 'text-slate-600'
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

function defaultStatusForMp(mp, active) {
  if (mp === 'ozon') return ''
  if (!active) return ''
  return 'На рассмотрении'
}

function defaultTabForMp(mp) {
  // Очередь согласования — приоритет: там нужен ответ продавца в ЛК
  if (mp === 'ozon') return 'approval'
  return true
}

async function loadData(p = 1) {
  loading.value = true
  page.value = p
  try {
    const params = new URLSearchParams({
      mp: currentMp.value,
      page: p,
      per_page: 25,
    })
    if (currentMp.value === 'ozon') {
      params.set('tab', String(activeTab.value || 'transit'))
      params.set('active', 'true')
    } else {
      params.set('active', String(activeTab.value))
      if (filterStatus.value) params.set('status', filterStatus.value)
    }
    if (filterType.value) params.set('type', filterType.value)
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
  filterStatus.value = defaultStatusForMp(currentMp.value, val === true)
  if (currentMp.value !== 'ozon') filterType.value = ''
  closePanel()
  loadData(1)
}

function closePanel() {
  selectedId.value = ''
  selected.value = null
  rejectOpen.value = false
  rejectComment.value = ''
  rejectReason.value = ''
  rejectAction.value = ''
  decisionError.value = ''
}

async function selectItem(item) {
  if (!item?.claim_id) return
  selectedId.value = item.claim_id
  selected.value = { ...item, actions: [] }
  rejectOpen.value = false
  rejectComment.value = ''
  rejectReason.value = ''
  rejectAction.value = ''
  decisionError.value = ''
  detailLoading.value = true
  try {
    const r = await apiFetch(`/api/v1/cx-responses/claims/${encodeURIComponent(item.claim_id)}?mp=${currentMp.value}`)
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
  if (a.kind === 'reject' || a.action === 'rejectcustom' || a.action === 'DECLINE_REFUND') {
    rejectOpen.value = true
    rejectAction.value = a.action
    const reasons = a.reason_types || []
    rejectReason.value = reasons[0]?.value || ''
    return
  }
  const mpName = mpLabel.value
  if (!confirm(`${a.label}?\n\nРешение уйдёт в ${mpName} сразу.`)) return
  submitDecision(a.action, '')
}

function submitReject() {
  const action = rejectAction.value || (currentMp.value === 'wb' ? 'rejectcustom' : 'DECLINE_REFUND')
  const minLen = currentMp.value === 'wb' ? 10 : 5
  if (String(rejectComment.value || '').trim().length < minLen) {
    decisionError.value = `Укажите причину отказа (не короче ${minLen} символов)`
    return
  }
  if (currentMp.value === 'ym' && rejectReasons.value.length && !rejectReason.value) {
    decisionError.value = 'Выберите причину отказа'
    return
  }
  submitDecision(action, rejectComment.value, rejectReason.value)
}

async function submitDecision(action, comment, reason = '') {
  const claimId = selected.value?.claim_id
  if (!claimId || !action) return
  deciding.value = true
  decisionError.value = ''
  try {
    const r = await apiFetch(`/api/v1/cx-responses/claims/${encodeURIComponent(claimId)}/decision?mp=${currentMp.value}`, {
      method: 'POST',
      body: JSON.stringify({ action, comment: comment || '', reason: reason || '' }),
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
  router.push({ path: '/chats', query: { chat: chatId, mp: currentMp.value } })
}

async function openOrCreateChat() {
  const claimId = selected.value?.claim_id
  if (!claimId) return
  openingChat.value = true
  chatError.value = ''
  try {
    const r = await apiFetch(
      `/api/v1/cx-responses/claims/${encodeURIComponent(claimId)}/open-chat?mp=${currentMp.value}`,
      { method: 'POST' },
    )
    const d = await r.json().catch(() => ({}))
    if (!r.ok) {
      const det = d.detail
      chatError.value = Array.isArray(det)
        ? det.map(x => x.msg || x).join('; ')
        : (det || d.message || `Ошибка ${r.status}`)
      return
    }
    if (d.chat_id) {
      if (selected.value) selected.value.chat = { ...(selected.value.chat || {}), chat_id: d.chat_id }
      openChat(d.chat_id)
    }
  } catch (e) {
    chatError.value = e?.message || 'Не удалось открыть чат'
  } finally {
    openingChat.value = false
  }
}

function setReturnsMp(mp) {
  if (!['wb', 'ym', 'ozon'].includes(mp)) return
  router.replace({ query: { ...route.query, mp, claim: undefined, srid: undefined } })
}

function syncMpToStoreAndQuery() {
  const mp = currentMp.value
  if (platformStore.platform !== mp && platformStore.platform !== 'all') {
    platformStore.setPlatform(mp)
  }
  if (route.query.mp !== mp) {
    router.replace({ query: { ...route.query, mp } })
  }
}

async function openFromQuery() {
  const claim = route.query.claim ? String(route.query.claim) : ''
  const srid = route.query.srid ? String(route.query.srid) : ''
  const tabHint = String(route.query.tab || '')
  syncMpToStoreAndQuery()

  let detail = null
  if (claim) {
    try {
      const r = await apiFetch(`/api/v1/cx-responses/claims/${encodeURIComponent(claim)}?mp=${currentMp.value}`)
      if (r.ok) detail = await r.json()
    } catch { /* ignore */ }
  }

  if (currentMp.value === 'ozon') {
    if (['transit', 'received', 'resolved', 'utilization', 'all'].includes(tabHint)) {
      activeTab.value = tabHint
    } else if (detail?.ozon_tab) {
      activeTab.value = detail.ozon_tab
    } else {
      activeTab.value = 'all'
    }
    filterStatus.value = ''
  } else {
    let useArchive = false
    if (tabHint === 'archive') useArchive = true
    else if (tabHint === 'active') useArchive = false
    else if (detail) useArchive = !!detail.is_archive
    activeTab.value = !useArchive
    filterStatus.value = ''
  }
  filterType.value = ''
  searchQ.value = claim || srid || ''

  await loadData(1)

  if (claim) {
    let found = items.value.find(i => i.claim_id === claim)
    if (!found && currentMp.value === 'ozon' && activeTab.value !== 'all') {
      activeTab.value = 'all'
      await loadData(1)
      found = items.value.find(i => i.claim_id === claim)
    } else if (!found && currentMp.value !== 'ozon') {
      activeTab.value = !activeTab.value
      filterStatus.value = ''
      await loadData(1)
      found = items.value.find(i => i.claim_id === claim)
    }
    if (found) {
      await selectItem(found)
    } else if (detail) {
      selectedId.value = claim
      selected.value = detail
    } else {
      await selectItem({ claim_id: claim })
    }
    return
  }

  if (srid) {
    let found = items.value.find(i => i.srid === srid)
    if (!found && currentMp.value !== 'ozon') {
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
  if (!route.query.mp && ['wb', 'ym', 'ozon'].includes(platformStore.platform)) {
    await router.replace({ query: { ...route.query, mp: platformStore.platform } })
  }
  activeTab.value = defaultTabForMp(currentMp.value)
  filterStatus.value = defaultStatusForMp(currentMp.value, activeTab.value === true)
  if (route.query.claim || route.query.srid) {
    await openFromQuery()
  } else {
    syncMpToStoreAndQuery()
    await loadData(1)
  }
})

onUnmounted(() => {
  window.removeEventListener('keydown', onLightboxKey)
})

watch(() => [route.query.claim, route.query.srid, route.query.tab, route.query.mp], () => {
  if (route.query.claim || route.query.srid) openFromQuery()
  else {
    closePanel()
    activeTab.value = defaultTabForMp(currentMp.value)
    filterStatus.value = defaultStatusForMp(currentMp.value, activeTab.value === true)
    loadData(1)
  }
})

watch(() => platformStore.platform, (p) => {
  if (p === 'all') {
    // Оставляем текущий конкретный mp в URL — локальный свитчер в шапке
    if (!['wb', 'ym', 'ozon'].includes(String(route.query.mp || ''))) {
      router.replace({ query: { ...route.query, mp: currentMp.value } })
    }
    return
  }
  if (!['wb', 'ym', 'ozon'].includes(p)) return
  if (route.query.mp === p) return
  router.replace({ query: { ...route.query, mp: p, claim: undefined, srid: undefined } })
})
</script>
