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
            <img v-if="r.product_photo" :src="r.product_photo" class="w-full h-full object-cover" />
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
              <span :class="['text-xs px-2 py-0.5 rounded-full font-medium border',
                r.state === 'answered' ? 'bg-green-50 text-green-700 border-green-200' : 'bg-amber-50 text-amber-700 border-amber-200']">
                {{ r.state === 'answered' ? 'Отвечено' : 'Ждёт ответа' }}
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

        <!-- Связка с этим клиентом (по заказу), не «по товару» -->
        <div v-if="linkedChat || linkedReturn" class="space-y-2 pt-1 border-t border-gray-100">
          <p class="text-xs font-medium text-gray-500">Связано с этим заказом</p>
          <button
            v-if="linkedChat"
            type="button"
            @click="goToChat"
            class="w-full flex items-center justify-between gap-2 text-sm font-medium px-3 py-2.5 rounded-lg border border-violet-200 bg-violet-50 text-violet-900 hover:bg-violet-100 text-left"
          >
            <span>
              Чат{{ linkedChat.client_name ? `: ${linkedChat.client_name}` : '' }}
              <span v-if="linkedChat.needs_answer" class="text-violet-600 font-normal"> · ждёт ответа</span>
            </span>
            <span class="shrink-0">→</span>
          </button>
          <button
            v-if="linkedReturn"
            type="button"
            @click="goToReturn"
            class="w-full flex items-center justify-between gap-2 text-sm font-medium px-3 py-2.5 rounded-lg border border-orange-200 bg-orange-50 text-orange-900 hover:bg-orange-100 text-left"
          >
            <span>
              Возврат{{ linkedReturn.claim_date_ru ? ` от ${linkedReturn.claim_date_ru}` : '' }}
              <span v-if="linkedReturn.status_label" class="font-normal text-orange-700"> · {{ linkedReturn.status_label }}</span>
            </span>
            <span class="shrink-0">→</span>
          </button>
        </div>
        <p v-else-if="!loading && data && mp === 'wb'" class="text-xs text-gray-400">
          Чат или возврат по этому заказу не найдены (покупатель мог не писать).
        </p>

        <button
          v-if="showGoToReviews"
          type="button"
          @click="goToReviews"
          class="w-full text-sm font-medium px-3 py-2.5 rounded-lg border border-amber-200 bg-amber-50 text-amber-900 hover:bg-amber-100 text-left"
        >Открыть в разделе «Отзывы» →</button>
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
import { useRouter } from 'vue-router'
import { apiFetch } from '../api'

const props = defineProps({
  mp: String,
  reviewId: [String, Number],
  /** true = открыто из чата: показать переход в раздел отзывов */
  showGoToReviews: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])
const router = useRouter()

const loading = ref(true)
const data = ref(null)
const lb = ref({ show: false, i: 0 })
const mp = computed(() => props.mp)
const r = computed(() => data.value?.review || {})
const linkedChat = computed(() => data.value?.linked_chat || null)
const linkedReturn = computed(() => data.value?.linked_return || null)

const mpCls = { wb: 'bg-purple-100 text-purple-700', ym: 'bg-yellow-100 text-yellow-700', ozon: 'bg-blue-100 text-blue-700' }

function fmtDateTime(s) {
  if (!s) return ''
  const d = new Date(s)
  return d.toLocaleDateString('ru', { day: '2-digit', month: '2-digit', year: '2-digit' }) +
    ' ' + d.toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' })
}

function goToReviews() {
  emit('close')
  router.push({ path: '/reviews', query: { review: `${props.mp}:${props.reviewId}` } })
}

function goToChat() {
  const id = linkedChat.value?.chat_id
  if (!id) return
  emit('close')
  router.push({ path: '/chats', query: { chat: id, mp: props.mp || 'wb' } })
}

function goToReturn() {
  const claimId = linkedReturn.value?.claim_id
  if (!claimId) return
  emit('close')
  const q = { claim: claimId, mp: props.mp || 'wb' }
  if (linkedReturn.value?.is_archive) q.tab = 'archive'
  router.push({ path: '/returns', query: q })
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
