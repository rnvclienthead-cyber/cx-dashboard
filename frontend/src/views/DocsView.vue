<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import {
  BookOpen, Server, Layers, GitFork, Rocket, Plug,
  Download, ChevronRight, Database, Globe, Cpu, Box,
  Users, Shield, Clock, AlertTriangle, CheckCircle2, RefreshCw
} from 'lucide-vue-next'
import { SYNC_CATALOG } from '../syncCatalog'

const activeSection = ref('overview')
const rolesExpanded = ref(false)
const showMobileNav = ref(false)

const selectSection = (id) => {
  activeSection.value = id
  showMobileNav.value = false
}

const deployBackendSteps = [
  { t: 'Загрузи изменённые файлы', code: '# Один файл:\nscp backend/routers/ФАЙЛ.py \\\n  root@72.56.240.22:/root/cx-dashboard/backend/routers/\n\n# Вся папка backend:\nrsync -avz backend/ root@72.56.240.22:/root/cx-dashboard/backend/' },
  { t: 'Перезапусти сервис',        code: 'ssh root@72.56.240.22 "systemctl restart cx-backend"' },
  { t: 'Проверь статус',             code: 'ssh root@72.56.240.22 "systemctl status cx-backend"' },
  { t: 'Посмотри логи (если ошибка)',code: 'ssh root@72.56.240.22 "journalctl -u cx-backend -n 50"' },
]

const diagCmds = [
  { t: 'Все сервисы работают?',           c: 'ssh root@72.56.240.22 "systemctl status cx-backend nginx postgresql"' },
  { t: 'Бэкенд отвечает?',               c: 'curl https://cxvo.ru/api/v1/health/db\n# Ожидается: {"db_status": "ok"}' },
  { t: 'Логи в реальном времени',         c: 'ssh root@72.56.240.22 "journalctl -u cx-backend -f"' },
  { t: 'Перезапуск всего (крайний случай)',c: 'ssh root@72.56.240.22 "systemctl restart cx-backend nginx"' },
]

const sections = [
  { id: 'overview',      label: 'Обзор системы',      icon: 'Server' },
  { id: 'modules',       label: 'Модули',              icon: 'Layers' },
  { id: 'syncs',         label: 'Синхронизаторы',      icon: 'RefreshCw' },
  { id: 'processes',     label: 'Процессы и RACI',     icon: 'GitFork' },
  { id: 'deploy',        label: 'Деплой',              icon: 'Rocket' },
  { id: 'integrations',  label: 'Интеграции',          icon: 'Plug' },
]

// Справочник синхронизаторов для раздела «Синхронизаторы»
const syncCatalog = SYNC_CATALOG

// Переход по прямой ссылке из журнала: /docs?section=syncs
const route = useRoute()
onMounted(() => {
  const sec = route.query.section
  if (sec && sections.some(s => s.id === sec)) activeSection.value = sec
})

