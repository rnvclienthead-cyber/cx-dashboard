<template>
  <div class="batch-page">
    <div class="batch-header">
      <button class="back-btn" @click="$router.push('/return/scan')">← Сканировать</button>
      <div class="batch-logo">CX Dashboard</div>
    </div>

    <!-- Загрузка -->
    <div v-if="loading" class="state-center">
      <div class="spinner"></div>
      <p>Загрузка партии…</p>
    </div>

    <!-- Ошибка -->
    <div v-else-if="error" class="state-center error-state">
      <div class="error-icon">✕</div>
      <p class="error-title">Партия не найдена</p>
      <p class="error-sub">income_id: {{ route.params.incomeId }}</p>
      <p class="error-hint">Данные появятся после ночной синхронизации</p>
      <button class="retry-btn" @click="$router.push('/return/scan')">Сканировать снова</button>
    </div>

    <!-- Список карточек -->
    <div v-else-if="batch" class="batch-body">
      <div class="batch-summary">
        <div class="summary-label">Партия возвратов</div>
        <div class="summary-id">#{{ batch.income_id }}</div>
        <div class="summary-count">{{ batch.count }} претензий</div>
      </div>

      <div
        v-for="item in batch.items"
        :key="item.srid"
        class="claim-card"
        @click="$router.push(`/return/${encodeURIComponent(item.srid)}`)"
      >
        <div class="claim-top">
          <div class="claim-name">{{ item.product_name || item.supplier_article }}</div>
          <div :class="['claim-status', statusClass(item.status)]">{{ item.status }}</div>
        </div>
        <div class="claim-meta">
          <span v-if="item.supplier_article" class="meta-chip">{{ item.supplier_article }}</span>
          <span v-if="item.nm_id" class="meta-chip muted">WB #{{ item.nm_id }}</span>
        </div>
        <div v-if="item.categories.length" class="claim-cats">
          <span v-for="cat in item.categories.slice(0, 2)" :key="cat" class="cat-chip">{{ cat }}</span>
          <span v-if="item.categories.length > 2" class="cat-chip more">+{{ item.categories.length - 2 }}</span>
        </div>
        <div v-if="item.user_comment" class="claim-comment">{{ item.user_comment }}</div>
        <div v-if="item.photos.length" class="claim-photo-row">
          <img
            v-for="(ph, i) in item.photos.slice(0, 3)"
            :key="i"
            :src="ph"
            class="claim-photo-thumb"
            loading="lazy"
          />
          <span v-if="item.photos.length > 3" class="photo-more">+{{ item.photos.length - 3 }}</span>
        </div>
        <div class="claim-date">{{ formatDate(item.claim_date) }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const loading = ref(true)
const error = ref(false)
const batch = ref(null)

const API_BASE = import.meta.env.VITE_API_URL || ''

onMounted(async () => {
  try {
    const incomeId = route.params.incomeId
    const res = await fetch(`${API_BASE}/api/return/income/${encodeURIComponent(incomeId)}`)
    if (!res.ok) throw new Error('not found')
    batch.value = await res.json()
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
})

function statusClass(status) {
  const s = (status || '').toLowerCase()
  if (s.includes('одобр')) return 'status-approved'
  if (s.includes('отклон')) return 'status-rejected'
  return 'status-pending'
}

function formatDate(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
}
</script>

<style scoped>
.batch-page {
  min-height: 100dvh;
  background: #0f172a;
  color: #f1f5f9;
  font-family: system-ui, sans-serif;
  padding-bottom: 40px;
}

.batch-header {
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

.batch-logo {
  font-size: 11px;
  color: #475569;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.state-center {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 60px 20px;
  color: #94a3b8;
}

.spinner {
  width: 40px; height: 40px;
  border: 3px solid #1e293b;
  border-top-color: #60a5fa;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.error-state { color: #f87171; }
.error-icon {
  width: 56px; height: 56px;
  border-radius: 50%;
  background: #450a0a;
  display: flex; align-items: center; justify-content: center;
  font-size: 24px;
}
.error-title { font-size: 18px; font-weight: 600; }
.error-sub { font-size: 13px; color: #64748b; }
.error-hint { font-size: 12px; color: #475569; text-align: center; }
.retry-btn {
  background: #1e3a5f; border: 1px solid #2563eb;
  color: #93c5fd; border-radius: 8px;
  padding: 10px 20px; cursor: pointer; font-size: 14px;
}

/* Body */
.batch-body {
  padding: 16px 16px 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.batch-summary {
  background: #1e293b;
  border-radius: 14px;
  padding: 16px 18px;
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.summary-label { font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: 0.06em; }
.summary-id { font-size: 18px; font-weight: 700; color: #60a5fa; }
.summary-count { font-size: 13px; color: #94a3b8; margin-left: auto; }

/* Claim card */
.claim-card {
  background: #1e293b;
  border-radius: 14px;
  padding: 14px 16px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: background 0.15s;
}
.claim-card:active { background: #273549; }

.claim-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.claim-name {
  font-size: 15px;
  font-weight: 600;
  line-height: 1.3;
  flex: 1;
}

.claim-status {
  border-radius: 20px;
  padding: 3px 10px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  flex-shrink: 0;
}
.status-approved { background: #14532d; color: #4ade80; }
.status-rejected { background: #450a0a; color: #f87171; }
.status-pending  { background: #1c1917; color: #fbbf24; }

.claim-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.meta-chip {
  background: #0f172a; border-radius: 5px;
  padding: 2px 7px; font-size: 11px; color: #94a3b8;
}
.meta-chip.muted { color: #475569; }

.claim-cats {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.cat-chip {
  background: #0f172a; border: 1px solid #334155;
  border-radius: 5px; padding: 2px 7px;
  font-size: 11px; color: #64748b;
}
.cat-chip.more { color: #475569; }

.claim-comment {
  font-size: 13px;
  color: #94a3b8;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.claim-photo-row {
  display: flex;
  gap: 6px;
  align-items: center;
}
.claim-photo-thumb {
  width: 52px; height: 52px;
  border-radius: 6px;
  object-fit: cover;
  background: #0f172a;
}
.photo-more {
  font-size: 12px;
  color: #475569;
}

.claim-date {
  font-size: 11px;
  color: #475569;
  text-align: right;
}
</style>
