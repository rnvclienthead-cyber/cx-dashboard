<template>
  <div class="scan-page" @pointerdown="onPagePointer">
    <div class="scan-header">
      <div class="scan-title">Сканер возвратов</div>
    </div>

    <div class="scan-body">
      <div v-if="state === 'error'" class="lead">
        <p class="err-msg">{{ errorMsg }}</p>
      </div>

      <div v-if="state === 'found'" class="scan-found">
        <div class="found-text">Ищу возврат…</div>
      </div>

      <div v-if="state !== 'found'" class="manual-wrap">
        <p class="manual-label">Пистолет — только на чёрные полоски штрихкода, не на QR. Курсор в поле не обязателен</p>
        <input
          ref="manualInput"
          v-model="manualCode"
          class="manual-input"
          inputmode="numeric"
          autocomplete="off"
          placeholder="56903118348"
        >
        <button class="manual-btn" :disabled="lookingUp" @click="goManual">
          {{ lookingUp ? 'Ищу…' : 'Найти' }}
        </button>
      </div>

      <div v-if="state !== 'found'" class="intake-box">
        <div class="intake-head">
          Сегодня на складе: {{ intakeTotal }} шт
        </div>
        <div v-if="intakeArticles.length" class="intake-arts">
          <div v-for="row in intakeArticles" :key="row.supplier_article" class="intake-art">
            <span class="intake-art-name">{{ row.supplier_article }}{{ row.product_name ? ' · ' + row.product_name : '' }}</span>
            <span class="intake-art-qty">{{ row.qty }}</span>
          </div>
        </div>
        <div v-else class="intake-empty">Пока ничего не пробивали</div>
        <div v-if="intakeItems.length" class="intake-log">
          <div v-for="row in intakeItems.slice(0, 12)" :key="row.id" class="intake-row">
            <span class="intake-time">{{ formatReceivedAt(row.received_at) }}</span>
            <span class="intake-what">{{ row.supplier_article || row.scan_code || 'без артикула' }}</span>
            <span v-if="row.scan_count > 1" class="intake-dup">повтор</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onBeforeUnmount, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import {
  createHidWedge,
  formatManualCode,
  formatReceivedAt,
  hintForMiss,
  isLinearSticker,
  isStickerQr,
} from '../utils/returnScan.js'

const router = useRouter()
const manualInput = ref(null)
const state = ref('idle')
const errorMsg = ref('')
const manualCode = ref('')
const lookingUp = ref(false)
const intakeTotal = ref(0)
const intakeArticles = ref([])
const intakeItems = ref([])
const API_BASE = import.meta.env.VITE_API_URL || ''
const hidWedge = createHidWedge()

async function loadIntake() {
  try {
    const res = await fetch(`${API_BASE}/api/return/intake`)
    if (!res.ok) return
    const data = await res.json()
    intakeTotal.value = Number(data.total || 0)
    intakeArticles.value = Array.isArray(data.articles) ? data.articles : []
    intakeItems.value = Array.isArray(data.items) ? data.items : []
  } catch {
    /* журнал не должен ломать сканер */
  }
}

async function lookupCode(raw) {
  const code = String(raw || '').trim()
  if (!code || lookingUp.value) return false
  lookingUp.value = true
  state.value = 'found'
  try {
    const res = await fetch(`${API_BASE}/api/return/lookup?code=${encodeURIComponent(code)}`)
    if (!res.ok) {
      state.value = 'error'
      errorMsg.value = hintForMiss(code)
      loadIntake()
      return false
    }
    const data = await res.json()
    if (data.kind === 'batch') {
      router.push(`/return/batch/${encodeURIComponent(data.income_id)}`)
      return true
    }
    const dest = data.item?.srid || data.item?.claim_id || code
    router.push(`/return/${encodeURIComponent(dest)}`)
    return true
  } catch (_) {
    state.value = 'error'
    errorMsg.value = 'Не удалось связаться с сервером. Проверьте интернет и попробуйте снова.'
    return false
  } finally {
    lookingUp.value = false
    if (state.value !== 'found') focusManual()
  }
}

