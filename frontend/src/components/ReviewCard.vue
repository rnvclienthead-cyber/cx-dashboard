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

        <!-- Связанные чаты -->
        <div v-if="data.chats.length">
          <p class="text-xs font-medium text-gray-500 mb-2">Чаты по этому товару ({{ data.chats.length }})</p>
          <div class="space-y-1.5">
            <router-link v-for="c in data.chats" :key="c.chat_id" to="/chats"
              class="flex items-center justify-between gap-2 px-3 py-2 bg-gray-50 hover:bg-violet-50 rounded-lg border border-gray-100 transition-colors">
              <div class="min-w-0">
                <span class="text-sm text-gray-700 font-medium">{{ c.client_name || 'Клиент' }}</span>
                <p class="text-xs text-gray-400 truncate">{{ c.last_message }}</p>
              </div>
              <span v-if="c.needs_answer" class="shrink-0 text-xs bg-violet-100 text-violet-700 rounded-full px-2 py-0.5">ждёт</span>
            </router-link>
          </div>
        </div>

        <!-- Связанные возвраты -->
        <div v-if="data.returns.length">
          <p class="text-xs font-medium text-gray-500 mb-2">Возвраты по этому товару ({{ data.returns.length }})</p>
          <div class="space-y-1.5">
            <router-link v-for="rt in data.returns" :key="rt.srid" to="/returns"
              class="flex items-center justify-between gap-2 px-3 py-2 bg-gray-50 hover:bg-emerald-50 rounded-lg border border-gray-100 transition-colors">
              <div class="min-w-0">
                <span class="text-sm text-gray-700">{{ rt.claim_type_label }}</span>
                <p v-if="rt.user_comment" class="text-xs text-gray-400 truncate">{{ rt.user_comment }}</p>
              </div>
              <span class="shrink-0 text-xs text-gray-500">{{ rt.status_label }}</span>
            </router-link>
          </div>
        </div>

        <!-- Контакты / переотправки -->
        <div v-if="data.reshipments.length">
          <p class="text-xs font-medium text-gray-500 mb-2">Переотправки и контакты ({{ data.reshipments.length }})</p>
          <div class="space-y-2">
            <div v-for="rs in data.reshipments" :key="rs.id" class="px-3 py-2.5 bg-blue-50/60 rounded-lg border border-blue-100">
              <div class="flex items-center justify-between gap-2">
                <span class="text-sm font-medium text-gray-800">{{ rs.customer_name || 'Без имени' }}</span>
                <span class="text-xs text-gray-500">{{ rs.status }}</span>
              </div>
              <div class="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-gray-600 mt-1">
                <span v-if="rs.customer_phone">📞 {{ rs.customer_phone }}</span>
                <span v-if="rs.customer_email">✉ {{ rs.customer_email }}</span>
                <span v-if="rs.order_number">Заказ: {{ rs.order_number }}</span>
                <span v-if="rs.salebot_messenger" class="text-blue-600">{{ rs.salebot_messenger }}</span>
              </div>
            </div>
          </div>
        </div>

        <p v-if="!data.chats.length && !data.returns.length && !data.reshipments.length" class="text-xs text-gray-400 text-center py-2">
          Связанных чатов, возвратов и переотправок не найдено.
        </p>
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
import { apiFetch } from '../api'

const props = defineProps({ mp: String, reviewId: [String, Number] })
defineEmits(['close'])

const loading = ref(true)
const data = ref(null)
const lb = ref({ show: false, i: 0 })
const mp = computed(() => props.mp)
const r = computed(() => data.value?.review || {})

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
