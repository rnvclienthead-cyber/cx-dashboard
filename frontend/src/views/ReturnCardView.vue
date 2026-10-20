<template>
  <div class="card-page">
    <div class="card-header">
      <RouterLink class="back-btn" to="/return/scan">← Сканер</RouterLink>
    </div>

    <div v-if="loading" class="state-center">
      <div class="spinner"></div>
      <p>Загрузка…</p>
    </div>

    <div v-else-if="error" class="state-center error-state">
      <p class="error-title">Возврат не найден</p>
      <p class="error-sub">{{ route.params.srid }}</p>
      <RouterLink class="retry-btn" to="/return/scan">Сканировать снова</RouterLink>
    </div>

    <div v-else-if="data" class="card-body">
      <div class="product-block">
        <div class="product-head">
          <img v-if="data.product_photo" :src="data.product_photo" class="product-thumb" alt="" />
          <div class="product-copy">
            <div class="mp-row">
              <span class="mp-badge">{{ mpLabel }}</span>
              <span :class="['status-badge', statusClass]">{{ data.status }}</span>
            </div>
            <div class="product-name">{{ data.product_name || data.supplier_article || 'Возврат' }}</div>
            <div class="product-meta">
              <span v-if="data.supplier_article">{{ data.supplier_article }}</span>
              <span v-if="data.nm_id">{{ data.marketplace === 'ozon' ? 'SKU' : 'WB' }} {{ data.nm_id }}</span>
            </div>
          </div>
        </div>
        <p v-if="data.match_label" class="match-hint">нашли по: {{ data.match_label }}</p>
      </div>

      <div v-if="!hasClaim" class="block warehouse-note">
        <div class="section-label">{{ data.no_claim_title || 'Претензии нет — это нормально' }}</div>
        <p class="comment-text">{{ data.no_claim_text || 'Заявки покупателя может не быть: отмена, невыкуп или отбраковка на приёмке. Причины дефекта тогда не будет.' }}</p>
      </div>

      <div v-if="hasClaim && (data.categories || []).length" class="block">
        <div class="section-label">Категории дефекта</div>
        <div class="cats-list">
          <span v-for="cat in data.categories" :key="cat" class="cat-tag">{{ cat }}</span>
        </div>
      </div>

      <div v-if="!hasClaim && (data.categories || []).length" class="block">
        <div class="section-label">Как вернули</div>
        <div class="cats-list">
          <span v-for="cat in data.categories" :key="cat" class="cat-tag">{{ cat }}</span>
        </div>
      </div>

      <div v-if="hasClaim && data.user_comment" class="block">
        <div class="section-label">Комментарий покупателя</div>
        <p class="comment-text">{{ data.user_comment }}</p>
      </div>

      <div v-if="hasClaim" class="block">
        <div class="section-label">Фото от покупателя</div>
        <div v-if="data.photos && data.photos.length" class="photos-row">
          <button
            v-for="(photo, i) in data.photos"
            :key="i"
            type="button"
            class="photo-thumb"
            @click="openLightbox(i)"
          >
            <img :src="photo" loading="lazy" alt="" />
          </button>
        </div>
        <p v-else class="empty-photos">Фото в базе нет</p>
      </div>

      <div class="block dates-block">
        <div v-if="data.received_at" class="date-row">
          <span class="date-label">Принято</span>
          <span class="date-val">{{ formatReceivedAt(data.received_at) }}{{ data.intake && data.intake.scan_count > 1 ? ' · повтор' : '' }}</span>
        </div>
        <div v-if="data.claim_date" class="date-row">
          <span class="date-label">Дата</span>
          <span class="date-val">{{ formatDate(data.claim_date) }}</span>
        </div>
        <div v-if="data.place_name" class="date-row">
          <span class="date-label">Сейчас</span>
          <span class="date-val">{{ data.place_name }}</span>
        </div>
        <div v-if="data.target_place_name" class="date-row">
          <span class="date-label">Куда</span>
          <span class="date-val">{{ data.target_place_name }}</span>
        </div>
        <div v-if="data.srid" class="date-row">
          <span class="date-label">{{ data.marketplace === 'ym' ? 'Заказ' : (data.marketplace === 'ozon' ? 'Отправление' : 'SRID') }}</span>
          <span class="date-val srid-val">{{ data.srid }}</span>
        </div>
        <div v-if="data.barcode" class="date-row">
          <span class="date-label">Штрихкод</span>
          <span class="date-val srid-val">{{ data.barcode }}</span>
        </div>
      </div>

      <RouterLink class="scan-again" to="/return/scan">Сканировать ещё</RouterLink>
    </div>

    <!-- Лайтбокс -->
    <div v-if="lightbox.open" class="lightbox" @click="lightbox.open = false">
      <img :src="data.photos[lightbox.index]" class="lightbox-img" @click.stop />
      <div class="lightbox-nav" @click.stop>
        <button v-if="lightbox.index > 0" @click="lightbox.index--">‹</button>
        <span>{{ lightbox.index + 1 }} / {{ data.photos.length }}</span>
        <button v-if="lightbox.index < data.photos.length - 1" @click="lightbox.index++">›</button>
      </div>
      <button class="lightbox-close" @click="lightbox.open = false">✕</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, reactive } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { formatReceivedAt } from '../utils/returnScan.js'

const route = useRoute()
const router = useRouter()
const loading = ref(true)
const error = ref(false)
const data = ref(null)
const lightbox = reactive({ open: false, index: 0 })