function goManual() {
  const raw = String(manualCode.value || '').trim()
  if (lookingUp.value) return
  if (isStickerQr(raw) || (raw && !isLinearSticker(raw))) {
    state.value = 'error'
    errorMsg.value = hintForMiss(raw || 'qr')
    manualCode.value = ''
    focusManual()
    return
  }
  const s = formatManualCode(raw)
  if (!s) {
    state.value = 'error'
    errorMsg.value = 'Пробейте чёрные полоски штрихкода или введите номер с наклейки.'
    return
  }
  lookupCode(s)
}

function isTypingTarget(el) {
  if (!el || el === manualInput.value) return false
  const tag = String(el.tagName || '').toLowerCase()
  if (tag === 'textarea') return true
  if (tag === 'input' && el.type !== 'hidden' && el.type !== 'button') return true
  return Boolean(el.isContentEditable)
}

function onHidKey(e) {
  if (lookingUp.value) return
  if (isTypingTarget(e.target)) return
  const inField = e.target === manualInput.value
  const result = hidWedge.push(e.key, Date.now(), e)
  if (result.kind === 'char') {
    if (!inField) {
      e.preventDefault()
      manualCode.value = result.buf
    }
    return
  }
  if (result.kind === 'complete') {
    e.preventDefault()
    manualCode.value = result.code
    goManual()
    return
  }
  if (result.kind === 'enter' && inField) {
    e.preventDefault()
    goManual()
  }
}

function onPagePointer(e) {
  const tag = String(e.target && e.target.tagName || '').toLowerCase()
  if (tag === 'button' || tag === 'a' || tag === 'input' || tag === 'label') return
  focusManual()
}

function onVisibility() {
  if (document.visibilityState === 'visible') focusManual()
}

function focusManual() {
  nextTick(() => {
    const el = manualInput.value
    if (el && typeof el.focus === 'function') el.focus()
  })
}

onMounted(() => {
  window.addEventListener('keydown', onHidKey, true)
  document.addEventListener('visibilitychange', onVisibility)
  focusManual()
  loadIntake()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onHidKey, true)
  document.removeEventListener('visibilitychange', onVisibility)
  hidWedge.reset()
})
</script>

<style scoped>
.scan-page {
  min-height: 100dvh;
  background: #0f172a;
  display: flex;
  flex-direction: column;
  color: #f1f5f9;
  font-family: system-ui, sans-serif;
}
.scan-header {
  padding: 12px 16px 8px;
  border-bottom: 1px solid #1e293b;
}
.scan-title { font-size: 16px; font-weight: 700; }
.scan-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px 16px 20px;
  gap: 10px;
  width: 100%;
  max-width: 420px;
  margin: 0 auto;
  box-sizing: border-box;
}
.lead {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.manual-wrap {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.manual-label { font-size: 13px; color: #94a3b8; text-align: center; line-height: 1.4; margin: 0; }
.manual-input {
  width: 100%;
  box-sizing: border-box;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 10px;
  padding: 12px 10px;
  color: #f1f5f9;
  font-size: 16px;
  outline: none;
}
.manual-input:focus { border-color: #3b82f6; }
.manual-btn {
  background: #2563eb;
  border: none;
  color: #fff;
  border-radius: 10px;
  padding: 14px;
  font-size: 16px;
  font-weight: 700;
  cursor: pointer;
  width: 100%;
}
.manual-btn:disabled { opacity: 0.4; cursor: default; }
.scan-found {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding-top: 40px;
}
.found-text { font-size: 16px; color: #cbd5e1; }
.intake-box {
  width: 100%;
  margin-top: 8px;
  padding: 12px;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.intake-head { font-size: 14px; font-weight: 700; color: #e2e8f0; }
.intake-empty { font-size: 13px; color: #64748b; }
.intake-arts { display: flex; flex-direction: column; gap: 6px; }
.intake-art {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
  color: #cbd5e1;
}
.intake-art-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.intake-art-qty { font-weight: 700; color: #93c5fd; flex-shrink: 0; }
.intake-log {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-top: 6px;
  border-top: 1px solid #334155;
}
.intake-row {
  display: flex;
  gap: 8px;
  font-size: 12px;
  color: #94a3b8;
}
.intake-time { flex-shrink: 0; color: #64748b; }
.intake-what { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.intake-dup { color: #fbbf24; flex-shrink: 0; }
.err-msg { color: #fca5a5; font-size: 14px; text-align: center; line-height: 1.5; margin: 0; }
</style>
