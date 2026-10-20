/** Коды с этикетки WB: что искать, что пропускать. */

export function isBoxOrRouteCode(code) {
  const u = String(code || '').trim().toUpperCase()
  if (!u) return true
  if (u.includes('TRBX')) return true
  if (u.startsWith('$')) return true
  if (/^\d{1,5}$/.test(u)) return true
  return false
}

export function isStickerQr(code) {
  const s = String(code || '').trim()
  if (!s || s.toUpperCase().includes('TRBX')) return false
  if (/^[!*]/.test(s)) return true
  return /^[A-Za-zА-Яа-яЁё0-9]{6,12}$/.test(s) && /[A-Za-zА-Яа-яЁё]/.test(s)
}

/** Пистолет и учёт: только цифры линейного штрихкода, не QR. */
export function isLinearSticker(code) {
  return /^\d{7,14}$/.test(formatManualCode(code))
}

export function isProductCode(code) {
  const s = String(code || '').trim()
  if (!s || isBoxOrRouteCode(s)) return false
  if (/^\d{7,14}$/.test(s)) return true
  return isStickerQr(s)
}

export function pickBestScanCode(codes, opts = {}) {
  const list = [...new Set((codes || []).map((c) => String(c || '').trim()).filter(Boolean))]
  const numeric = list.filter((c) => /^\d{10,14}$/.test(c))
  if (numeric.length) return numeric.sort((a, b) => b.length - a.length)[0]
  const partA = list.filter((c) => /^\d{7,9}$/.test(c))
  if (partA.length) return partA[0]
  if (opts.numericOnly) return ''
  const qr = list.filter(isStickerQr)
  if (qr.length) return qr[0]
  const product = list.filter(isProductCode)
  if (product.length) return product[0]
  return ''
}

/**
 * USB/Bluetooth-пистолет печатает как клавиатура: цифры подряд и Enter.
 * Между символами обычно меньше 0.12 сек — так отличаем от ручного набора.
 */
export function createHidWedge({ maxGapMs = 120, minLen = 6 } = {}) {
  let buf = ''
  let lastAt = 0

  function reset() {
    buf = ''
    lastAt = 0
  }

  function push(key, now = Date.now(), mods = {}) {
    if (mods.ctrlKey || mods.metaKey || mods.altKey) return { kind: 'ignore' }
    if (key === 'Shift' || key === 'Unidentified' || key === 'Dead') return { kind: 'ignore' }

    if (key === 'Enter') {
      const code = buf
      reset()
      if (code.length >= minLen) return { kind: 'complete', code }
      return { kind: 'enter' }
    }

    if (typeof key === 'string' && key.length === 1) {
      if (lastAt && now - lastAt > maxGapMs) buf = ''
      lastAt = now
      buf += key
      return { kind: 'char', buf }
    }

    return { kind: 'ignore' }
  }

  return { push, reset, buffer: () => buf }
}

export function formatReceivedAt(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('ru-RU', {
    timeZone: 'Europe/Moscow',
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).replace(',', '')
}

/** Одно поле: «35548064307» или «3554806 4307». */
export function formatManualCode(raw) {
  const s = String(raw || '').trim()
  if (!s) return ''
  const parts = s.replace(/[^\d\s]/g, ' ').trim().split(/\s+/).filter(Boolean)
  if (parts.length === 2 && parts.every((p) => /^\d+$/.test(p))) return parts[0] + parts[1]
  const digits = s.replace(/\D/g, '')
  if (digits.length >= 7) return digits
  return s
}

/** Только линейные штрихкоды. QR наклейки WB нам не нужен. */
export const LINEAR_DETECTOR_FORMATS = [
  'code_128',
  'code_39',
  'ean_13',
  'ean_8',
  'codabar',
  'itf',
]

/** html5-qrcode: без QR_CODE=0 и DATA_MATRIX=6 */
export const ZXING_LINEAR_FORMATS = [2, 3, 4, 5, 8, 9, 10]

export function html5QrcodeLinearConfig() {
  return { verbose: false, formatsToSupport: ZXING_LINEAR_FORMATS }
}

export function dropStickerQr(codes) {
  return (codes || []).map((c) => String(c || '').trim()).filter((c) => c && !isStickerQr(c))
}

export function makeBarcodeDetector() {
  if (typeof BarcodeDetector === 'undefined') return null
  const tries = [
    LINEAR_DETECTOR_FORMATS,
    ['code_128', 'ean_13'],
    ['code_128'],
  ]
  for (const formats of tries) {
    try {
      return new BarcodeDetector({ formats })
    } catch {
      /* Safari может не знать формат */
    }
  }
  return null
}

export function parseStickerDigits(raw) {
  const s = String(raw || '').trim()
  if (!s) return ''
  try {
    const jsonText = s.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '')
    const start = jsonText.indexOf('{')
    const end = jsonText.lastIndexOf('}')
    if (start >= 0 && end > start) {
      const j = JSON.parse(jsonText.slice(start, end + 1))
      if (j && typeof j === 'object') {
        if (j.code) return formatManualCode(String(j.code))
        const a = String(j.part_a || j.partA || '').replace(/\D/g, '')
        const b = String(j.part_b || j.partB || '').replace(/\D/g, '')
        if (a && b) return formatManualCode(`${a} ${b}`)
      }
    }
  } catch { /* не JSON */ }
  const nums = s.match(/\d{4,14}/g) || []
  const seven = nums.filter((n) => n.length >= 7 && n.length <= 9)
  const four = nums.filter((n) => n.length === 4)
  if (seven.length && four.length) return seven[0] + four[0]
  const glued = nums.filter((n) => n.length >= 11 && n.length <= 14)
  if (glued.length) return glued.sort((a, b) => b.length - a.length)[0]
  return formatManualCode(s)
}

export function hintForMiss(code) {
  const u = String(code || '').toUpperCase()
  if (u.includes('TRBX')) return 'Это QR короба, не товара. Наведите на наклейку WB.'
  if (String(code || '').startsWith('$')) return 'Это код сортировки. Наведите на наклейку WB.'
  if (isStickerQr(code)) {
    return 'Пробит QR. Нужны чёрные полоски штрихкода сверху или снизу наклейки — не квадрат.'
  }
  return 'Этот код не найден. Пробейте чёрные полоски штрихкода или введите номер.'
}