const API_BASE = import.meta.env.VITE_API_URL || ''
const MP_LABEL = { wb: 'Wildberries', ym: 'Яндекс Маркет', ozon: 'Ozon' }
const mpLabel = computed(() => MP_LABEL[data.value?.marketplace] || 'Wildberries')
const hasClaim = computed(() => Boolean(data.value?.claim_id) && data.value?.has_claim !== false)

onMounted(async () => {
  try {
    const srid = route.params.srid
    const res = await fetch(`${API_BASE}/api/return/${encodeURIComponent(srid)}`)
    if (!res.ok) throw new Error('not found')
    const payload = await res.json()
    if (payload.kind === 'batch' && payload.income_id) {
      router.replace(`/return/batch/${encodeURIComponent(payload.income_id)}`)
      return
    }
    data.value = payload.item || payload
    if (data.value && !Array.isArray(data.value.photos)) data.value.photos = []
  } catch (_) {
    error.value = true
  } finally {
    loading.value = false
  }
})

const statusClass = computed(() => {
  const s = (data.value?.status || '').toLowerCase()
  if (s.includes('одобр')) return 'status-approved'
  if (s.includes('отклон') || s.includes('отмен')) return 'status-rejected'
  if (s.includes('без претенз') || s.includes('выдано') || s.includes('склад')) return 'status-pending'
  return 'status-pending'
})

function formatDate(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

function openLightbox(index) {
  lightbox.index = index
  lightbox.open = true
}
</script>

<style scoped>
.card-page {
  min-height: 100dvh;
  background: #0f172a;
  color: #f1f5f9;
  font-family: system-ui, sans-serif;
  padding-bottom: 40px;
}

.card-header {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  border-bottom: 1px solid #1e293b;
}

.back-btn {
  background: none;
  border: none;
  color: #60a5fa;
  font-size: 14px;
  cursor: pointer;
  padding: 4px 0;
  text-decoration: none;
}

.state-center {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 40px 20px;
  color: #94a3b8;
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid #1e293b;
  border-top-color: #60a5fa;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

.error-state { color: #f87171; }
.error-title { font-size: 16px; font-weight: 600; margin: 0; }
.error-sub { font-size: 12px; color: #64748b; margin: 0; word-break: break-all; }
.retry-btn {
  margin-top: 8px;
  background: #1e3a5f;
  border: 1px solid #2563eb;
  color: #93c5fd;
  border-radius: 8px;
  padding: 10px 20px;
  cursor: pointer;
  font-size: 14px;
  text-decoration: none;
  display: inline-block;
}

.card-body {
  padding: 12px 12px 24px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.product-block, .block {
  background: #1e293b;
  border-radius: 12px;
  padding: 12px;
}

.warehouse-note { border: 1px solid #334155; }

.mp-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}
.mp-badge {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #93c5fd;
  background: #0f172a;
  border-radius: 5px;
  padding: 2px 6px;
}
.match-hint { font-size: 11px; color: #64748b; margin: 8px 0 0; }

.product-head {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.product-thumb {
  width: 48px;
  height: 64px;
  object-fit: cover;
  border-radius: 6px;
  background: #0f172a;
  flex-shrink: 0;
}
.product-copy { min-width: 0; flex: 1; }

.product-name {
  font-size: 15px;
  font-weight: 700;
  line-height: 1.25;
}

.product-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
  font-size: 12px;
  color: #94a3b8;
}

.status-badge {
  display: inline-block;
  border-radius: 12px;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 600;
}
.status-approved { background: #14532d; color: #4ade80; }
.status-rejected { background: #450a0a; color: #f87171; }
.status-pending  { background: #1c1917; color: #fbbf24; }

.section-label {
  font-size: 11px;
  font-weight: 600;
  color: #64748b;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin-bottom: 6px;
}

.photos-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.photo-thumb {
  width: 44px;
  height: 44px;
  padding: 0;
  border: none;
  border-radius: 6px;
  overflow: hidden;
  cursor: pointer;
  background: #0f172a;
}

.photo-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.empty-photos { margin: 0; font-size: 13px; color: #64748b; }

.comment-text {
  font-size: 14px;
  line-height: 1.4;
  color: #cbd5e1;
  margin: 0;
  white-space: pre-wrap;
}

.cats-list {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.cat-tag {
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 12px;
  color: #94a3b8;
}

.date-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
  padding: 4px 0;
  border-bottom: 1px solid #0f172a;
}
.date-row:last-child { border-bottom: none; }
.date-label { font-size: 12px; color: #64748b; flex-shrink: 0; }
.date-val { font-size: 12px; color: #cbd5e1; text-align: right; }
.srid-val { font-size: 11px; color: #64748b; word-break: break-all; }

.scan-again {
  width: 100%;
  background: #2563eb;
  color: #fff;
  border: none;
  border-radius: 10px;
  padding: 14px;
  font-size: 15px;
  font-weight: 700;
  cursor: pointer;
  text-decoration: none;
  display: block;
  text-align: center;
  box-sizing: border-box;
}

/* Lightbox */
.lightbox {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.92);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 20px;
}

.lightbox-img {
  max-width: 100%;
  max-height: 70dvh;
  border-radius: 10px;
  object-fit: contain;
}

.lightbox-nav {
  display: flex;
  align-items: center;
  gap: 20px;
  margin-top: 16px;
  color: #cbd5e1;
  font-size: 15px;
}

.lightbox-nav button {
  background: #1e293b;
  border: none;
  color: #f1f5f9;
  font-size: 28px;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.lightbox-close {
  position: absolute;
  top: 16px;
  right: 16px;
  background: #1e293b;
  border: none;
  color: #f1f5f9;
  font-size: 18px;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  cursor: pointer;
}
</style>
