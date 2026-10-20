<template>
  <div class="fixed inset-0 z-[65] bg-black/50 flex items-start justify-center overflow-y-auto p-4" @click.self="$emit('close')">
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-2xl my-6">
      <!-- Шапка -->
      <div class="flex items-start justify-between gap-3 px-5 py-4 border-b border-gray-100 sticky top-0 bg-white rounded-t-2xl z-10">
        <h2 class="font-semibold text-gray-800">Карточка отзыва</h2>
        <button @click="$emit('close')" class="text-gray-400 hover:text-gray-600 text-2xl leading-none">×</button>
      </div>

      <div v-if="loading" class="flex justify-center py-16">
        <div class="w-8 h-8 border-4 border-rose-500 border-t-transparent rounded-full animate-spin"></div>
      </div>

      <div v-else-if="data" class="px-5 py-4 space-y-5">
        <!-- Товар + оценка + статус -->
        <div class="flex gap-4">
          <div class="w-24 h-32 rounded-lg overflow-hidden bg-gray-100 border border-gray-200 shrink-0">
            <img v-if="photoSrc" :src="photoSrc" class="w-full h-full object-cover" />
            <span v-else class="w-full h-full flex items-center justify-center text-gray-300">
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/></svg>
            </span>
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2 flex-wrap">
              <span :class="['px-1.5 py-0.5 rounded text-xs font-medium', mpCls[mp]]">{{ (mp||'').toUpperCase() }}</span>
              <span v-if="r.rating != null" class="inline-flex items-center gap-px">
                <svg v-for="i in 5" :key="i" width="15" height="15" viewBox="0 0 24 24"
                  :fill="i <= r.rating ? '#f59e0b' : '#e5e7eb'"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
              </span>
              <span :class="['text-xs px-2 py-0.5 rounded-full font-medium border', cardStatusCls]">
                {{ cardStatusLabel }}
              </span>
            </div>
            <p class="text-sm text-gray-500 mt-2">
              Артикул:
              <a v-if="mp === 'wb' && r.nm_id" :href="`https://www.wildberries.ru/catalog/${r.nm_id}/detail.aspx`" target="_blank"
                class="text-rose-600 hover:underline font-medium">{{ r.sku || r.nm_id }}</a>
              <span v-else class="text-gray-700 font-medium">{{ r.sku || '—' }}</span>
            </p>
            <p v-if="r.nm_id" class="text-xs text-gray-400">nm_id: {{ r.nm_id }}</p>
            <p class="text-xs text-gray-400 mt-1">{{ fmtDateTime(r.created_at) }}</p>
          </div>
        </div>

        <!-- Текст отзыва -->
        <div>
          <p v-if="r.body && r.body !== '—'" class="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{{ r.body }}</p>
          <p v-else class="text-sm text-gray-400 italic">Отзыв без текста (только оценка)</p>
          <p v-if="r.pros" class="text-xs text-gray-600 mt-2"><span class="text-green-600 font-medium">Плюсы:</span> {{ r.pros }}</p>
          <p v-if="r.cons" class="text-xs text-gray-600 mt-1"><span class="text-red-500 font-medium">Минусы:</span> {{ r.cons }}</p>
        </div>

        <div v-if="r.update_label" class="rounded-xl border border-violet-200 bg-violet-50 px-3 py-2.5">
          <p class="text-xs font-semibold text-violet-700">{{ r.update_label }}
            <span v-if="r.buyer_updated_at" class="font-normal text-violet-500"> · {{ fmtDateTime(r.buyer_updated_at) }}</span>
          </p>
          <div class="mt-1.5 grid grid-cols-2 gap-2 text-xs">
            <div>
              <p class="text-[10px] uppercase tracking-wide text-slate-400 mb-0.5">Было</p>
              <p v-if="r.prev_valuation != null" class="text-slate-500">{{ r.prev_valuation }}★</p>
              <p class="text-slate-600 whitespace-pre-wrap">{{ r.prev_text || '—' }}</p>
            </div>
            <div>
              <p class="text-[10px] uppercase tracking-wide text-slate-400 mb-0.5">Стало</p>
              <p v-if="r.rating != null" class="text-slate-700">{{ r.rating }}★</p>
              <p class="text-slate-800 whitespace-pre-wrap">{{ r.body && r.body !== '—' ? r.body : '—' }}</p>
            </div>
          </div>
        </div>

        <!-- Фото из отзыва -->
        <div v-if="r.photos && r.photos.length">
          <p class="text-xs font-medium text-gray-500 mb-1.5">Фото покупателя</p>
          <div class="flex flex-wrap gap-2">
            <button v-for="(ph, i) in r.photos" :key="i" @click="lb = { show: true, i }"
              class="w-16 h-16 rounded-lg overflow-hidden border border-gray-200 hover:border-rose-400">
              <img :src="ph" class="w-full h-full object-cover" />
            </button>
          </div>
        </div>

        <!-- Наш ответ -->
        <div v-if="r.answer_text" class="bg-green-50 border border-green-200 rounded-lg px-3 py-2.5">
          <p class="text-xs font-medium text-green-700 mb-1">Наш ответ
            <span v-if="r.answered_by" class="text-green-600/70 font-normal">· {{ r.answered_by === 'auto_reply' ? 'автоответ' : r.answered_by }}<span v-if="r.answered_at"> · {{ fmtDateTime(r.answered_at) }}</span></span>
          </p>
          <p class="text-sm text-green-900 whitespace-pre-wrap">{{ r.answer_text }}</p>
        </div>
        <p v-else-if="r.state === 'answered'" class="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2.5">
          На Wildberries этот отзыв уже не в очереди, но продавец ничего не писал.
          Так площадка делает с оценками без текста.
        </p>

        <!-- Связка с этим клиентом (по заказу), не «по товару» -->
        <div v-if="linkedChat || linkedReturn || linkedReshipment" class="space-y-2 pt-1 border-t border-gray-100">
          <p class="text-xs font-medium text-gray-500">Связано с этим заказом</p>
          <RouterLink
            v-if="linkedChat && chatTo"
            :to="chatTo"
            @click="$emit('close')"
            class="w-full flex items-center justify-between gap-2 text-sm font-medium px-3 py-2.5 rounded-lg border border-violet-200 bg-violet-50 text-violet-900 hover:bg-violet-100 text-left no-underline"
          >
            <span>
              Чат{{ linkedChat.client_name ? `: ${linkedChat.client_name}` : '' }}
              <span v-if="linkedChat.needs_answer" class="text-violet-600 font-normal"> · ждёт ответа</span>
            </span>
            <span class="shrink-0">→</span>
          </RouterLink>
          <RouterLink
            v-if="linkedReturn && returnTo"
            :to="returnTo"
            @click="$emit('close')"
            class="w-full flex items-center justify-between gap-2 text-sm font-medium px-3 py-2.5 rounded-lg border border-orange-200 bg-orange-50 text-orange-900 hover:bg-orange-100 text-left no-underline"
          >
            <span>
              Возврат{{ linkedReturn.claim_date_ru ? ` от ${linkedReturn.claim_date_ru}` : '' }}
              <span v-if="linkedReturn.status_label" class="font-normal text-orange-700"> · {{ linkedReturn.status_label }}</span>
            </span>
            <span class="shrink-0">→</span>
          </RouterLink>
          <RouterLink
            v-if="linkedReshipment && reshipmentTo"
            :to="reshipmentTo"
            @click="$emit('close')"
            class="w-full flex items-center justify-between gap-2 text-sm font-medium px-3 py-2.5 rounded-lg border border-indigo-200 bg-indigo-50 text-indigo-900 hover:bg-indigo-100 text-left no-underline"
          >
            <span>
              Доотправка #{{ linkedReshipment.id }}
              <span v-if="linkedReshipment.status" class="font-normal text-indigo-700"> · {{ linkedReshipment.status }}</span>
            </span>
            <span class="shrink-0">→</span>
          </RouterLink>
        </div>
        <p v-else-if="!loading && data && mp === 'wb'" class="text-xs text-gray-400">
          Чат или возврат по этому заказу не найдены (покупатель мог не писать).
        </p>

        <RouterLink
          v-if="showGoToReviews"
          :to="reviewsTo"
          @click="$emit('close')"
          class="w-full text-sm font-medium px-3 py-2.5 rounded-lg border border-amber-200 bg-amber-50 text-amber-900 hover:bg-amber-100 text-left no-underline block"
        >Открыть в разделе «Отзывы» →</RouterLink>
      </div>
    </div>

    <!-- Мини-лайтбокс фото -->
    <div v-if="lb.show" @click.stop="lb.show = false" class="fixed inset-0 z-[75] bg-black/80 flex items-center justify-center p-4">
      <button @click.stop="lb.i = (lb.i - 1 + r.photos.length) % r.photos.length" v-if="r.photos.length > 1"
        class="absolute left-6 text-white/70 hover:text-white text-5xl">‹</button>
      <img :src="r.photos[lb.i]" @click.stop class="max-w-full max-h-[85vh] rounded-lg object-contain" />
      <button @click.stop="lb.i = (lb.i + 1) % r.photos.length" v-if="r.photos.length > 1"
        class="absolute right-6 text-white/70 hover:text-white text-5xl">›</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { apiFetch } from '../api'
