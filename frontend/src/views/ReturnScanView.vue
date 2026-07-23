<template>
  <div class="scan-page">
    <div class="scan-header">
      <div class="scan-logo">CX Dashboard</div>
      <div class="scan-title">Сканер возвратов</div>
    </div>

    <div class="scan-body">
      <!-- Кнопка запуска (нужен жест пользователя для камеры) -->
      <div v-if="state === 'idle'" class="scan-idle">
        <div class="idle-icon">📦</div>
        <p class="idle-hint">Нажмите кнопку и наведите камеру на штрихкод с наклейки WB</p>
        <button class="start-btn" @click="startCamera">Открыть камеру</button>
        <div class="manual-wrap">
          <p class="manual-label">или введите SRID вручную</p>
          <input
            v-model="manualSrid"
            class="manual-input"
            placeholder="SRID или число (income_id)"
            @keyup.enter="goManual"
          />
          <button class="manual-btn" :disabled="!manualSrid.trim()" @click="goManual">Найти</button>
        </div>
      </div>

      <!-- Камера активна -->
      <div v-if="state === 'scanning'" class="scanner-wrap">
        <div class="video-container">
          <video ref="videoEl" autoplay playsinline muted class="video-feed" />
          <canvas ref="canvasEl" class="canvas-hidden" />
          <div class="scan-frame" />
        </div>
        <p class="scan-hint">Наведите на штрихкод/QR-код на наклейке WB</p>
        <button class="stop-btn" @click="stopCamera">Отмена</button>
      </div>

      <!-- Считано -->
      <div v-if="state === 'found'" class="scan-found">
        <div class="found-icon">✓</div>
        <div class="found-text">Считано — загружаю данные…</div>
      </div>

      <!-- Ошибка -->
      <div v-if="state === 'error'" class="scan-error-state">
        <div class="err-icon">!</div>
        <p class="err-msg">{{ errorMsg }}</p>
        <button class="start-btn" @click="resetToIdle">Попробовать снова</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const videoEl = ref(null)
const canvasEl = ref(null)
const state = ref('idle')   // idle | scanning | found | error
const errorMsg = ref('')
const manualSrid = ref('')

let stream = null
let rafId = null
let detector = null

async function startCamera() {
  errorMsg.value = ''

  // Проверяем поддержку BarcodeDetector
  if (!('BarcodeDetector' in window)) {
    state.value = 'error'
    errorMsg.value = 'Ваш браузер не поддерживает сканирование. Используйте Safari iOS 17+ или Chrome.'
    return
  }

  try {
    detector = new window.BarcodeDetector({ formats: ['qr_code', 'code_128', 'code_39', 'ean_13', 'ean_8', 'data_matrix'] })
  } catch {
    state.value = 'error'
    errorMsg.value = 'Не удалось инициализировать сканер.'
    return
  }

  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'environment', width: { ideal: 1280 }, height: { ideal: 720 } }
    })
  } catch (e) {
    state.value = 'error'
    errorMsg.value = 'Нет доступа к камере. Разрешите доступ в настройках браузера.'
    return
  }

  state.value = 'scanning'

  // Подключаем видео после рендера
  await new Promise(r => setTimeout(r, 50))
  if (videoEl.value) {
    videoEl.value.srcObject = stream
    await videoEl.value.play().catch(() => {})
    scan()
  }
}

function scan() {
  if (state.value !== 'scanning' || !videoEl.value || !canvasEl.value) return

  const video = videoEl.value
  if (video.readyState < 2) {
    rafId = requestAnimationFrame(scan)
    return
  }

  const canvas = canvasEl.value
  canvas.width = video.videoWidth
  canvas.height = video.videoHeight
  const ctx = canvas.getContext('2d')
  ctx.drawImage(video, 0, 0)

  detector.detect(canvas)
    .then(codes => {
      if (codes.length && state.value === 'scanning') {
        const raw = codes[0].rawValue
        handleDetected(raw)
      } else {
        rafId = requestAnimationFrame(scan)
      }
    })
    .catch(() => {
      rafId = requestAnimationFrame(scan)
    })
}