function downloadSection() {
  const sectionEl = document.getElementById('doc-content')
  if (!sectionEl) return
  const title = sections.find(s => s.id === activeSection.value)?.label || 'Документация'
  const html = `<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${title} — CX Dashboard</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; color: #1e293b; background: #fff; padding: 32px; max-width: 960px; margin: 0 auto; line-height: 1.6; }
    h1 { font-size: 26px; font-weight: 900; color: #0f172a; margin-bottom: 8px; }
    h2 { font-size: 18px; font-weight: 800; color: #0f172a; margin: 28px 0 12px; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px; }
    h3 { font-size: 15px; font-weight: 700; color: #334155; margin: 20px 0 8px; }
    p { margin: 8px 0; color: #475569; }
    table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; }
    th { background: #f1f5f9; text-align: left; padding: 8px 12px; font-weight: 700; color: #475569; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; border: 1px solid #e2e8f0; }
    td { padding: 8px 12px; border: 1px solid #e2e8f0; vertical-align: top; }
    tr:hover td { background: #f8fafc; }
    code { background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-size: 12px; font-family: 'Courier New', monospace; color: #0f172a; }
    pre { background: #1e293b; color: #e2e8f0; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 12px; margin: 12px 0; line-height: 1.5; }
    pre code { background: none; color: inherit; padding: 0; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 700; }
    .badge-r { background: #dcfce7; color: #15803d; }
    .badge-a { background: #fef9c3; color: #854d0e; }
    .badge-c { background: #dbeafe; color: #1d4ed8; }
    .badge-i { background: #f3f4f6; color: #6b7280; }
    .badge-ra { background: #d1fae5; color: #065f46; }
    .warn { background: #fef2f2; border-left: 4px solid #ef4444; padding: 10px 14px; margin: 12px 0; border-radius: 4px; color: #7f1d1d; font-weight: 600; }
    .info { background: #eff6ff; border-left: 4px solid #3b82f6; padding: 10px 14px; margin: 12px 0; border-radius: 4px; color: #1e3a5f; }
    .step { display: flex; gap: 12px; margin: 10px 0; align-items: flex-start; }
    .step-num { background: #6366f1; color: #fff; width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }
    .arch-box { border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 14px; background: #f8fafc; margin: 4px 0; }
    @media print { body { padding: 16px; } }
  </style>
</head>
<body>
  <h1>${title}</h1>
  <p style="color:#94a3b8;font-size:12px;margin-bottom:24px;">CX Dashboard — экспорт от ${new Date().toLocaleDateString('ru-RU')}</p>
  ${sectionEl.innerHTML}
</body>
</html>`

  const blob = new Blob([html], { type: 'text/html;charset=utf-8' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = `cx-docs-${activeSection.value}-${new Date().toISOString().slice(0,10)}.html`
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="flex h-full bg-slate-50 overflow-hidden">

    <!-- Левый навигатор (десктоп) -->
    <aside class="hidden md:flex w-56 flex-shrink-0 bg-white border-r border-slate-200 flex-col">
      <div class="p-5 border-b border-slate-200">
        <div class="flex items-center gap-2">
          <BookOpen class="w-5 h-5 text-indigo-600" />
          <span class="font-black text-slate-800 text-sm">Документация</span>
        </div>
        <p class="text-xs text-slate-400 mt-1">Система CX Dashboard</p>
      </div>
      <nav class="flex-1 p-3 space-y-1">
        <button
          v-for="s in sections"
          :key="s.id"
          @click="selectSection(s.id)"
          :class="[
            'w-full text-left px-3 py-2.5 rounded-xl text-sm font-semibold transition-all flex items-center gap-2.5',
            activeSection === s.id
              ? 'bg-indigo-50 text-indigo-700'
              : 'text-slate-600 hover:bg-slate-100'
          ]"
        >
          <Server     v-if="s.icon==='Server'"    class="w-4 h-4 flex-shrink-0" />
          <Layers     v-if="s.icon==='Layers'"    class="w-4 h-4 flex-shrink-0" />
          <GitFork    v-if="s.icon==='GitFork'"   class="w-4 h-4 flex-shrink-0" />
          <Rocket     v-if="s.icon==='Rocket'"    class="w-4 h-4 flex-shrink-0" />
          <Plug       v-if="s.icon==='Plug'"      class="w-4 h-4 flex-shrink-0" />
          <RefreshCw  v-if="s.icon==='RefreshCw'" class="w-4 h-4 flex-shrink-0" />
          {{ s.label }}
        </button>
      </nav>
    </aside>

    <!-- Основной контент -->
    <main class="flex-1 overflow-y-auto">
      <div class="max-w-4xl mx-auto p-4 md:p-8">

        <!-- Мобильная навигация -->
        <div class="md:hidden mb-4">
          <button @click="showMobileNav = !showMobileNav"
            class="w-full flex items-center justify-between px-4 py-3 bg-white border border-slate-200 rounded-2xl shadow-sm text-sm font-bold text-slate-700">
            <div class="flex items-center gap-2">
              <BookOpen class="w-4 h-4 text-indigo-600" />
              {{ sections.find(s=>s.id===activeSection)?.label }}
            </div>
            <ChevronRight class="w-4 h-4 transition-transform" :class="{'rotate-90': showMobileNav}" />
          </button>
          <div v-if="showMobileNav" class="mt-2 bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-md">
            <button v-for="s in sections" :key="s.id" @click="selectSection(s.id)"
              :class="['w-full text-left px-4 py-3 text-sm font-semibold transition-all flex items-center gap-2.5 border-b border-slate-100 last:border-0', activeSection === s.id ? 'bg-indigo-50 text-indigo-700' : 'text-slate-600 hover:bg-slate-50']">
              <Server     v-if="s.icon==='Server'"    class="w-4 h-4 flex-shrink-0" />
              <Layers     v-if="s.icon==='Layers'"    class="w-4 h-4 flex-shrink-0" />
              <GitFork    v-if="s.icon==='GitFork'"   class="w-4 h-4 flex-shrink-0" />
              <Rocket     v-if="s.icon==='Rocket'"    class="w-4 h-4 flex-shrink-0" />
              <Plug       v-if="s.icon==='Plug'"      class="w-4 h-4 flex-shrink-0" />
              {{ s.label }}
            </button>
          </div>
        </div>

        <!-- Шапка секции -->
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6 md:mb-8">
          <div>
            <h1 class="text-xl md:text-2xl font-black text-slate-900">
              {{ sections.find(s=>s.id===activeSection)?.label }}
            </h1>
            <p class="text-sm text-slate-400 mt-1">CX Dashboard · Внутренняя документация</p>
          </div>
          <button
            @click="downloadSection"
            class="flex items-center gap-2 px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-bold rounded-xl transition-colors shadow-sm self-start sm:self-auto"
          >
            <Download class="w-4 h-4" />
            Скачать раздел
          </button>
        </div>

        <!-- Контент (рендерится для скачивания) -->
        <div id="doc-content">

          <!-- ══════════════════════════════════════════════════════════════ -->
          <!-- РАЗДЕЛ 1: ОБЗОР СИСТЕМЫ -->
          <!-- ══════════════════════════════════════════════════════════════ -->
          <template v-if="activeSection==='overview'">

            <p class="text-slate-600 text-sm mb-6 leading-relaxed">
              CX Dashboard — внутренний инструмент автоматизации клиентского опыта. Система собирает возвраты, отзывы и заказы с трёх маркетплейсов (WB, Яндекс Маркет, OZON), тегирует их с помощью ИИ, считает PPM, управляет доотправками через СДЭК, мониторит WB-чат и показывает ключевые метрики руководству. Реальное состояние системы (синхронизации, ошибки) — в разделе <strong>Статус автоматизации (/sync)</strong>.
            </p>

            <!-- Архитектура -->
            <h2 class="text-lg font-black text-slate-900 mb-4 flex items-center gap-2">
              <Globe class="w-5 h-5 text-indigo-500" /> Архитектура системы
            </h2>

            <div class="bg-white rounded-2xl border border-slate-200 p-6 mb-8 overflow-x-auto">
              <!-- Архитектурная схема -->
              <div class="space-y-3 min-w-[580px]">

                <!-- Строка 1: Внешние источники -->
                <div>
                  <p class="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2">Внешние источники</p>
                  <div class="grid grid-cols-4 gap-2">
                    <div v-for="src in ['Wildberries API','Яндекс Маркет API','OZON API','Google Sheets']" :key="src"
                         class="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 text-center">
                      <p class="text-xs font-bold text-slate-700">{{ src }}</p>
                    </div>
                  </div>
                </div>

                <!-- Стрелка вниз -->
                <div class="flex justify-center">
                  <div class="flex flex-col items-center gap-0.5">
                    <div class="w-px h-4 bg-slate-300"></div>
                    <div class="text-slate-300 text-xs">▼</div>
                  </div>
                </div>

                <!-- Строка 2: Воркеры -->
                <div>
                  <p class="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2">Воркеры (cron) + постоянные процессы (asyncio)</p>
                  <div class="grid grid-cols-4 gap-2">
                    <div v-for="w in [{l:'worker.py',s:'WB синхронизация'},{l:'ym_worker.py',s:'ЯМ синхронизация'},{l:'ozon_worker.py',s:'OZON синхронизация'},{l:'wb_chat.py',s:'WB чат — мониторинг #от'}]" :key="w.l"
                         class="bg-amber-50 border border-amber-200 rounded-xl px-3 py-2.5 text-center">
                      <p class="text-xs font-bold text-amber-800 font-mono">{{ w.l }}</p>
                      <p class="text-[11px] text-amber-600 mt-0.5">{{ w.s }}</p>
                    </div>
                  </div>
                </div>

                <!-- Стрелка вниз -->
                <div class="flex justify-center">
                  <div class="flex flex-col items-center gap-0.5">
                    <div class="w-px h-4 bg-slate-300"></div>
                    <div class="text-slate-300 text-xs">▼</div>
                  </div>
                </div>

                <!-- Строка 3: БД -->
                <div>
                  <p class="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2">База данных</p>
                  <div class="bg-blue-50 border-2 border-blue-300 rounded-xl px-4 py-3 text-center">
                    <div class="flex items-center justify-center gap-2">
                      <Database class="w-5 h-5 text-blue-600" />
                      <p class="text-sm font-black text-blue-800">PostgreSQL · cx_dashboard</p>
                    </div>
                    <p class="text-xs text-blue-500 mt-1">127.0.0.1:5432 · ~20 таблиц</p>
                  </div>
                </div>

                <!-- Стрелка вниз -->
                <div class="flex justify-center">
                  <div class="flex flex-col items-center gap-0.5">
                    <div class="w-px h-4 bg-slate-300"></div>
                    <div class="text-slate-300 text-xs">▼</div>
                  </div>
                </div>

                <!-- Строка 4: Бэкенд -->
                <div>
                  <p class="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2">Бэкенд</p>
                  <div class="bg-emerald-50 border-2 border-emerald-300 rounded-xl px-4 py-3 text-center">
                    <p class="text-sm font-black text-emerald-800">FastAPI · cx-backend.service</p>
                    <p class="text-xs text-emerald-500 mt-1">Python · порт 8000 · systemd</p>
                  </div>
                </div>

                <!-- Стрелка вниз -->
                <div class="flex justify-center">
                  <div class="flex flex-col items-center gap-0.5">
                    <div class="w-px h-4 bg-slate-300"></div>
                    <div class="text-slate-300 text-xs">▼</div>
                  </div>
                </div>

                <!-- Строка 5: Nginx -->
                <div>
                  <p class="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2">Веб-сервер</p>
                  <div class="bg-slate-100 border border-slate-300 rounded-xl px-4 py-2.5 text-center">
                    <p class="text-sm font-bold text-slate-700">Nginx · реверс-прокси</p>
                  </div>
                </div>

                <!-- Стрелки вниз (развилка) -->
                <div class="flex justify-center">
                  <div class="flex flex-col items-center gap-0.5">
                    <div class="w-px h-4 bg-slate-300"></div>
                    <div class="text-slate-300 text-xs">▼</div>
                  </div>
                </div>

                <!-- Строка 6: Фронтенд + доп. сервисы -->
                <div>
                  <p class="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2">Доставка контента · Внешние сервисы</p>
                  <div class="grid grid-cols-4 gap-2">
                    <div class="bg-indigo-50 border border-indigo-200 rounded-xl px-3 py-2.5 text-center col-span-2">
                      <p class="text-xs font-bold text-indigo-800">Vue.js фронтенд</p>
                      <p class="text-[11px] text-indigo-500 mt-0.5">/var/www/cx-dashboard/</p>
                    </div>
                    <div class="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 text-center">
                      <p class="text-xs font-bold text-slate-700">СДЭК API</p>
                      <p class="text-[11px] text-slate-400 mt-0.5">доотправки</p>
                    </div>
                    <div class="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 text-center">
                      <p class="text-xs font-bold text-slate-700">Яндекс S3</p>
                      <p class="text-[11px] text-slate-400 mt-0.5">фото возвратов</p>
                    </div>
                  </div>
                </div>

              </div>
            </div>

            <!-- Технологический стек -->
            <h2 class="text-lg font-black text-slate-900 mb-4 flex items-center gap-2">
              <Cpu class="w-5 h-5 text-indigo-500" /> Технологический стек
            </h2>
            <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden mb-8">
              <table class="w-full text-sm">
                <thead>
                  <tr class="bg-slate-50 border-b border-slate-200">
                    <th class="text-left px-5 py-3 font-bold text-slate-500 text-xs uppercase tracking-wider">Компонент</th>
                    <th class="text-left px-5 py-3 font-bold text-slate-500 text-xs uppercase tracking-wider">Технология</th>
                    <th class="text-left px-5 py-3 font-bold text-slate-500 text-xs uppercase tracking-wider">Расположение</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="r in [
                    {c:'Фронтенд',t:'Vue.js 3 + Vite + Tailwind',l:'/var/www/cx-dashboard/'},
                    {c:'Бэкенд',t:'Python 3 + FastAPI',l:'/root/cx-dashboard/'},
                    {c:'База данных',t:'PostgreSQL · cx_dashboard',l:'localhost:5432'},
                    {c:'Веб-сервер',t:'Nginx (реверс-прокси)',l:'порт 80/443'},
                    {c:'Хранилище фото',t:'Яндекс Object Storage (S3)',l:'cx-dashboard-media'},
                    {c:'Сервис',t:'systemd cx-backend',l:'VPS 72.56.240.22'},
                  ]" :key="r.c" class="border-b border-slate-100 hover:bg-slate-50">
                    <td class="px-5 py-3 font-semibold text-slate-800">{{ r.c }}</td>
                    <td class="px-5 py-3 text-slate-600 font-mono text-xs">{{ r.t }}</td>
                    <td class="px-5 py-3 text-slate-500 font-mono text-xs">{{ r.l }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- Воркеры -->
            <h2 class="text-lg font-black text-slate-900 mb-4 flex items-center gap-2">
              <Clock class="w-5 h-5 text-indigo-500" /> Воркеры — расписание
            </h2>
            <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden mb-8">
              <table class="w-full text-sm">
                <thead>
                  <tr class="bg-slate-50 border-b border-slate-200">
                    <th class="text-left px-5 py-3 font-bold text-slate-500 text-xs uppercase tracking-wider">Воркер</th>
                    <th class="text-left px-5 py-3 font-bold text-slate-500 text-xs uppercase tracking-wider">Расписание</th>
                    <th class="text-left px-5 py-3 font-bold text-slate-500 text-xs uppercase tracking-wider">Что делает</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="r in [
                    {w:'worker.py hourly',s:'каждые 2 ч',d:'WB: возвраты + отзывы'},
                    {w:'worker.py daily',s:'03:00',d:'WB: заказы, ассортимент, логистика, инвойсы, COGs'},
                    {w:'ym_worker.py hourly',s:'каждые 2 ч',d:'ЯМ: заказы, возвраты, отзывы, вопросы'},
                    {w:'ym_worker.py daily',s:'03:00',d:'ЯМ: ассортимент, цены, запуск финотчётов'},
                    {w:'ym_worker.py reports',s:'05:00 / 09:00 / 13:00',d:'ЯМ: скачивание готовых финансовых отчётов'},
                    {w:'ozon_worker.py hourly',s:'каждые 2 ч',d:'OZON: возвраты, статусы'},
                    {w:'ozon_worker.py daily',s:'03:00',d:'OZON: заказы (30-дн. окно)'},
                    {w:'wb_chat.py',s:'постоянно (asyncio)',d:'Мониторинг WB-чата, триггер #от'},
                  ]" :key="r.w" class="border-b border-slate-100 hover:bg-slate-50">
                    <td class="px-5 py-3 font-mono text-xs text-amber-700 bg-amber-50/30 font-semibold">{{ r.w }}</td>
                    <td class="px-5 py-3 text-slate-600 text-xs">{{ r.s }}</td>
                    <td class="px-5 py-3 text-slate-600 text-xs">{{ r.d }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- Роли -->
            <h2 class="text-lg font-black text-slate-900 mb-4 flex items-center gap-2">
              <Users class="w-5 h-5 text-indigo-500" /> Роли пользователей
            </h2>
            <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden">
              <table class="w-full text-sm">
                <thead>
                  <tr class="bg-slate-50 border-b border-slate-200">
                    <th class="text-left px-5 py-3 font-bold text-slate-500 text-xs uppercase tracking-wider">Роль</th>
                    <th class="text-left px-5 py-3 font-bold text-slate-500 text-xs uppercase tracking-wider">Описание</th>
                    <th class="text-left px-5 py-3 font-bold text-slate-500 text-xs uppercase tracking-wider">Доступ</th>
                  </tr>
                </thead>
                <tbody>
                  <tr class="border-b border-slate-100 hover:bg-slate-50">
                    <td class="px-5 py-3"><span class="font-mono text-xs bg-red-50 text-red-700 px-2 py-1 rounded-lg font-bold">admin</span></td>
                    <td class="px-5 py-3 text-slate-600">Администратор</td>
                    <td class="px-5 py-3 text-slate-600">Всё, включая управление пользователями и документацию</td>
                  </tr>
                  <tr class="hover:bg-slate-50">
                    <td class="px-5 py-3"><span class="font-mono text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded-lg font-bold">custom</span></td>
                    <td class="px-5 py-3 text-slate-600">Настраиваемые роли</td>
                    <td class="px-5 py-3 text-slate-600">Набор модулей × набор маркетплейсов (настраивается в Admin)</td>
                  </tr>
                </tbody>
              </table>
            </div>

          </template>

          <!-- ══════════════════════════════════════════════════════════════ -->
          <!-- РАЗДЕЛ 2: МОДУЛИ -->
          <!-- ══════════════════════════════════════════════════════════════ -->
          <template v-else-if="activeSection==='modules'">

            <div class="grid grid-cols-3 gap-3 mb-8">
              <div v-for="m in [
                {n:'Главный дашборд',p:'/',c:'bg-emerald-50 border-emerald-200 text-emerald-700'},
                {n:'Статус автоматизации',p:'/sync',c:'bg-blue-50 border-blue-200 text-blue-700'},
                {n:'ИИ Тегирование',p:'/ai-tagging',c:'bg-blue-50 border-blue-200 text-blue-700'},
                {n:'Модерация тегов',p:'/moderation',c:'bg-blue-50 border-blue-200 text-blue-700'},
                {n:'Обучение ИИ',p:'/ai-training',c:'bg-blue-50 border-blue-200 text-blue-700'},
                {n:'Карта проблем',p:'/production',c:'bg-emerald-50 border-emerald-200 text-emerald-700'},
                {n:'PPM и Акты',p:'/ppm',c:'bg-emerald-50 border-emerald-200 text-emerald-700'},
                {n:'Голос покупателя',p:'/voc',c:'bg-emerald-50 border-emerald-200 text-emerald-700'},
                {n:'Рейтинг товаров',p:'/ratings',c:'bg-emerald-50 border-emerald-200 text-emerald-700'},
                {n:'Финансовые потери',p:'/finances',c:'bg-rose-50 border-rose-200 text-rose-700'},
                {n:'Доотправки',p:'/reshipment',c:'bg-emerald-50 border-emerald-200 text-emerald-700'},
                {n:'Реестр актов',p:'/registry',c:'bg-emerald-50 border-emerald-200 text-emerald-700'},
                {n:'Панель управления',p:'/admin',c:'bg-red-50 border-red-200 text-red-700'},
                {n:'Документация',p:'/docs',c:'bg-indigo-50 border-indigo-200 text-indigo-700'},
              ]" :key="m.n"
                 :class="['border rounded-xl px-4 py-3', m.c]">
                <p class="font-bold text-sm">{{ m.n }}</p>
                <p class="font-mono text-xs opacity-70 mt-0.5">{{ m.p }}</p>
              </div>
            </div>

            <!-- Подробно каждый модуль -->
            <div class="space-y-6">

              <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                <div class="px-6 py-4 bg-emerald-50 border-b border-emerald-100">
                  <h3 class="font-black text-emerald-900">1. Главный дашборд</h3>
                  <p class="text-xs text-emerald-600 mt-0.5">Исполнительный обзор ключевых метрик</p>
                </div>
                <div class="p-6">
                  <table class="w-full text-sm mb-0">
                    <thead><tr class="border-b border-slate-100"><th class="text-left pb-2 text-xs text-slate-400 font-bold uppercase tracking-wider">Метрика</th><th class="text-left pb-2 text-xs text-slate-400 font-bold uppercase tracking-wider">Источник</th><th class="text-left pb-2 text-xs text-slate-400 font-bold uppercase tracking-wider">Формула</th></tr></thead>
                    <tbody class="divide-y divide-slate-100">
                      <tr v-for="r in [
                        {m:'Прямые потери ₽',s:'wb_claims + wb_cogs',f:'Σ себестоимость одобренных возвратов'},
                        {m:'PPM %',s:'wb_claims / wb_orders',f:'одобр. возвраты ÷ заказы × 100'},
                        {m:'CSAT %',s:'wb_feedbacks + ym_feedbacks',f:'оценки ≥4 ÷ всего отзывов × 100'},
                        {m:'ИИ обработано',s:'wb_claims.cat_1',f:'записи с заполненным тегом cat_1'},
                        {m:'Топ-5 проблемных SKU',s:'wb_claims + wb_cogs',f:'сортировка по сумме прямых потерь'},
                        {m:'Потеря выручки',s:'wb_logistics.finish_price',f:'Σ цен продаж возвращённых товаров'},
                        {m:'Доотправки — воронка',s:'reshipment_requests',f:'pending → approved → warehouse → shipped → delivered'},
                        {m:'Доотправки — затраты',s:'reshipment_requests.delivery_cost',f:'Σ стоимости доставок по отправленным заявкам'},
                        {m:'Коэф. реализации PPM',s:'ppm_invoices',f:'акты отправлены / с ответом / с КС / с датой устранения'},
                      ]" :key="r.m">
                        <td class="py-2.5 pr-4 font-semibold text-slate-800">{{ r.m }}</td>
                        <td class="py-2.5 pr-4 font-mono text-xs text-blue-600">{{ r.s }}</td>
                        <td class="py-2.5 text-slate-500 text-xs">{{ r.f }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>

              <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                <div class="px-6 py-4 bg-blue-50 border-b border-blue-100">
                  <h3 class="font-black text-blue-900">3–4. ИИ Тегирование + Модерация</h3>
                  <p class="text-xs text-blue-600 mt-0.5">Автоматическая разметка → ручная проверка</p>
                </div>
                <div class="p-6 space-y-4">
                  <div class="flex gap-3">
                    <div class="flex-1 bg-blue-50 rounded-xl p-4 border border-blue-100">
                      <p class="font-bold text-blue-800 text-sm mb-2">ИИ ставит теги</p>
                      <div class="space-y-1.5 text-xs text-blue-700">
                        <div class="flex items-center gap-2"><span class="bg-blue-200 text-blue-800 px-2 py-0.5 rounded font-bold">cat_1</span> Верхний уровень: «Дефект производства»</div>
                        <div class="flex items-center gap-2"><span class="bg-blue-200 text-blue-800 px-2 py-0.5 rounded font-bold">cat_2</span> Конкретика: «Трещина корпуса»</div>
                        <div class="flex items-center gap-2"><span class="bg-blue-200 text-blue-800 px-2 py-0.5 rounded font-bold">cat_3</span> Деталь: «Боковая стенка»</div>
                      </div>
                    </div>
                    <div class="flex items-center text-slate-300 text-2xl">→</div>
                    <div class="flex-1 bg-amber-50 rounded-xl p-4 border border-amber-100">
                      <p class="font-bold text-amber-800 text-sm mb-2">Менеджер проверяет</p>
                      <p class="text-xs text-amber-700">Видит возврат, фото, теги ИИ → подтверждает или исправляет. Исправление записывается в <span class="font-mono">Корректировка</span></p>
                    </div>
                    <div class="flex items-center text-slate-300 text-2xl">→</div>
                    <div class="flex-1 bg-emerald-50 rounded-xl p-4 border border-emerald-100">
                      <p class="font-bold text-emerald-800 text-sm mb-2">Обучение ИИ</p>
                      <p class="text-xs text-emerald-700">Исправления накапливаются → улучшают точность следующих тегирований</p>
                    </div>
                  </div>
                  <p class="text-xs text-slate-500"><strong>Условие очереди:</strong> одобренные возвраты WB с тегами, у которых поле «Корректировка» пустое</p>
                </div>
              </div>

              <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                <div class="px-6 py-4 bg-emerald-50 border-b border-emerald-100">
                  <h3 class="font-black text-emerald-900">8. PPM и Акты</h3>
                  <p class="text-xs text-emerald-600 mt-0.5">Расчёт дефектности и генерация претензионных актов PDF</p>
                </div>
                <div class="p-6">
                  <div class="bg-slate-50 rounded-xl px-5 py-4 mb-4 inline-flex items-center gap-4">
                    <div class="text-center">
                      <p class="text-xs text-slate-400 font-semibold">PPM формула</p>
                      <p class="text-lg font-black text-slate-800 mt-1">дефекты ÷ отгрузки × 100</p>
                    </div>
                  </div>
                  <div class="grid grid-cols-2 gap-3 mt-4">
                    <div v-for="g in [{t:'ABC',d:'A = топ 80% выручки, B = след. 15%, C = остальные 5%'},{t:'XYZ',d:'X = стабильный спрос, Y = сезонный, Z = нерегулярный'}]" :key="g.t"
                         class="bg-slate-50 border border-slate-200 rounded-xl px-4 py-3">
                      <span class="font-black text-indigo-700 text-sm">{{ g.t }}:</span>
                      <p class="text-xs text-slate-600 mt-1">{{ g.d }}</p>
                    </div>
                  </div>
                </div>
              </div>

              <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                <div class="px-6 py-4 bg-rose-50 border-b border-rose-100">
                  <h3 class="font-black text-rose-900">10. Финансовые потери</h3>
                  <p class="text-xs text-rose-600 mt-0.5">Детальный учёт потерь от возвратов с разбивкой по SKU, каналам и периодам</p>
                </div>
                <div class="p-6">
                  <div class="grid grid-cols-2 gap-3">
                    <div v-for="g in [
                      {t:'Прямые потери',d:'Себестоимость (wb_cogs) × количество одобренных возвратов по каждому SKU'},
                      {t:'Потеря выручки',d:'finish_price из wb_logistics — сумма продаж по возвращённым товарам'},
                      {t:'Логистика',d:'Стоимость обратной доставки и хранения из wb_logistics'},
                      {t:'Фильтры',d:'По маркетплейсу, периоду (быстрые пресеты месяцев), категории, SKU'},
                    ]" :key="g.t" class="bg-slate-50 border border-slate-200 rounded-xl px-4 py-3">
                      <span class="font-black text-rose-700 text-sm">{{ g.t }}:</span>
                      <p class="text-xs text-slate-600 mt-1">{{ g.d }}</p>
                    </div>
                  </div>
                </div>
              </div>

              <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                <div class="px-6 py-4 bg-amber-50 border-b border-amber-100">
                  <h3 class="font-black text-amber-900">7. Голос покупателя (VoC)</h3>
                  <p class="text-xs text-amber-600 mt-0.5">Анализ отзывов и вопросов с тегами ИИ и трендами</p>
                </div>
                <div class="p-6">
                  <div class="grid grid-cols-2 gap-3">
                    <div v-for="g in [
                      {t:'Источники',d:'Отзывы WB (wb_feedbacks) и ЯМ (ym_feedbacks), вопросы ЯМ (ym_questions)'},
                      {t:'Теги ИИ',d:'cat_1 / cat_2 / cat_3 — трёхуровневая классификация тематики отзыва'},
                      {t:'Тональность',d:'Позитивные (оценка ≥4) и негативные (≤3) — динамика во времени'},
                      {t:'Фильтры',d:'Маркетплейс, период, категория, тег, минимальная оценка'},
                    ]" :key="g.t" class="bg-slate-50 border border-slate-200 rounded-xl px-4 py-3">
                      <span class="font-black text-amber-700 text-sm">{{ g.t }}:</span>
                      <p class="text-xs text-slate-600 mt-1">{{ g.d }}</p>
                    </div>
                  </div>
                </div>
              </div>

              <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                <div class="px-6 py-4 bg-emerald-50 border-b border-emerald-100">
                  <h3 class="font-black text-emerald-900">9. Рейтинг товаров</h3>
                  <p class="text-xs text-emerald-600 mt-0.5">Средние оценки и число отзывов в разрезе SKU</p>
                </div>
                <div class="p-6">
                  <div class="grid grid-cols-2 gap-3">
                    <div v-for="g in [
                      {t:'Источник',d:'wb_feedbacks и ym_feedbacks — оценки покупателей по артикулу'},
                      {t:'Средний рейтинг',d:'Средняя оценка (1–5) по каждому SKU за выбранный период'},
                      {t:'Динамика',d:'Сравнение текущего и предыдущего периода — рост / падение рейтинга'},
                      {t:'Фильтры',d:'Период (быстрые пресеты месяцев), маркетплейс, минимальное число отзывов'},
                    ]" :key="g.t" class="bg-slate-50 border border-slate-200 rounded-xl px-4 py-3">
                      <span class="font-black text-emerald-700 text-sm">{{ g.t }}:</span>
                      <p class="text-xs text-slate-600 mt-1">{{ g.d }}</p>
                    </div>
                  </div>
                </div>
              </div>

              <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                <div class="px-6 py-4 bg-slate-50 border-b border-slate-100">
                  <h3 class="font-black text-slate-900">Все статусы заявки на доотправку</h3>
                </div>
                <div class="p-6">
                  <div class="grid grid-cols-2 gap-2">
                    <div v-for="s in [
                      {st:'new',              c:'bg-blue-50 border-blue-200 text-blue-700',     d:'Клиент заполнил форму, ждёт проверки КС'},
                      {st:'matched',          c:'bg-sky-50 border-sky-200 text-sky-700',        d:'КС нашёл заказ WB, привязал srid — готово к одобрению'},
                      {st:'approved',         c:'bg-violet-50 border-violet-200 text-violet-700',d:'КС одобрил, ждёт упаковки на складе'},
                      {st:'packed',           c:'bg-amber-50 border-amber-200 text-amber-700',  d:'Склад упаковал посылку, создаёт отправку в СДЭК'},
                      {st:'shipped',          c:'bg-indigo-50 border-indigo-200 text-indigo-700',d:'СДЭК принял посылку, трек-номер присвоен'},
                      {st:'in_transit',       c:'bg-cyan-50 border-cyan-200 text-cyan-700',     d:'Посылка в пути между городами'},
                      {st:'ready_for_pickup', c:'bg-emerald-50 border-emerald-200 text-emerald-700',d:'Посылка в ПВЗ СДЭК, готова к выдаче'},
                      {st:'delivered',        c:'bg-slate-100 border-slate-200 text-slate-600', d:'Клиент забрал. Авто или по ссылке-подтверждению'},
                      {st:'returned',         c:'bg-orange-50 border-orange-200 text-orange-700',d:'СДЭК: NOT_DELIVERED — посылка возвращается'},
                      {st:'rejected',         c:'bg-red-50 border-red-200 text-red-700',        d:'КС или склад отклонили заявку'},
                    ]" :key="s.st"
                       :class="['border rounded-xl px-4 py-3', s.c]">
                      <p class="font-mono font-bold text-xs">{{ s.st }}</p>
                      <p class="text-xs mt-0.5 opacity-80">{{ s.d }}</p>
                    </div>
                  </div>
                </div>
              </div>

            </div>
          </template>

          <!-- ══════════════════════════════════════════════════════════════ -->
          <!-- РАЗДЕЛ: СИНХРОНИЗАТОРЫ -->
          <!-- ══════════════════════════════════════════════════════════════ -->
          <template v-else-if="activeSection==='syncs'">
            <div class="flex items-start gap-3 mb-6 p-4 bg-indigo-50 border border-indigo-100 rounded-2xl">
              <RefreshCw class="w-5 h-5 text-indigo-600 flex-shrink-0 mt-0.5" />
              <p class="text-sm text-slate-600 leading-relaxed">
                Справочник фоновых синхронизаторов и воркеров. Их названия видны в
                <span class="font-semibold text-slate-700">Системном журнале</span> — кликните по названию синка, чтобы попасть сюда.
                Здесь можно понять, за что отвечает каждый процесс.
              </p>
            </div>

            <div v-for="mp in ['WB','ЯМ','OZON','']" :key="mp || 'other'"
                 v-show="syncCatalog.some(s => s.mp === mp)" class="mb-6">
              <h3 class="text-sm font-black text-slate-500 uppercase tracking-wider mb-3">
                {{ mp === 'WB' ? 'Wildberries' : mp === 'ЯМ' ? 'Яндекс Маркет' : mp === 'OZON' ? 'OZON' : 'Общие' }}
              </h3>
              <div class="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm divide-y divide-slate-100">
                <div v-for="s in syncCatalog.filter(x => x.mp === mp)" :key="s.key"
                     class="p-4 hover:bg-slate-50 transition-colors">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="font-bold text-slate-800">{{ s.label }}</span>
                    <code class="text-[10px] bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded font-mono">{{ s.key }}</code>
                  </div>
                  <p class="text-sm text-slate-500 mt-1">{{ s.desc }}</p>
                </div>
              </div>
            </div>

            <p class="text-xs text-slate-400 mt-2">
              Пачки обработки (ИИ-разметка, пагинация) в журнале сгруппированы автоматически и здесь не перечислены.
            </p>
          </template>

          <!-- ══════════════════════════════════════════════════════════════ -->
          <!-- РАЗДЕЛ 3: ПРОЦЕССЫ И RACI -->
          <!-- ══════════════════════════════════════════════════════════════ -->
          <template v-else-if="activeSection==='processes'">

            <!-- Участники — сворачиваемый блок -->
            <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden mb-6">

              <!-- Шапка-переключатель -->
              <button
                @click="rolesExpanded = !rolesExpanded"
                class="w-full flex items-center justify-between px-5 py-4 hover:bg-slate-50 transition-colors text-left"
              >
                <div class="flex items-center gap-2">
                  <Users class="w-5 h-5 text-indigo-500" />
                  <span class="font-black text-slate-900">Кто есть кто в процессах</span>
                  <span class="text-xs text-slate-400 font-normal ml-1">— 6 участников</span>
                </div>
                <div class="flex items-center gap-1.5">
                  <span class="text-xs text-slate-400">{{ rolesExpanded ? 'свернуть' : 'подробнее' }}</span>
                  <ChevronRight
                    class="w-4 h-4 text-slate-400 transition-transform duration-200"
                    :class="rolesExpanded ? 'rotate-90' : ''"
                  />
                </div>
              </button>

              <!-- Свёрнутый вид: компактная таблица -->
              <div v-if="!rolesExpanded" class="border-t border-slate-100">
                <div class="divide-y divide-slate-100">
                  <div v-for="r in [
                    {k:'ADM', name:'Администратор',            modules:'Панель управления, все модули',                         bc:'bg-red-100 text-red-700'},
                    {k:'MGR', name:'Менеджер по работе с клиентами', modules:'Доотправки, Модерация тегов, ИИ-тегирование',      bc:'bg-amber-100 text-amber-700'},
                    {k:'ANA', name:'Менеджер по качеству / Менеджер CX', modules:'PPM, Карта проблем / Финансы, VoC, Рейтинги', bc:'bg-blue-100 text-blue-700'},
                    {k:'WHS', name:'Склад',                    modules:'Доотправки (этап подтверждения)',                        bc:'bg-emerald-100 text-emerald-700'},
                    {k:'AI',  name:'ИИ-система (авто)',        modules:'Тегирование, генерация PDF-актов',                      bc:'bg-violet-100 text-violet-700'},
                    {k:'WRK', name:'Воркеры (авто)',           modules:'Синхронизация данных из маркетплейсов',                 bc:'bg-slate-200 text-slate-600'},
                  ]" :key="r.k"
                     class="flex items-center gap-3 px-5 py-3">
                    <span :class="['font-mono font-black text-xs px-2.5 py-1 rounded-lg w-12 text-center flex-shrink-0', r.bc]">{{ r.k }}</span>
                    <span class="font-semibold text-sm text-slate-800 w-56 flex-shrink-0">{{ r.name }}</span>
                    <span class="text-xs text-slate-400">{{ r.modules }}</span>
                  </div>
                </div>
              </div>

              <!-- Развёрнутый вид: подробные карточки -->
              <div v-else class="border-t border-slate-100 p-4 grid grid-cols-2 gap-3">

                <div class="bg-slate-50 rounded-xl border border-red-200 overflow-hidden">
                  <div class="bg-red-50 px-4 py-2.5 border-b border-red-200 flex items-center gap-2.5">
                    <span class="font-mono font-black text-xs bg-red-100 text-red-700 px-2 py-1 rounded-lg">ADM</span>
                    <span class="font-black text-slate-800 text-sm">Администратор</span>
                  </div>
                  <div class="px-4 py-3 space-y-2 text-sm text-slate-600">
                    <p>Человек, который управляет самой системой технически. Добавляет пользователей, настраивает роли, деплоит обновления, разбирает технические сбои.</p>
                    <p class="text-xs text-slate-400 border-t border-slate-100 pt-2"><strong class="text-slate-500">В системе:</strong> Панель управления, доступ ко всем модулям и документации</p>
                  </div>
                </div>

                <div class="bg-slate-50 rounded-xl border border-amber-200 overflow-hidden">
                  <div class="bg-amber-50 px-4 py-2.5 border-b border-amber-200 flex items-center gap-2.5">
                    <span class="font-mono font-black text-xs bg-amber-100 text-amber-700 px-2 py-1 rounded-lg">MGR</span>
                    <span class="font-black text-slate-800 text-sm">Менеджер по работе с клиентами</span>
                  </div>
                  <div class="px-4 py-3 space-y-2 text-sm text-slate-600">
                    <p>Напрямую взаимодействует с покупателями. Инициирует доотправку — отправляет клиенту ссылку-шаблон на форму, следит за заявкой: одобряет или отклоняет по итогу проверки. Также проверяет теги ИИ на возвратах и запускает тегирование.</p>
                    <p class="text-xs text-slate-400 border-t border-slate-100 pt-2"><strong class="text-slate-500">В системе:</strong> Доотправки, Модерация тегов, ИИ-тегирование (запуск)</p>
                  </div>
                </div>

                <div class="bg-slate-50 rounded-xl border border-blue-200 overflow-hidden col-span-2">
                  <div class="bg-blue-50 px-4 py-2.5 border-b border-blue-200 flex items-center gap-2.5">
                    <span class="font-mono font-black text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded-lg">ANA</span>
                    <span class="font-black text-slate-800 text-sm">В таблицах RACI обозначает двух разных людей — смотрите по контексту процесса</span>
                  </div>
                  <div class="grid grid-cols-2 divide-x divide-blue-100">
                    <div class="px-4 py-3 space-y-2 text-sm text-slate-600">
                      <p class="font-bold text-slate-800 text-sm flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-orange-400 inline-block flex-shrink-0"></span>
                        Менеджер по качеству
                      </p>
                      <p class="text-xs text-slate-500">Отдел производства. Формирует PPM-акты, передаёт претензии на заводы, отслеживает ответы и ведёт реестр актов. Использует карту проблем для выявления системных дефектов.</p>
                      <p class="text-xs text-slate-400 border-t border-slate-100 pt-2"><strong class="text-slate-500">В системе:</strong> PPM и Акты, Карта проблем, Реестр</p>
                    </div>
                    <div class="px-4 py-3 space-y-2 text-sm text-slate-600">
                      <p class="font-bold text-slate-800 text-sm flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-blue-400 inline-block flex-shrink-0"></span>
                        Менеджер CX
                      </p>
                      <p class="text-xs text-slate-500">Отдел клиентского опыта. Анализирует данные в разрезе клиентского восприятия: финансовые потери от возвратов, голос покупателя (отзывы, тональность, темы), рейтинги товаров. Готовит сводки для руководства.</p>
                      <p class="text-xs text-slate-400 border-t border-slate-100 pt-2"><strong class="text-slate-500">В системе:</strong> Финансовые потери, Голос покупателя, Рейтинги, Главный дашборд</p>
                    </div>
                  </div>
                </div>

                <div class="bg-slate-50 rounded-xl border border-emerald-200 overflow-hidden">
                  <div class="bg-emerald-50 px-4 py-2.5 border-b border-emerald-200 flex items-center gap-2.5">
                    <span class="font-mono font-black text-xs bg-emerald-100 text-emerald-700 px-2 py-1 rounded-lg">WHS</span>
                    <span class="font-black text-slate-800 text-sm">Склад</span>
                  </div>
                  <div class="px-4 py-3 space-y-2 text-sm text-slate-600">
                    <p>Сотрудник склада. Видит одобренные менеджером заявки на доотправку, физически комплектует посылку, подтверждает отправку или сообщает об отсутствии товара.</p>
                    <p class="text-xs text-slate-400 border-t border-slate-100 pt-2"><strong class="text-slate-500">В системе:</strong> только Доотправки — кнопки подтверждения/отклонения на своём этапе</p>
                  </div>
                </div>

                <div class="bg-slate-50 rounded-xl border border-violet-200 overflow-hidden">
                  <div class="bg-violet-50 px-4 py-2.5 border-b border-violet-200 flex items-center gap-2.5">
                    <span class="font-mono font-black text-xs bg-violet-100 text-violet-700 px-2 py-1 rounded-lg">AI</span>
                    <span class="font-black text-slate-800 text-sm">ИИ-система</span>
                  </div>
                  <div class="px-4 py-3 space-y-2 text-sm text-slate-600">
                    <p>Не человек — автоматический модуль анализа текстов. Читает комментарии покупателей к возвратам и отзывам, присваивает теги трёх уровней (cat_1 / cat_2 / cat_3), генерирует PDF-акты.</p>
                    <p class="text-xs text-slate-400 border-t border-slate-100 pt-2"><strong class="text-slate-500">В системе:</strong> запускается кнопкой или по расписанию; результат виден в модерации</p>
                  </div>
                </div>

                <div class="bg-slate-50 rounded-xl border border-slate-200 overflow-hidden col-span-2">
                  <div class="bg-slate-100 px-4 py-2.5 border-b border-slate-200 flex items-center gap-2.5">
                    <span class="font-mono font-black text-xs bg-slate-200 text-slate-700 px-2 py-1 rounded-lg">WRK</span>
                    <span class="font-black text-slate-800 text-sm">Воркеры</span>
                  </div>
                  <div class="px-4 py-3 space-y-2 text-sm text-slate-600">
                    <p>Не человек — автоматические скрипты (worker.py, ym_worker.py, ozon_worker.py, wb_chat.py). Работают по расписанию на сервере без чьего-либо участия: тянут данные из API маркетплейсов и пишут в базу данных.</p>
                    <p class="text-xs text-slate-400 border-t border-slate-100 pt-2"><strong class="text-slate-500">В системе:</strong> результат виден в /sync (Статус автоматизации) и /logs (Журнал событий)</p>
                  </div>
                </div>

              </div>
            </div>

            <!-- Легенда RACI -->
            <div class="bg-white rounded-2xl border border-slate-200 p-5 mb-8">
              <h3 class="font-black text-slate-800 mb-3 text-sm">Обозначения RACI</h3>
              <div class="grid grid-cols-2 gap-2.5">
                <div v-for="b in [
                  {l:'R',t:'Responsible — Исполнитель',d:'Тот, кто физически выполняет шаг',c:'bg-emerald-100 text-emerald-800'},
                  {l:'A',t:'Accountable — Владелец',d:'Тот, кто отвечает за итоговый результат',c:'bg-yellow-100 text-yellow-800'},
                  {l:'C',t:'Consulted — Консультируется',d:'К кому обращаются при необходимости',c:'bg-blue-100 text-blue-800'},
                  {l:'I',t:'Informed — Информируется',d:'Кого уведомляют о результате, но не вовлекают',c:'bg-slate-100 text-slate-600'},
                  {l:'R/A',t:'Исполнитель = Владелец',d:'Один человек и делает, и несёт ответственность',c:'bg-teal-100 text-teal-800'},
                ]" :key="b.l"
                   class="flex items-start gap-3 bg-slate-50 rounded-xl px-4 py-3">
                  <span :class="['font-black text-xs px-2 py-1 rounded-lg flex-shrink-0 mt-0.5', b.c]">{{ b.l }}</span>
                  <div>
                    <p class="font-bold text-xs text-slate-700">{{ b.t }}</p>
                    <p class="text-xs text-slate-500 mt-0.5">{{ b.d }}</p>
                  </div>
                </div>
              </div>
            </div>

            <!-- Таблицы RACI -->
            <div class="space-y-8">

              <div v-for="proc in [
                {
                  id: 'p1',
                  title: 'Синхронизация данных с маркетплейсами',
                  freq: 'Каждые 2 ч и 1 раз в сутки',
                  color: 'bg-amber-50 border-amber-200',
                  hcolor: 'bg-amber-50',
                  steps: [
                    {n:'Запуск воркера по cron',ADM:'I',MGR:'',ANA:'',WHS:'',AI:'',WRK:'R/A'},
                    {n:'Запрос данных из API маркетплейса',ADM:'',MGR:'',ANA:'',WHS:'',AI:'',WRK:'R/A'},
                    {n:'Запись данных в PostgreSQL',ADM:'',MGR:'',ANA:'',WHS:'',AI:'',WRK:'R/A'},
                    {n:'Логирование в system_logs',ADM:'',MGR:'',ANA:'',WHS:'',AI:'',WRK:'R/A'},
                    {n:'Просмотр статуса в /sync',ADM:'C',MGR:'I',ANA:'I',WHS:'',AI:'',WRK:''},
                    {n:'Обработка ошибки воркера',ADM:'R/A',MGR:'',ANA:'',WHS:'',AI:'',WRK:'I'},
                  ]
                },
                {
                  id: 'p2',
                  title: 'ИИ-тегирование возвратов',
                  freq: 'По расписанию или вручную',
                  color: 'bg-blue-50 border-blue-200',
                  hcolor: 'bg-blue-50',
                  steps: [
                    {n:'Запустить ИИ-тегирование (кнопка)',ADM:'',MGR:'R',ANA:'',WHS:'',AI:'',WRK:''},
                    {n:'Выборка необработанных записей',ADM:'',MGR:'',ANA:'',WHS:'',AI:'R/A',WRK:''},
                    {n:'Формирование промпта + запрос к ИИ',ADM:'',MGR:'',ANA:'',WHS:'',AI:'R/A',WRK:''},
                    {n:'Сохранение тегов (cat_1/2/3) в БД',ADM:'',MGR:'',ANA:'',WHS:'',AI:'R/A',WRK:''},
                    {n:'Просмотр результатов',ADM:'C',MGR:'I',ANA:'I',WHS:'',AI:'',WRK:''},
                  ]
                },
                {
                  id: 'p3',
                  title: 'Ручная модерация тегов',
                  freq: 'По мере поступления в очередь',
                  color: 'bg-indigo-50 border-indigo-200',
                  hcolor: 'bg-indigo-50',
                  steps: [
                    {n:'Просмотр очереди модерации',ADM:'',MGR:'R',ANA:'',WHS:'',AI:'',WRK:''},
                    {n:'Изучение комментария покупателя + фото',ADM:'',MGR:'R',ANA:'',WHS:'',AI:'',WRK:''},
                    {n:'Оценка предложенных тегов ИИ',ADM:'',MGR:'R/A',ANA:'',WHS:'',AI:'C',WRK:''},
                    {n:'Подтверждение или исправление тегов',ADM:'',MGR:'R/A',ANA:'',WHS:'',AI:'',WRK:''},
                    {n:'Сохранение результата',ADM:'',MGR:'R/A',ANA:'',WHS:'',AI:'',WRK:''},
                    {n:'Учёт исправлений для дообучения ИИ',ADM:'',MGR:'',ANA:'',WHS:'',AI:'R',WRK:''},
                  ]
                },
                {
                  id: 'p4',
                  title: 'Формирование PPM-акта',
                  freq: 'По накоплению дефектов по SKU',
                  color: 'bg-emerald-50 border-emerald-200',
                  hcolor: 'bg-emerald-50',
                  steps: [
                    {n:'Открыть /ppm, выбрать период и SKU',ADM:'',MGR:'',ANA:'R/A',WHS:'',AI:'',WRK:''},
                    {n:'Изучить список возвратов и фото',ADM:'',MGR:'C',ANA:'R',WHS:'',AI:'',WRK:''},
                    {n:'Заполнить описание дефекта (рус/кит)',ADM:'',MGR:'C',ANA:'R/A',WHS:'',AI:'',WRK:''},
                    {n:'Нажать «Сформировать акт» → PDF',ADM:'',MGR:'',ANA:'R',WHS:'',AI:'R',WRK:''},
                    {n:'Скачать PDF, акт попадает в реестр',ADM:'',MGR:'I',ANA:'R/A',WHS:'',AI:'R',WRK:''},
                    {n:'Отправить акт на завод',ADM:'A',MGR:'R',ANA:'C',WHS:'',AI:'',WRK:''},
                    {n:'Внести ответ завода в реестр',ADM:'',MGR:'R',ANA:'I',WHS:'',AI:'',WRK:''},
                  ]
                },
                {
                  id: 'p6',
                  title: 'Управление пользователями',
                  freq: 'По необходимости',
                  color: 'bg-red-50 border-red-200',
                  hcolor: 'bg-red-50',
                  steps: [
                    {n:'Admin → Панель управления → Приглашения',ADM:'R/A',MGR:'',ANA:'',WHS:'',AI:'',WRK:''},
                    {n:'Ввести email + выбрать роль + Добавить',ADM:'R/A',MGR:'',ANA:'',WHS:'',AI:'',WRK:''},
                    {n:'Сообщить сотруднику email и ссылку',ADM:'R/A',MGR:'',ANA:'',WHS:'',AI:'',WRK:''},
                    {n:'Сотрудник регистрируется на cxvo.ru',ADM:'',MGR:'C',ANA:'C',WHS:'C',AI:'',WRK:''},
                    {n:'Система проверяет белый список и создаёт аккаунт',ADM:'I',MGR:'I',ANA:'I',WHS:'I',AI:'',WRK:'R'},
                  ]
                },
              ]" :key="proc.id">
                <div :class="['bg-white rounded-2xl border overflow-hidden', proc.color]">
                  <div :class="['px-6 py-4 border-b', proc.hcolor, proc.color.replace('bg-','border-')]">
                    <h3 class="font-black text-slate-900">{{ proc.title }}</h3>
                    <p class="text-xs text-slate-500 mt-0.5">{{ proc.freq }}</p>
                  </div>
                  <div class="overflow-x-auto">
                    <table class="w-full text-xs">
                      <thead>
                        <tr class="border-b border-slate-100 bg-slate-50/50">
                          <th class="text-left px-5 py-3 font-bold text-slate-500 w-1/2">Шаг</th>
                          <th v-for="col in ['ADM','MGR','ANA','WHS','AI','WRK']" :key="col"
                              class="px-3 py-3 font-bold text-slate-500 text-center w-12">{{ col }}</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="(step, i) in proc.steps" :key="i" class="border-b border-slate-100 hover:bg-slate-50/50">
                          <td class="px-5 py-3 text-slate-700 font-medium">
                            <span class="inline-flex items-center justify-center w-5 h-5 rounded-full bg-slate-100 text-slate-500 font-bold mr-2 text-[10px]">{{ i+1 }}</span>
                            {{ step.n }}
                          </td>
                          <td v-for="col in ['ADM','MGR','ANA','WHS','AI','WRK']" :key="col" class="px-3 py-3 text-center">
                            <span v-if="step[col]"
                              :class="[
                                'inline-block px-2 py-0.5 rounded-lg font-bold text-[11px]',
                                step[col]==='R/A' ? 'bg-teal-100 text-teal-800' :
                                step[col]==='R'   ? 'bg-emerald-100 text-emerald-800' :
                                step[col]==='A'   ? 'bg-yellow-100 text-yellow-800' :
                                step[col]==='C'   ? 'bg-blue-100 text-blue-800' :
                                                    'bg-slate-100 text-slate-500'
                              ]">{{ step[col] }}</span>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>

              <!-- Процесс доотправки — отдельная визуальная схема -->
              <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                <div class="px-6 py-4 bg-purple-50 border-b border-purple-200">
                  <h3 class="font-black text-slate-900">Процесс доотправки — полный цикл</h3>
                  <p class="text-xs text-slate-500 mt-0.5">5 этапов: от триггера в чате до получения клиентом</p>
                </div>
                <div class="p-6">
                  <div class="space-y-4">
                    <div v-for="stage in [
                      {
                        n:'Этап 1',label:'Инициация',color:'bg-blue-50 border-blue-200',tc:'text-blue-800',
                        steps:['Менеджер пишет «#от» в WB-чате','wb_chat.py обнаруживает триггер, создаёт токен','Ссылка на форму отправляется клиенту автоматически (WB_CHAT_ENABLED=true) или менеджер копирует вручную'],
                        who: {MGR:'R/A', WRK:'R'}
                      },
                      {
                        n:'Этап 2',label:'Клиент заполняет форму',color:'bg-slate-50 border-slate-200',tc:'text-slate-700',
                        steps:['Клиент открывает ссылку (box.vidovito.com?t=TOKEN)','Заполняет: ФИО, телефон, адрес / ПВЗ СДЭК, категорию товара, описание проблемы, фото брака (до 5 шт.)','Нажимает «Отправить» → статус: new'],
                        who: {WRK:'R'}
                      },
                      {
                        n:'Этап 3',label:'Проверка и одобрение (КС)',color:'bg-amber-50 border-amber-200',tc:'text-amber-800',
                        steps:['КС открывает /reshipment, видит карточку заявки','Блок «Экономика»: система показывает 3 варианта (вернуть деньги / отправить деталь) с расчётом прибыли/потерь','КС привязывает заказ WB (matched_srid) → статус: matched','✅ Одобрить (если доставка убыточна — красная предупреждалка) → статус: approved','❌ Отклонить → ввести причину → статус: rejected'],
                        who: {MGR:'R/A'}
                      },
                      {
                        n:'Этап 4',label:'Упаковка и отправка (Склад)',color:'bg-emerald-50 border-emerald-200',tc:'text-emerald-800',
                        steps:['Склад видит одобренные заявки в своей вкладке','Упаковывает → кнопка «Упаковано» → статус: packed','Создаёт отправку через СДЭК API (одиночно или bulk) → статус: shipped','СДЭК-вебхуки автоматически двигают статус: shipped → in_transit → ready_for_pickup → delivered'],
                        who: {MGR:'A', WHS:'R/A'}
                      },
                      {
                        n:'Этап 5',label:'Доставка и уведомления (авто)',color:'bg-indigo-50 border-indigo-200',tc:'text-indigo-800',
                        steps:['На каждое изменение статуса СДЭК → Salebot (TG/MAX) + WB-чат клиенту','ready_for_pickup 48ч → автоматическое напоминание с датой хранения (keep_free_until из СДЭК)','Клиент получает ссылку-подтверждение → нажимает → статус: delivered','Снимки отзывов ДО и ПОСЛЕ отправки фиксируются в wb_feedback_snapshots'],
                        who: {WRK:'R/A'}
                      },
                    ]" :key="stage.n">
                      <div :class="['border rounded-xl overflow-hidden', stage.color]">
                        <div class="flex items-center gap-3 px-4 py-3 border-b" :class="stage.color">
                          <span :class="['font-black text-xs px-2 py-1 rounded-lg bg-white', stage.tc]">{{ stage.n }}</span>
                          <span class="font-bold text-sm text-slate-800">{{ stage.label }}</span>
                          <div class="ml-auto flex gap-2">
                            <span v-for="(v,k) in stage.who" :key="k"
                              :class="['font-mono text-[10px] font-black px-2 py-0.5 rounded',
                                v==='R/A' ? 'bg-teal-100 text-teal-800' :
                                v==='R'   ? 'bg-emerald-100 text-emerald-800' : 'bg-yellow-100 text-yellow-800']">
                              {{ k }}: {{ v }}
                            </span>
                          </div>
                        </div>
                        <div class="px-4 py-3">
                          <div class="flex flex-wrap gap-x-6 gap-y-1">
                            <div v-for="(step, i) in stage.steps" :key="i" class="flex items-start gap-1.5 text-xs text-slate-600">
                              <span class="text-slate-300 mt-0.5 flex-shrink-0">{{ i+1 }}.</span>
                              <span>{{ step }}</span>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>

                  <!-- Сводная RACI -->
                  <h3 class="font-black text-slate-800 mt-6 mb-3 text-sm">Сводная RACI-матрица</h3>
                  <div class="overflow-x-auto">
                    <table class="w-full text-xs">
                      <thead>
                        <tr class="bg-slate-50 border-b border-slate-200">
                          <th class="text-left px-4 py-2.5 font-bold text-slate-500">Процесс</th>
                          <th v-for="col in ['ADM','MGR','ANA','WHS','AI','WRK']" :key="col" class="px-3 py-2.5 font-bold text-slate-500 text-center">{{ col }}</th>
                        </tr>
                      </thead>
                      <tbody class="divide-y divide-slate-100">
                        <tr v-for="r in [
                          {p:'Синхронизация данных',ADM:'I',MGR:'I',ANA:'I',WHS:'',AI:'',WRK:'R/A'},
                          {p:'ИИ-тегирование',ADM:'C',MGR:'R',ANA:'',WHS:'',AI:'R/A',WRK:''},
                          {p:'Модерация тегов',ADM:'',MGR:'R/A',ANA:'',WHS:'',AI:'C',WRK:''},
                          {p:'Формирование PPM-акта',ADM:'A',MGR:'C',ANA:'R',WHS:'',AI:'C',WRK:''},
                          {p:'Доотправка: инициация',ADM:'',MGR:'R/A',ANA:'',WHS:'',AI:'',WRK:'R'},
                          {p:'Доотправка: одобрение (КС)',ADM:'',MGR:'R/A',ANA:'',WHS:'',AI:'',WRK:''},
                          {p:'Доотправка: упаковка и отправка',ADM:'A',MGR:'',ANA:'',WHS:'R/A',AI:'',WRK:''},
                          {p:'Доотправка: доставка и уведомления',ADM:'',MGR:'I',ANA:'',WHS:'I',AI:'',WRK:'R/A'},
                          {p:'Управление пользователями',ADM:'R/A',MGR:'',ANA:'',WHS:'',AI:'',WRK:'C'},
                          {p:'Деплой обновлений',ADM:'R/A',MGR:'',ANA:'',WHS:'',AI:'',WRK:''},
                          {p:'Аналитика и отчёты',ADM:'C',MGR:'I',ANA:'R/A',WHS:'',AI:'',WRK:''},
                        ]" :key="r.p" class="hover:bg-slate-50">
                          <td class="px-4 py-2.5 text-slate-700 font-medium">{{ r.p }}</td>
                          <td v-for="col in ['ADM','MGR','ANA','WHS','AI','WRK']" :key="col" class="px-3 py-2.5 text-center">
                            <span v-if="r[col]"
                              :class="['inline-block px-2 py-0.5 rounded-lg font-bold text-[10px]',
                                r[col]==='R/A' ? 'bg-teal-100 text-teal-800' :
                                r[col]==='R'   ? 'bg-emerald-100 text-emerald-800' :
                                r[col]==='A'   ? 'bg-yellow-100 text-yellow-800' :
                                r[col]==='C'   ? 'bg-blue-100 text-blue-800' : 'bg-slate-100 text-slate-500']">{{ r[col] }}</span>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>

            </div>
          </template>

          <!-- ══════════════════════════════════════════════════════════════ -->
          <!-- РАЗДЕЛ 4: ДЕПЛОЙ -->
          <!-- ══════════════════════════════════════════════════════════════ -->
          <template v-else-if="activeSection==='deploy'">

            <div class="bg-red-50 border border-red-200 rounded-2xl p-5 mb-8">
              <div class="flex items-start gap-3">
                <AlertTriangle class="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
                <div>
                  <p class="font-black text-red-800 text-sm">Критически важные правила</p>
                  <ul class="mt-2 space-y-1 text-sm text-red-700">
                    <li>✅ <code class="bg-red-100 px-1.5 py-0.5 rounded font-mono text-xs">npm run build</code> — только <strong>локально на Mac</strong>, никогда на сервере</li>
                    <li>✅ Фронт деплоится в <code class="bg-red-100 px-1.5 py-0.5 rounded font-mono text-xs">/var/www/cx-dashboard/</code></li>
                    <li>❌ Не восстанавливать <code class="bg-red-100 px-1.5 py-0.5 rounded font-mono text-xs">src/</code> из серверного <code class="bg-red-100 px-1.5 py-0.5 rounded font-mono text-xs">/root/cx-dashboard/</code> — это старая копия</li>
                  </ul>
                </div>
              </div>
            </div>

            <div class="space-y-6">

              <!-- Деплой фронта -->
              <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                <div class="px-6 py-4 bg-indigo-50 border-b border-indigo-200">
                  <h3 class="font-black text-indigo-900">Деплой фронтенда (Vue.js)</h3>
                </div>
                <div class="p-6 space-y-4">
                  <div v-for="(step, i) in [
                    {t:'Собери фронт локально (на Mac)',code:'cd /Users/user/Documents/ПРОЕКТЫ/local/cx-dashboard/frontend\nnpm run build'},
                    {t:'Загрузи на сервер через rsync',code:'rsync -avz --delete \\\n  frontend/dist/ \\\n  root@72.56.240.22:/var/www/cx-dashboard/'},
                    {t:'Проверь в браузере',note:'Открой https://cxvo.ru · Если старая версия — Ctrl+Shift+R'},
                  ]" :key="i" class="flex gap-4">
                    <div class="flex-shrink-0 w-7 h-7 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-700 font-black text-sm">{{ i+1 }}</div>
                    <div class="flex-1">
                      <p class="font-bold text-slate-800 text-sm mb-1.5">{{ step.t }}</p>
                      <pre v-if="step.code" class="bg-slate-900 text-emerald-300 rounded-xl p-4 text-xs font-mono overflow-x-auto whitespace-pre">{{ step.code }}</pre>
                      <p v-if="step.note" class="text-sm text-slate-600 bg-slate-50 rounded-xl px-4 py-3 border border-slate-200">{{ step.note }}</p>
                    </div>
                  </div>
                </div>
              </div>

              <!-- Деплой бэкенда -->
              <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                <div class="px-6 py-4 bg-emerald-50 border-b border-emerald-200">
                  <h3 class="font-black text-emerald-900">Деплой бэкенда (FastAPI)</h3>
                </div>
                <div class="p-6 space-y-4">
                  <div v-for="(step, i) in deployBackendSteps" :key="i" class="flex gap-4">
                    <div class="flex-shrink-0 w-7 h-7 rounded-full bg-emerald-100 flex items-center justify-center text-emerald-700 font-black text-sm">{{ i+1 }}</div>
                    <div class="flex-1">
                      <p class="font-bold text-slate-800 text-sm mb-1.5">{{ step.t }}</p>
                      <pre class="bg-slate-900 text-emerald-300 rounded-xl p-4 text-xs font-mono overflow-x-auto whitespace-pre">{{ step.code }}</pre>
                    </div>
                  </div>
                </div>
              </div>

              <!-- Структура папок -->
              <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                <div class="px-6 py-4 bg-slate-50 border-b border-slate-200">
                  <h3 class="font-black text-slate-900">Структура папок на сервере</h3>
                </div>
                <div class="p-6">
                  <pre class="bg-slate-900 text-slate-300 rounded-xl p-5 text-xs font-mono leading-relaxed whitespace-pre">/root/cx-dashboard/          ← исходники бэкенда + воркеры
