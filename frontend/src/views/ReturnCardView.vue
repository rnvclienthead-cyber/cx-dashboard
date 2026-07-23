<template>
  <div class="card-page">
    <div class="card-header">
      <button class="back-btn" @click="$router.push('/return/scan')">← Сканировать</button>
      <div class="card-logo">CX Dashboard</div>
    </div>

    <!-- Загрузка -->
    <div v-if="loading" class="state-center">
      <div class="spinner"></div>
      <p>Загрузка…</p>
    </div>

    <!-- Ошибка -->
    <div v-else-if="error" class="state-center error-state">
      <div class="error-icon">✕</div>
      <p class="error-title">Возврат не найден</p>
      <p class="error-sub">SRID: {{ route.params.srid }}</p>
      <button class="retry-btn" @click="$router.push('/return/scan')">Сканировать снова</button>
    </div>

    <!-- Карточка -->
    <div v-else-if="data" class="card-body">
      <!-- Шапка товара -->
      <div class="product-block">
        <div class="product-name">{{ data.product_name || data.supplier_article }}</div>
        <div class="product-meta">
          <span v-if="data.supplier_article" class="meta-tag">{{ data.supplier_article }}</span>
          <span v-if="data.nm_id" class="meta-tag muted">WB #{{ data.nm_id }}</span>
        </div>
        <div :class="['status-badge', statusClass]">{{ data.status }}</div>
      </div>

      <!-- Фото -->
      <div v-if="data.photos.length" class="photos-block">
        <div class="section-label">Фото от покупателя</div>
        <div class="photos-grid">
          <div
            v-for="(photo, i) in data.photos"
            :key="i"
            class="photo-thumb"
            @click="openLightbox(i)"
          >
            <img :src="photo" loading="lazy" alt="Фото возврата" />
          </div>
        </div>
      </div>

      <!-- Причина -->
      <div v-if="data.user_comment" class="comment-block">
        <div class="section-label">Комментарий покупателя</div>
        <p class="comment-text">{{ data.user_comment }}</p>
      </div>

      <!-- Категории дефекта -->
      <div v-if="data.categories.length" class="cats-block">
        <div class="section-label">Категории дефекта</div>
        <div class="cats-list">
          <span v-for="cat in data.categories" :key="cat" class="cat-tag">{{ cat }}</span>
        </div>
      </div>

      <!-- Даты -->
      <div class="dates-block">
        <div v-if="data.claim_date" class="date-row">
          <span class="date-label">Дата заявки</span>
          <span class="date-val">{{ formatDate(data.claim_date) }}</span>
        </div>
        <div class="date-row">
          <span class="date-label">SRID</span>
          <span class="date-val srid-val">{{ data.srid }}</span>
        </div>
      </div>
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
import { useRoute } from 'vue-router'

const route = useRoute()
const loading = ref(true)
const error = ref(false)
const data = ref(null)
const lightbox = reactive({ open: false, index: 0 })

const API_BASE = import.meta.env.VITE_API_URL || ''

onMounted(async () => {
  try {
    const srid = route.params.srid
    const res = await fetch(`${API_BASE}/api/return/${encodeURIComponent(srid)}`)
    if (!res.ok) throw new Error('not found')
    data.value = await res.json()
  } catch (_) {
    error.value = true
  } finally {
    loading.value = false
  }
})

const statusClass = computed(() => {
  const s = (data.value?.status || '').toLowerCase()
  if (s.includes('одобр')) return 'status-approved'
  if (s.includes('отклон')) return 'status-rejected'
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
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid #1e293b;
}

.back-btn {
  background: none;
  border: none;
  color: #60a5fa;
  font-size: 15px;
  cursor: pointer;
  padding: 4px 0;
}

.card-logo {
  font-size: 11px;
  color: #475569;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

/* States */
.state-center {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 60px 20px;
  color: #94a3b8;
}

.spinner {
  width: 40px;
  height: 40px;
  border: 3px solid #1e293b;
  border-top-color: #60a5fa;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

.error-state { color: #f87171; }
.error-icon {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: #450a0a;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
}
.error-title { font-size: 18px; font-weight: 600; }
.error-sub { font-size: 13px; color: #64748b; }
.retry-btn {
  margin-top: 8px;
  background: #1e3a5f;
  border: 1px solid #2563eb;
  color: #93c5fd;
  border-radius: 8px;
  padding: 10px 20px;
  cursor: pointer;
  font-size: 14px;
}

/* Card body */
.card-body {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.product-block {
  background: #1e293b;
  border-radius: 14px;
  padding: 18px;
}

.product-name {
  font-size: 18px;
  font-weight: 700;
  line-height: 1.3;
  margin-bottom: 10px;
}

.product-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}

.meta-tag {
  background: #0f172a;
  border-radius: 6px;
  padding: 3px 8px;
  font-size: 12px;
  color: #cbd5e1;
}

.meta-tag.muted { color: #64748b; }

.status-badge {
  display: inline-block;
  border-radius: 20px;
  padding: 5px 14px;
  font-size: 13px;
  font-weight: 600;
}
.status-approved { background: #14532d; color: #4ade80; }
.status-rejected { background: #450a0a; color: #f87171; }
.status-pending  { background: #1c1917; color: #fbbf24; }

/* Photos */
.section-label {
  font-size: 12px;
  font-weight: 600;
  color: #64748b;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  margin-bottom: 10px;
}

.photos-block, .comment-block, .cats-block, .dates-block {
  background: #1e293b;
  border-radius: 14px;
  padding: 16px;
}

.photos-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

.photo-thumb {
  aspect-ratio: 1;
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
  background: #0f172a;
}

.photo-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.2s;
}

.photo-thumb:active img { transform: scale(0.96); }

/* Comment */
.comment-text {
  font-size: 15px;
  line-height: 1.6;
  color: #cbd5e1;
  margin: 0;
  white-space: pre-wrap;
}

/* Categories */
.cats-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.cat-tag {
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 6px;
  padding: 5px 10px;
  font-size: 12px;
  color: #94a3b8;
}

/* Dates */
.date-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 6px 0;
  border-bottom: 1px solid #0f172a;
}
.date-row:last-child { border-bottom: none; }
.date-label { font-size: 13px; color: #64748b; }
.date-val { font-size: 13px; color: #cbd5e1; }
.srid-val { font-size: 11px; color: #475569; word-break: break-all; text-align: right; max-width: 60%; }

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