import { reviewProductPhoto } from '../utils/cx'

const props = defineProps({
  mp: String,
  reviewId: [String, Number],
  /** true = открыто из чата: показать переход в раздел отзывов */
  showGoToReviews: { type: Boolean, default: false },
})
defineEmits(['close'])

const loading = ref(true)
const data = ref(null)
const lb = ref({ show: false, i: 0 })
const mp = computed(() => props.mp)
const r = computed(() => data.value?.review || {})
const photoSrc = computed(() => reviewProductPhoto({
  mp: mp.value,
  nm_id: r.value.nm_id,
  product_photo: r.value.product_photo,
}))
const hasAnswerText = computed(() => !!(r.value.answer_text || '').trim())
const cardStatusLabel = computed(() => {
  if (hasAnswerText.value) return 'Отвечено'
  if (r.value.state === 'answered') return 'На WB без ответа'
  return 'Ждёт ответа'
})
const cardStatusCls = computed(() => {
  if (hasAnswerText.value) return 'bg-green-50 text-green-700 border-green-200'
  if (r.value.state === 'answered') return 'bg-amber-50 text-amber-700 border-amber-200'
  return 'bg-amber-50 text-amber-700 border-amber-200'
})
const linkedChat = computed(() => data.value?.linked_chat || null)
const linkedReturn = computed(() => data.value?.linked_return || null)
const linkedReshipment = computed(() => data.value?.linked_reshipment || null)
const chatTo = computed(() => {
  const id = linkedChat.value?.chat_id
  if (!id) return null
  return { path: '/chats', query: { chat: id, mp: props.mp || 'wb' } }
})
const returnTo = computed(() => {
  const claimId = linkedReturn.value?.claim_id
  if (!claimId) return null
  const q = { claim: claimId, mp: props.mp || 'wb' }
  if (linkedReturn.value?.is_archive) q.tab = 'archive'
  return { path: '/returns', query: q }
})
const reshipmentTo = computed(() => {
  const id = linkedReshipment.value?.id
  if (!id) return null
  return { path: '/reshipment', query: { id: String(id) } }
})
const reviewsTo = computed(() => ({
  path: '/reviews',
  query: { review: `${props.mp}:${props.reviewId}` },
}))

const mpCls = { wb: 'bg-purple-100 text-purple-700', ym: 'bg-yellow-100 text-yellow-700', ozon: 'bg-blue-100 text-blue-700' }

function fmtDateTime(s) {
  if (!s) return ''
  const d = new Date(s)
  return d.toLocaleDateString('ru', { day: '2-digit', month: '2-digit', year: '2-digit' }) +
    ' ' + d.toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' })
}

async function load() {
  loading.value = true
  data.value = null
  try {
    const res = await apiFetch(`/api/v1/cx-responses/review-card?mp=${props.mp}&item_id=${encodeURIComponent(props.reviewId)}`)
    data.value = await res.json()
  } catch (e) {
    console.error('review-card load error', e)
  } finally {
    loading.value = false
  }
}

watch(() => [props.mp, props.reviewId], load)
onMounted(load)
</script>