function handleDetected(raw) {
  stopCamera()
  state.value = 'found'

  // Пробуем извлечь значение из URL
  let value = raw
  try {
    const url = new URL(raw)
    const parts = url.pathname.split('/').filter(Boolean)
    if (parts.length) value = parts[parts.length - 1]
  } catch (_) {}

  // Чисто числовой код → это income_id с логистической наклейки (QR партии возвратов)
  if (/^\d+$/.test(value.trim())) {
    router.push(`/return/batch/${value.trim()}`)
  } else {
    router.push(`/return/${encodeURIComponent(value)}`)
  }
}

function stopCamera() {
  if (rafId) { cancelAnimationFrame(rafId); rafId = null }
  if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null }
  if (state.value === 'scanning') state.value = 'idle'
}

function resetToIdle() {
  errorMsg.value = ''
  state.value = 'idle'
}

function goManual() {
  const s = manualSrid.value.trim()
  if (!s) return
  if (/^\d+$/.test(s)) {
    router.push(`/return/batch/${s}`)
  } else {
    router.push(`/return/${encodeURIComponent(s)}`)
  }
}

onBeforeUnmount(stopCamera)
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
  padding: 20px 24px 12px;
  border-bottom: 1px solid #1e293b;
}
.scan-logo {
  font-size: 11px;
  color: #64748b;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-bottom: 4px;
}
.scan-title { font-size: 20px; font-weight: 700; }

.scan-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 32px 20px;
}

/* Idle */
.scan-idle {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  width: 100%;
  max-width: 360px;
}
.idle-icon { font-size: 56px; }
.idle-hint { color: #94a3b8; font-size: 15px; text-align: center; line-height: 1.5; }

.start-btn {
  width: 100%;
  background: #2563eb;
  color: #fff;
  border: none;
  border-radius: 12px;
  padding: 14px;
  font-size: 16px;
  font-weight: 700;
  cursor: pointer;
  transition: background 0.15s;
}
.start-btn:active { background: #1d4ed8; }

/* Manual input */
.manual-wrap {
  width: 100%;
  border-top: 1px solid #1e293b;
  padding-top: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.manual-label { font-size: 12px; color: #475569; text-align: center; }
.manual-input {
  width: 100%;
  box-sizing: border-box;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 10px;
  padding: 12px 14px;
  color: #f1f5f9;
  font-size: 14px;
  outline: none;
}
.manual-input:focus { border-color: #3b82f6; }
.manual-btn {
  background: #1e3a5f;
  border: 1px solid #2563eb;
  color: #93c5fd;
  border-radius: 10px;
  padding: 10px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  width: 100%;
}
.manual-btn:disabled { opacity: 0.4; cursor: default; }

/* Scanner */
.scanner-wrap {
  width: 100%;
  max-width: 420px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}
.video-container {
  position: relative;
  width: 100%;
  aspect-ratio: 4/3;
  border-radius: 16px;
  overflow: hidden;
  background: #000;
}
.video-feed {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.canvas-hidden {
  position: absolute;
  visibility: hidden;
  pointer-events: none;
  top: 0; left: 0;
}
.scan-frame {
  position: absolute;
  inset: 20%;
  border: 3px solid #3b82f6;
  border-radius: 10px;
  box-shadow: 0 0 0 9999px rgba(0,0,0,0.45);
  pointer-events: none;
}

.scan-hint { color: #94a3b8; font-size: 14px; text-align: center; }

.stop-btn {
  background: #1e293b;
  border: 1px solid #334155;
  color: #94a3b8;
  border-radius: 10px;
  padding: 10px 24px;
  font-size: 14px;
  cursor: pointer;
}

/* Found */
.scan-found {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding-top: 60px;
}
.found-icon {
  width: 64px; height: 64px;
  border-radius: 50%;
  background: #166534;
  display: flex; align-items: center; justify-content: center;
  font-size: 32px; color: #4ade80;
}
.found-text { font-size: 16px; color: #cbd5e1; }

/* Error */
.scan-error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  width: 100%;
  max-width: 360px;
}
.err-icon {
  width: 56px; height: 56px;
  border-radius: 50%;
  background: #450a0a;
  display: flex; align-items: center; justify-content: center;
  font-size: 28px; color: #f87171;
}
.err-msg { color: #fca5a5; font-size: 14px; text-align: center; line-height: 1.5; }
</style>