├── .env                     ← секреты и ключи API
├── backend/
│   ├── main.py              ← FastAPI приложение
│   ├── database.py          ← подключение к PostgreSQL
│   ├── routers/             ← API маршруты (/api/v1/...)
│   ├── cdek.py              ← интеграция СДЭК
│   └── wb_chat.py           ← интеграция WB-чата
├── worker.py                ← воркер WB
├── ym_worker.py             ← воркер ЯМ
├── ozon_worker.py           ← воркер OZON
├── uploads/reshipment/      ← фото из форм доотправки
└── static/ym_returns/       ← фото возвратов ЯМ

/var/www/cx-dashboard/       ← собранный фронтенд
├── index.html
└── assets/</pre>
                </div>
              </div>

              <!-- Диагностика -->
              <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                <div class="px-6 py-4 bg-amber-50 border-b border-amber-200">
                  <h3 class="font-black text-amber-900">Быстрая диагностика</h3>
                </div>
                <div class="p-6 space-y-3">
                  <div v-for="cmd in diagCmds" :key="cmd.t">
                    <p class="text-xs font-bold text-slate-600 mb-1">{{ cmd.t }}</p>
                    <pre class="bg-slate-900 text-emerald-300 rounded-xl p-3 text-xs font-mono whitespace-pre">{{ cmd.c }}</pre>
                  </div>
                </div>
              </div>

              <!-- Переменные окружения -->
              <div class="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                <div class="px-6 py-4 bg-slate-50 border-b border-slate-200">
                  <h3 class="font-black text-slate-900">Ключевые переменные .env</h3>
                  <p class="text-xs text-slate-400 mt-0.5">Файл: /root/cx-dashboard/.env</p>
                </div>
                <div class="overflow-x-auto">
                  <table class="w-full text-sm">
                    <thead><tr class="bg-slate-50 border-b border-slate-100"><th class="text-left px-5 py-3 text-xs font-bold text-slate-400 uppercase tracking-wider">Переменная</th><th class="text-left px-5 py-3 text-xs font-bold text-slate-400 uppercase tracking-wider">Описание</th></tr></thead>
                    <tbody class="divide-y divide-slate-100">
                      <tr v-for="v in [
                        {k:'WB_API_KEY',d:'API ключ Wildberries (нужны права: Статистика, Возвраты, Чат)'},
                        {k:'WB_CHAT_ENABLED',d:'true = бот сам пишет в чат, false = только мониторинг'},
                        {k:'WB_TRIGGER_KEYWORD',d:'Слово-триггер в чате (по умолч. #от)'},
                        {k:'YM_API_KEY / YM_CAMPAIGN_ID / YM_BUSINESS_ID',d:'Доступ к API Яндекс Маркет'},
                        {k:'OZON_CLIENT_ID / OZON_API_KEY',d:'Доступ к API OZON'},
                        {k:'CDEK_CLIENT_ID / CDEK_CLIENT_SECRET',d:'Доступ к API СДЭК'},
                        {k:'CDEK_FROM_PVZ_CODE',d:'⚠️ Код ПВЗ в Казани (обязательно заполнить!)'},
                        {k:'S3_ACCESS_KEY / S3_SECRET_KEY',d:'Яндекс Object Storage (хранение фото)'},
                        {k:'SPREADSHEET_ID_INVOICES',d:'ID Google Sheet с инвойсами'},
                        {k:'JWT_SECRET',d:'Секрет для подписи JWT токенов'},
                      ]" :key="v.k" class="hover:bg-slate-50">
                        <td class="px-5 py-3 font-mono text-xs text-slate-700 font-semibold">{{ v.k }}</td>
                        <td class="px-5 py-3 text-slate-600 text-xs">{{ v.d }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>

            </div>
          </template>

          <!-- ══════════════════════════════════════════════════════════════ -->
          <!-- РАЗДЕЛ 5: ИНТЕГРАЦИИ -->
          <!-- ══════════════════════════════════════════════════════════════ -->
          <template v-else-if="activeSection==='integrations'">

            <!-- Схема потоков данных -->
            <h2 class="text-lg font-black text-slate-900 mb-4 flex items-center gap-2">
              <GitFork class="w-5 h-5 text-indigo-500" /> Схема потоков данных
            </h2>

            <div class="bg-white rounded-2xl border border-slate-200 p-6 mb-8">
              <div class="space-y-4">
                <!-- Источники -->
                <div class="grid grid-cols-5 gap-2">
                  <div v-for="src in [
                    {n:'WB API',c:'bg-purple-50 border-purple-200 text-purple-800',d:'возвраты, отзывы, заказы, ассортимент'},
                    {n:'ЯМ API',c:'bg-orange-50 border-orange-200 text-orange-800',d:'возвраты, отзывы, заказы, цены'},
                    {n:'OZON API',c:'bg-blue-50 border-blue-200 text-blue-800',d:'возвраты FBO, заказы'},
                    {n:'Google Sheets',c:'bg-green-50 border-green-200 text-green-800',d:'инвойсы, себестоимость'},
                    {n:'WB Чат API',c:'bg-purple-50 border-purple-200 text-purple-700',d:'события чата, триггер #от'},
                  ]" :key="src.n" :class="['border rounded-xl p-3 text-center', src.c]">
                    <p class="font-bold text-xs">{{ src.n }}</p>
                    <p class="text-[10px] opacity-70 mt-1">{{ src.d }}</p>
                  </div>
                </div>

                <div class="flex justify-center items-center gap-2 text-slate-300 text-xs">
                  <div class="flex-1 h-px bg-slate-200"></div>
                  <span>воркеры (cron) и asyncio</span>
                  <div class="flex-1 h-px bg-slate-200"></div>
                </div>
                <div class="flex justify-center text-slate-300 text-sm">▼</div>

                <!-- БД -->
                <div class="bg-blue-50 border-2 border-blue-300 rounded-xl px-6 py-4 text-center">
                  <div class="flex items-center justify-center gap-3">
                    <Database class="w-6 h-6 text-blue-600" />
                    <div>
                      <p class="font-black text-blue-900 text-base">PostgreSQL · cx_dashboard</p>
                      <p class="text-xs text-blue-500 mt-0.5">wb_claims, wb_feedbacks, wb_orders, wb_logistics, wb_invoices, wb_cogs, ym_*, ozon_*, users, reshipment_requests, ...</p>
                    </div>
                  </div>
                </div>

                <div class="flex justify-center text-slate-300 text-sm">▼</div>

                <!-- Бэкенд -->
                <div class="bg-emerald-50 border-2 border-emerald-300 rounded-xl px-6 py-3 text-center">
                  <p class="font-black text-emerald-900">FastAPI Backend · /api/v1/...</p>
                </div>

                <div class="flex justify-center text-slate-300 text-sm">▼</div>

                <!-- Выходные сервисы -->
                <div class="grid grid-cols-4 gap-2">
                  <div v-for="out in [
                    {n:'Vue.js Frontend',c:'bg-indigo-50 border-indigo-200 text-indigo-800',d:'cxvo.ru'},
                    {n:'СДЭК API',c:'bg-slate-50 border-slate-200 text-slate-700',d:'создание отправлений'},
                    {n:'Яндекс S3',c:'bg-slate-50 border-slate-200 text-slate-700',d:'хранение фото'},
                    {n:'SaleBot',c:'bg-slate-50 border-slate-200 text-slate-700',d:'уведомления команде'},
                  ]" :key="out.n" :class="['border rounded-xl p-3 text-center', out.c]">
                    <p class="font-bold text-xs">{{ out.n }}</p>
                    <p class="text-[10px] opacity-70 mt-1">{{ out.d }}</p>
                  </div>
                </div>
              </div>
            </div>

            <!-- Карточки интеграций -->
            <div class="space-y-4">
              <div v-for="int in [
                {
                  title:'Wildberries API',
                  color:'bg-purple-50 border-purple-200',hc:'bg-purple-50',
                  key:'WB_API_KEY',
                  rows:[
                    {d:'Возвраты (рекламации)',t:'wb_claims',f:'каждые 2 ч'},
                    {d:'Отзывы покупателей',t:'wb_feedbacks',f:'каждые 2 ч'},
                    {d:'Заказы',t:'wb_orders',f:'ежедневно'},
                    {d:'Ассортимент (матрица)',t:'wb_assortment',f:'ежедневно'},
                    {d:'Финансовая логистика',t:'wb_logistics',f:'ежедневно'},
                    {d:'Инвойсы (через Google Sheets)',t:'wb_invoices',f:'ежедневно'},
                  ],
                  notes:['API ключ нужен с правами: Аналитика, Статистика, Возвраты, Отзывы', 'Для WB-чата нужен отдельный ключ с правом «Чат» (см. WB Чат API ниже)']
                },
                {
                  title:'WB Чат API',
                  color:'bg-violet-50 border-violet-200',hc:'bg-violet-50',
                  key:'WB_API_KEY (scope: Чат) + WB_CHAT_ENABLED + WB_TRIGGER_KEYWORD',
                  rows:[
                    {d:'Мониторинг входящих сообщений',t:'—',f:'постоянно (asyncio)'},
                    {d:'Создание заявки на доотправку по триггеру',t:'reshipment_requests',f:'при #от'},
                  ],
                  notes:['wb_chat.py работает постоянно как отдельный asyncio-процесс (не cron)', 'WB_CHAT_ENABLED=true → бот сам отвечает в чат и отправляет ссылку; false → только мониторинг', 'WB_TRIGGER_KEYWORD — слово-триггер (по умолч. #от)']
                },
                {
                  title:'Яндекс Маркет API',
                  color:'bg-orange-50 border-orange-200',hc:'bg-orange-50',
                  key:'YM_API_KEY + YM_CAMPAIGN_ID + YM_BUSINESS_ID',
                  rows:[
                    {d:'Заказы',t:'ym_orders',f:'каждые 2 ч'},
                    {d:'Возвраты',t:'ym_returns',f:'каждые 2 ч'},
                    {d:'Отзывы',t:'ym_feedbacks',f:'каждые 2 ч'},
                    {d:'Ассортимент + цены',t:'ym_assortment / ym_prices',f:'ежедневно'},
                    {d:'Финансовые отчёты',t:'—',f:'3 раза в день'},
                  ],
                  notes:['Пагинация через paging (не pager!)', 'Лимит 420 → воркер пропускает задачу и повторяет на следующем запуске', 'Фото возвратов хранятся в /root/cx-dashboard/static/ym_returns/']
                },
                {
                  title:'OZON API',
                  color:'bg-blue-50 border-blue-200',hc:'bg-blue-50',
                  key:'OZON_CLIENT_ID + OZON_API_KEY',
                  rows:[
                    {d:'Возвраты FBO',t:'ozon_returns',f:'каждые 2 ч'},
                    {d:'Заказы FBS (30-дн. окно)',t:'ozon_orders',f:'ежедневно'},
                  ],
                  notes:['Только FBO (склад OZON), FBS не поддерживается', 'Нет фото и комментариев к возвратам — API не отдаёт', 'Пагинация через last_id (cursor-based)', 'Ручной backfill: python3 ozon_worker.py backfill']
                },
                {
                  title:'СДЭК API v2',
                  color:'bg-emerald-50 border-emerald-200',hc:'bg-emerald-50',
                  key:'CDEK_CLIENT_ID + CDEK_CLIENT_SECRET',
                  rows:[
                    {d:'Создание отправлений',t:'reshipment_requests',f:'по запросу'},
                    {d:'Тариф',t:'483 (ПВЗ→ПВЗ)',f:'Экспресс'},
                  ],
                  notes:['⚠️ CDEK_FROM_PVZ_CODE обязательно заполнить — без него создание отправлений не работает', 'Город отправки: Казань (код 424)', 'OAuth2 токен обновляется автоматически каждый час']
                },
                {
                  title:'Яндекс Object Storage (S3)',
                  color:'bg-slate-50 border-slate-200',hc:'bg-slate-50',
                  key:'S3_ACCESS_KEY + S3_SECRET_KEY',
                  rows:[
                    {d:'Миниатюры фото возвратов WB',t:'бакет cx-dashboard-media',f:'при синхронизации'},
                  ],
                  notes:['Фото уменьшаются до 1000×1000 пикселей', 'Если S3 не настроен — хранятся оригинальные URL WB (могут протухать)']
                },
                {
                  title:'Google Sheets',
                  color:'bg-green-50 border-green-200',hc:'bg-green-50',
                  key:'bot_api_key.json (сервисный аккаунт Google)',
                  rows:[
                    {d:'Инвойсы (поставка → артикул → номер)',t:'wb_invoices',f:'ежедневно'},
                    {d:'Себестоимость товаров (COGs)',t:'wb_cogs',f:'ежедневно'},
                  ],
                  notes:['Инвойсы вносятся вручную в Google Sheets, откуда воркер забирает в БД', 'SPREADSHEET_ID_INVOICES и SPREADSHEET_ID_FINANCES в .env']
                },
              ]" :key="int.title">
                <div :class="['bg-white rounded-2xl border overflow-hidden', int.color.split(' ')[1]]">
                  <div :class="['px-6 py-4 border-b flex items-center justify-between', int.color]">
                    <div>
                      <h3 class="font-black text-slate-900">{{ int.title }}</h3>
                      <p class="font-mono text-xs text-slate-500 mt-0.5">{{ int.key }}</p>
                    </div>
                  </div>
                  <div class="p-6">
                    <table class="w-full text-xs mb-4">
                      <thead><tr class="border-b border-slate-100"><th class="text-left pb-2 text-slate-400 font-bold uppercase tracking-wider">Данные</th><th class="text-left pb-2 text-slate-400 font-bold uppercase tracking-wider">Таблица БД</th><th class="text-left pb-2 text-slate-400 font-bold uppercase tracking-wider">Частота</th></tr></thead>
                      <tbody class="divide-y divide-slate-100">
                        <tr v-for="r in int.rows" :key="r.d" class="hover:bg-slate-50">
                          <td class="py-2 pr-4 text-slate-700">{{ r.d }}</td>
                          <td class="py-2 pr-4 font-mono text-blue-600">{{ r.t }}</td>
                          <td class="py-2 text-slate-500">{{ r.f }}</td>
                        </tr>
                      </tbody>
                    </table>
                    <div v-if="int.notes.length" class="space-y-1.5">
                      <div v-for="note in int.notes" :key="note"
                           class="flex items-start gap-2 text-xs text-slate-600 bg-slate-50 rounded-lg px-3 py-2">
                        <CheckCircle2 class="w-3.5 h-3.5 text-slate-400 flex-shrink-0 mt-0.5" />
                        {{ note }}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

          </template>

        </div><!-- /doc-content -->
      </div>
    </main>
  </div>
</template>
