<template>
  <div class="min-h-screen bg-gray-50">

    <!-- ══ ШАПКА РАЗДЕЛА ══════════════════════════════════════════════ -->
    <div class="bg-white border-b border-gray-200 sticky top-0 z-20">
      <div class="max-w-7xl mx-auto px-5">

        <!-- Строка 1: заголовок + счётчики-пилюли -->
        <div class="flex items-center justify-between gap-4 py-3">
          <div class="flex items-center gap-3">
            <div class="w-9 h-9 rounded-xl bg-blue-50 flex items-center justify-center text-blue-600 shrink-0">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
              </svg>
            </div>
            <div>
              <h1 class="text-sm font-medium text-gray-900 leading-tight">Обращения клиентов</h1>
              <p class="text-xs text-gray-400 leading-tight mt-0.5">Wildberries · ЯМаркет · Ozon</p>
            </div>
          </div>

          <!-- Счётчики только в очереди -->
          <div v-if="activeSection === 'queue' && counts.total != null" class="flex items-center gap-2 flex-wrap justify-end">
            <span v-if="(counts.total || 0) > 0"
              class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-red-50 text-red-700 border border-red-200">
              <span class="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse"></span>
              {{ counts.total }} обращений
            </span>
            <span v-if="(counts.wb || 0) > 0"
              class="px-2.5 py-1 rounded-full text-xs font-medium bg-purple-50 text-purple-700 border border-purple-200">
              WB {{ counts.wb }}
            </span>
            <span v-if="(counts.ym || 0) > 0"
              class="px-2.5 py-1 rounded-full text-xs font-medium bg-yellow-50 text-yellow-700 border border-yellow-200">
              ЯМ {{ counts.ym }}
            </span>
            <span v-if="(counts.ozon || 0) > 0"
              class="px-2.5 py-1 rounded-full text-xs font-medium bg-blue-50 text-blue-700 border border-blue-200">
              Ozon {{ counts.ozon }}
            </span>
          </div>
        </div>

        <!-- Строка 2: вкладки секций -->
        <div class="flex -mb-px">
          <button v-for="s in sections" :key="s.key"
            @click="switchSection(s.key)"
            :class="['px-4 py-2.5 text-sm font-medium border-b-2 transition-all',
              activeSection === s.key
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700']">
            {{ s.label }}
          </button>
        </div>
      </div>

      <!-- Строка 3: фильтры типов (только очередь) -->
      <div v-if="activeSection === 'queue'" class="border-t border-gray-100 bg-gray-50/60">
        <div class="max-w-7xl mx-auto px-5 py-2 flex items-center gap-2 flex-wrap">
          <button v-for="t in qTabs" :key="t.key"
            @click="activeQTab = t.key; currentPage = 1"
            :class="['inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition-all',
              activeQTab === t.key
                ? 'bg-blue-600 border-blue-600 text-white'
                : 'bg-white border-gray-200 text-gray-600 hover:border-gray-300 hover:text-gray-800']">
            {{ t.label }}
            <span :class="['px-1.5 py-0.5 rounded-full text-xs font-semibold leading-none',
              activeQTab === t.key ? 'bg-white/25 text-white' : 'bg-gray-100 text-gray-500']">
              {{ getCnt(t.key) }}
            </span>
          </button>
          <div class="ml-auto flex items-center gap-3">
            <button @click="markAllRead"
              class="text-xs text-blue-500 hover:text-blue-700 flex items-center gap-1 transition-colors">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg>
              Все прочитано
            </button>
            <button @click="loadQueue()" class="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1 transition-colors">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
              Обновить
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- ══ ОЧЕРЕДЬ: отзывы / вопросы / все ════════════════════════════ -->
    <div v-if="activeSection === 'queue' && activeQTab !== 'chat'" class="max-w-7xl mx-auto px-4 py-4">

      <!-- Загрузка -->
      <div v-if="queueLoading" class="flex items-center justify-center py-20">
        <div class="text-center">
          <div class="w-10 h-10 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin mx-auto mb-3"></div>
          <p class="text-gray-400 text-sm">Загружаем очередь…</p>
        </div>
      </div>

      <!-- Пусто -->
      <div v-else-if="filteredItems.length === 0" class="flex flex-col items-center justify-center py-20">
        <div class="text-5xl mb-4">🎉</div>
        <p class="text-gray-500 font-medium">Очередь пуста</p>
        <p class="text-gray-400 text-sm mt-1">Все обращения обработаны</p>
      </div>

      <!-- Контролы: сортировка + пагинация -->
      <div v-else class="flex flex-wrap items-center justify-between gap-3 mb-4">
        <p class="text-sm text-gray-500">
          Показано {{ pagedItems.length }} из {{ filteredItems.length }}
        </p>
        <div class="flex items-center gap-2">
          <!-- Сортировка по давности с переключением направления -->
          <button @click="toggleSort"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-sm text-gray-600 hover:border-gray-300 hover:text-gray-800 transition-all">
            <span>{{ activeQTab === 'chat'
              ? (sortDesc ? 'Последнее сообщение ↑' : 'Последнее сообщение ↓')
              : (sortDesc ? 'Сначала старые' : 'Сначала новые') }}</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path v-if="sortDesc" d="M12 5v14M19 12l-7 7-7-7"/>
              <path v-else d="M12 19V5M5 12l7-7 7 7"/>
            </svg>
          </button>
          <select v-model.number="perPage" @change="currentPage = 1"
            class="border border-gray-200 rounded-lg px-2 py-1.5 text-sm text-gray-600 focus:outline-none focus:ring-1 focus:ring-blue-400">
            <option v-for="n in [10,25,50,100]" :key="n" :value="n">{{ n }} на стр.</option>
          </select>
        </div>
      </div>

      <!-- Карточки -->
      <div v-if="pagedItems.length > 0" class="space-y-2.5">
        <div v-for="item in pagedItems" :key="item.mp + item.item_type + item.id"
          :class="['bg-white rounded-xl border transition-colors overflow-hidden',
            jumpToItemId && String(item.id) === String(jumpToItemId)
              ? 'border-indigo-400 ring-2 ring-indigo-200'
              : 'border-gray-200 hover:border-gray-300']">

          <div class="flex gap-3 p-4">
            <!-- Превью товара -->
            <div class="shrink-0">
              <a v-if="item.nm_id && item.mp === 'wb'"
                :href="`https://www.wildberries.ru/catalog/${item.nm_id}/detail.aspx`" target="_blank"
                class="block w-12 h-12 rounded-lg overflow-hidden bg-gray-100 border border-gray-200">
                <img v-if="item.photos && item.photos.length" :src="item.photos[0]" class="w-full h-full object-cover" />
                <span v-else class="w-full h-full flex items-center justify-center text-gray-300">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/></svg>
                </span>
              </a>
              <div v-else class="w-12 h-12 rounded-lg bg-gray-100 border border-gray-200 flex items-center justify-center text-gray-300">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/></svg>
              </div>
            </div>

            <!-- Основной блок -->
            <div class="flex-1 min-w-0">
              <!-- Метаданные -->
              <div class="flex items-center justify-between gap-2 mb-1.5">
                <div class="flex items-center gap-2 min-w-0">
                  <span :class="['px-1.5 py-0.5 rounded text-xs font-medium', mpBadgeCls[item.mp]]">{{ item.mp.toUpperCase() }}</span>
                  <span :class="['px-2 py-0.5 rounded-full text-xs font-medium', typeChipCls[item.item_type]]">{{ typeLabel[item.item_type] }}</span>
                  <span v-if="hasNewMsg(item)"
                    class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold bg-blue-600 text-white leading-none">
                    <span class="w-1.5 h-1.5 rounded-full bg-white opacity-80 animate-pulse"></span>
                    Новое
                  </span>
                  <!-- Рейтинг звёздами SVG -->
                  <span v-if="item.rating != null" class="flex items-center gap-px shrink-0">
                    <svg v-for="i in 5" :key="i" width="13" height="13" viewBox="0 0 24 24"
                      :fill="i <= item.rating ? '#f59e0b' : '#e5e7eb'" :stroke="i <= item.rating ? '#f59e0b' : '#e5e7eb'">
                      <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
                    </svg>
                  </span>
                  <span v-if="item.contact" class="text-xs text-gray-400 truncate">· {{ item.contact }}</span>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                  <span :class="['text-xs px-2 py-0.5 rounded-full font-medium', ageCls(item.age_sec)]">{{ fmtAge(item.age_sec) }}</span>
                  <button @click="dismissItem(item)" title="В архив без ответа"
                    class="text-gray-300 hover:text-red-400 transition-colors">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
                  </button>
                </div>
              </div>

              <!-- Артикул -->
              <p v-if="item.sku" class="text-xs text-gray-400 mb-1">Арт. {{ item.sku }}</p>

              <!-- Текст -->
              <p class="text-sm text-gray-700 leading-relaxed">{{ item.body }}</p>

              <!-- Достоинства / недостатки -->
              <div v-if="item.pros || item.cons" class="mt-2 flex flex-col gap-1">
                <p v-if="item.pros" class="text-xs text-gray-600"><span class="text-green-600 font-medium">Плюсы:</span> {{ item.pros }}</p>
                <p v-if="item.cons" class="text-xs text-gray-600"><span class="text-red-500 font-medium">Минусы:</span> {{ item.cons }}</p>
              </div>

              <!-- Фото-миниатюры (для отзывов) -->
              <div v-if="item.photos && item.photos.length && item.item_type !== 'chat'" class="mt-2 flex flex-wrap gap-1.5">
                <a v-for="(ph, i) in item.photos.slice(0, 6)" :key="i" :href="ph" target="_blank"
                  class="block w-14 h-14 rounded-lg overflow-hidden border border-gray-200 hover:opacity-80 transition-opacity">
                  <img :src="ph" class="w-full h-full object-cover" />
                </a>
              </div>

              <!-- Кнопка открыть чат -->
              <button v-if="item.item_type === 'chat' && item.mp === 'wb'"
                @click="openChat(item)"
                class="mt-2 inline-flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-700 font-medium">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                Открыть переписку
              </button>
            </div>
          </div>

          <!-- Модерация черновика -->
          <div v-if="item.pending_mod_id" class="border-t border-amber-200 bg-amber-50/70 px-4 py-3 space-y-2">
            <p class="text-xs font-medium text-amber-700">Черновик автоответа ожидает подтверждения</p>
            <p class="text-sm text-gray-700 whitespace-pre-wrap bg-white border border-amber-200 rounded-lg px-3 py-2">{{ item.pending_draft }}</p>
            <div class="flex gap-2">
              <button @click="confirmMod(item)"
                class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg transition-colors font-medium">
                Подтвердить и отправить
              </button>
              <button @click="rejectMod(item)"
                class="px-3 py-1.5 bg-white hover:bg-gray-50 text-gray-600 border border-gray-200 text-sm rounded-lg transition-colors font-medium">
                Отклонить
              </button>
            </div>
          </div>

          <!-- Форма ответа (не для чатов — чаты отвечаются в окне переписки) -->
          <div v-else-if="item.item_type !== 'chat'" class="border-t border-gray-100 px-4 py-3 bg-gray-50/40">
            <div v-if="relevantTemplates(item).length" class="mb-2 flex flex-wrap gap-1.5">
              <button v-for="t in relevantTemplates(item)" :key="t.id"
                @click="item._reply = t.body"
                class="px-2.5 py-1 bg-white hover:bg-blue-50 text-gray-600 hover:text-blue-700 border border-gray-200 hover:border-blue-200 rounded-full text-xs font-medium transition-colors">
                {{ t.title }}
              </button>
            </div>
            <textarea
              v-model="item._reply"
              placeholder="Написать ответ…"
              rows="2"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent"
            ></textarea>
            <div class="flex justify-end mt-2">
              <button @click="sendReply(item)"
                :disabled="!item._reply?.trim()"
                :class="['px-4 py-2 rounded-lg text-sm font-medium transition-all',
                  item._reply?.trim()
                    ? 'bg-blue-600 hover:bg-blue-700 text-white'
                    : 'bg-gray-100 text-gray-400 cursor-not-allowed']">
                Опубликовать ответ
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Пагинация снизу -->
      <div v-if="totalPages > 1" class="flex items-center justify-center gap-2 mt-6">
        <button @click="currentPage = Math.max(1, currentPage - 1)"
          :disabled="currentPage === 1"
          class="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed">
          ← Назад
        </button>
        <div class="flex items-center gap-1.5">
          <input type="number" :min="1" :max="totalPages"
            :value="currentPage"
            @change="e => currentPage = Math.min(totalPages, Math.max(1, parseInt(e.target.value) || 1))"
            @keydown.enter="e => e.target.blur()"
            class="w-14 text-center border border-gray-200 rounded-lg px-1 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none" />
          <span class="text-sm text-gray-400">/ {{ totalPages }}</span>
        </div>
        <button @click="currentPage = Math.min(totalPages, currentPage + 1)"
          :disabled="currentPage === totalPages"
          class="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed">
          Вперёд →
        </button>
      </div>
    </div>

    <!-- ══ ОЧЕРЕДЬ: чаты — split panel ════════════════════════════════ -->
    <div v-if="activeSection === 'queue' && activeQTab === 'chat'"
      class="flex border-t border-gray-200 overflow-hidden"
      style="height: calc(100vh - 148px)">

      <!-- ── Левая панель: список чатов ────────────────────────────── -->
      <div class="w-80 shrink-0 flex flex-col border-r border-gray-200 bg-white">

        <!-- контролы: фильтры + кнопки -->
        <div class="px-3 py-2 border-b border-gray-100 flex items-center gap-1.5 shrink-0 bg-gray-50/50 flex-wrap">
          <!-- Ждёт ответа -->
          <button @click="pendingOnly = !pendingOnly; unreadOnly = false"
            :class="['inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full border font-medium transition-all',
              pendingOnly ? 'bg-orange-500 text-white border-orange-500' : 'bg-white text-gray-500 border-gray-200 hover:border-gray-300']">
            Ждёт ответа
            <span :class="['px-1 rounded-full font-bold text-xs',
              pendingOnly ? 'bg-white/20 text-white' : 'bg-orange-100 text-orange-700']">{{ pendingChatCount }}</span>
          </button>
          <!-- Непрочитанные -->
          <button @click="unreadOnly = !unreadOnly; pendingOnly = false"
            :class="['inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full border font-medium transition-all',
              unreadOnly ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-500 border-gray-200 hover:border-gray-300']">
            Новые
            <span v-if="unreadChatCount" :class="['px-1 rounded-full font-bold text-xs',
              unreadOnly ? 'bg-white/20 text-white' : 'bg-blue-100 text-blue-700']">{{ unreadChatCount }}</span>
          </button>
          <div class="ml-auto flex items-center gap-2">
            <button @click="markAllRead" title="Отметить все прочитанными"
              class="text-xs text-gray-400 hover:text-blue-500 transition-colors">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg>
            </button>
            <button @click="loadQueue" title="Обновить"
              class="text-gray-400 hover:text-gray-600 transition-colors">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
            </button>
          </div>
        </div>

        <!-- список -->
        <div class="flex-1 overflow-y-auto">
          <div v-if="queueLoading" class="flex justify-center py-10">
            <div class="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin"></div>
          </div>
          <div v-else-if="filteredItems.length === 0"
            class="flex flex-col items-center justify-center py-12 px-4 text-center">
            <div class="text-3xl mb-2">🎉</div>
            <p class="text-sm text-gray-500 font-medium">{{ unreadOnly ? 'Нет непрочитанных' : 'Чатов нет' }}</p>
          </div>
          <div v-else>
            <div v-for="item in pagedItems" :key="item.id"
              @click="selectChat(item)"
              :class="['flex gap-2.5 px-3 py-3 cursor-pointer border-b border-gray-100 transition-colors',
                chatItem?.id === item.id
                  ? 'bg-blue-50 border-l-[3px] border-l-blue-500'
                  : item.needs_answer
                    ? 'hover:bg-orange-50/40 border-l-[3px] border-l-orange-400'
                    : 'hover:bg-gray-50 border-l-[3px] border-l-transparent opacity-60']">

              <!-- фото товара -->
              <div class="shrink-0 w-11 h-11 rounded-lg overflow-hidden bg-gray-100 border border-gray-200 flex items-center justify-center">
                <img :src="wbPhotoUrl(item.nm_id)"
                  class="w-full h-full object-cover"
                  @error="e => { e.target.style.display='none' }" />
                <svg v-if="!item.nm_id" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#d1d5db" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/></svg>
              </div>

              <div class="flex-1 min-w-0">
                <div class="flex items-start justify-between gap-1 mb-0.5">
                  <span :class="['text-xs font-semibold truncate', item.needs_answer ? 'text-gray-900' : 'text-gray-500']">
                    {{ item.contact || 'Покупатель' }}
                  </span>
                  <div class="flex items-center gap-1 shrink-0">
                    <span v-if="hasNewMsg(item)" class="w-2 h-2 rounded-full bg-blue-500 animate-pulse block"></span>
                    <span v-if="item.needs_answer" class="text-xs text-orange-600 font-semibold">Ждёт</span>
                    <span class="text-xs text-gray-400" :title="`Последнее: ${fmtTs(item.last_ts)}`">
                      {{ fmtAge(item.age_sec) }}
                    </span>
                  </div>
                </div>
                <p class="text-xs text-gray-400 truncate leading-tight">{{ item.sku || item.nm_id || '—' }}</p>
                <p :class="['text-xs truncate mt-0.5 leading-tight', item.needs_answer ? 'text-gray-700' : 'text-gray-400']">
                  {{ item.body || '—' }}
                </p>
              </div>
            </div>
          </div>
        </div>

        <!-- пагинация левой панели -->
        <div v-if="totalPages > 1" class="border-t border-gray-100 px-3 py-2 flex items-center justify-between shrink-0 bg-white">
          <button @click="currentPage = Math.max(1, currentPage - 1)" :disabled="currentPage === 1"
            class="text-xs text-gray-500 hover:text-gray-700 disabled:opacity-40 px-1.5 py-1">← Пред.</button>
          <div class="flex items-center gap-1">
            <input type="number" :min="1" :max="totalPages" :value="currentPage"
              @change="e => currentPage = Math.min(totalPages, Math.max(1, parseInt(e.target.value)||1))"
              @keydown.enter="e => e.target.blur()"
              class="w-10 text-center border border-gray-200 rounded px-1 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none" />
            <span class="text-xs text-gray-400">/ {{ totalPages }}</span>
          </div>
          <button @click="currentPage = Math.min(totalPages, currentPage + 1)" :disabled="currentPage === totalPages"
            class="text-xs text-gray-500 hover:text-gray-700 disabled:opacity-40 px-1.5 py-1">След. →</button>
        </div>
      </div>

      <!-- ── Правая панель: переписка ───────────────────────────────── -->
      <div class="flex-1 flex flex-col overflow-hidden">

        <!-- Placeholder (ни один чат не выбран) -->
        <div v-if="!chatItem" class="flex-1 flex items-center justify-center bg-gray-50">
          <div class="text-center text-gray-400">
            <svg class="w-14 h-14 mx-auto mb-3 opacity-20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
            <p class="text-sm">Выберите чат из списка</p>
          </div>
        </div>

        <template v-else>
          <!-- Шапка: фото + товар + контекст клиента -->
          <div class="bg-white border-b border-gray-200 px-5 py-3 shrink-0">
            <div class="flex items-center gap-3">
              <div class="w-12 h-12 rounded-xl overflow-hidden bg-gray-100 border border-gray-200 shrink-0 flex items-center justify-center">
                <img :src="wbPhotoUrl(chatItem.nm_id)" class="w-full h-full object-cover"
                  @error="e => e.target.style.display='none'" />
                <svg v-if="!chatItem.nm_id" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#d1d5db" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/></svg>
              </div>
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                  <a v-if="chatItem.nm_id"
                    :href="`https://www.wildberries.ru/catalog/${chatItem.nm_id}/detail.aspx`" target="_blank"
                    class="text-sm font-semibold text-blue-600 hover:text-blue-700 truncate max-w-xs">
                    {{ chatItem.sku || `Арт. ${chatItem.nm_id}` }}
                  </a>
                  <span v-else class="text-sm font-semibold text-gray-700">Товар без артикула</span>
                  <span :class="['text-xs px-2 py-0.5 rounded-full font-medium', ageCls(chatItem.age_sec)]"
                    :title="`Чат открыт ${fmtAge(chatItem.age_sec)} назад`">
                    чат {{ fmtAge(chatItem.age_sec) }}
                  </span>
                </div>
                <p class="text-xs text-gray-400 mt-0.5">{{ chatItem.contact || 'Покупатель' }} · нм {{ chatItem.nm_id || '—' }}</p>
              </div>
              <button @click="dismissChatItem" title="В архив без ответа"
                class="text-gray-300 hover:text-red-400 transition-colors shrink-0">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
              </button>
            </div>

            <!-- Конкретные обращения этого клиента по этому заказу (как в WB ЛК) -->
            <div v-if="chatContextLoading" class="mt-2 text-xs text-gray-400">Загружаем…</div>
            <template v-else-if="chatContext">
              <div v-if="chatContext.specific?.claim || chatContext.specific?.review"
                class="mt-2 flex flex-wrap gap-1.5">

                <!-- Возврат этого клиента -->
                <button v-if="chatContext.specific.claim"
                  @click="expandedClaimId = expandedClaimId === chatContext.specific.claim.claim_id ? '' : chatContext.specific.claim.claim_id"
                  :class="['inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-medium transition-colors',
                    expandedClaimId === chatContext.specific.claim.claim_id
                      ? 'bg-orange-100 border-orange-400 text-orange-800'
                      : 'bg-orange-50 border-orange-300 text-orange-700 hover:bg-orange-100']">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.93"/></svg>
                  Возврат от {{ chatContext.specific.claim.claim_date }}
                </button>

                <!-- Отзыв этого клиента -->
                <button v-if="chatContext.specific.review"
                  @click="openReviewFromChat(chatContext.specific.review.id)"
                  class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-amber-300 bg-amber-50 text-amber-800 text-xs font-medium hover:bg-amber-100 transition-colors">
                  <svg width="12" height="12" viewBox="0 0 24 24" :fill="(chatContext.specific.review.rating||0)>=4?'#f59e0b':'#ef4444'"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
                  Отзыв от {{ chatContext.specific.review.created_date }}
                  <span v-if="chatContext.specific.review.state==='active'" class="text-rose-600">· ждёт ответа</span>
                </button>
              </div>

              <!-- Детали возврата (раскрывается по клику) -->
              <div v-if="expandedClaimId && expandedClaimId === chatContext.specific?.claim?.claim_id"
                class="mt-2 rounded-lg border border-orange-200 bg-orange-50 px-3 py-2.5">
                <div class="flex items-center justify-between mb-1.5">
                  <div class="flex items-center gap-2 text-xs font-semibold text-orange-800">
                    <span>{{ chatContext.specific.claim.claim_type_label || 'Возврат' }}</span>
                    <span class="text-orange-300">·</span>
                    <span :class="chatContext.specific.claim.status_label==='Одобрено' ? 'text-emerald-600' : 'text-orange-700'">
                      {{ chatContext.specific.claim.status_label }}
                    </span>
                  </div>
                  <button @click="expandedClaimId=''" class="text-gray-300 hover:text-gray-500 text-lg leading-none">×</button>
                </div>
                <p v-if="chatContext.specific.claim.body && chatContext.specific.claim.body !== '—'"
                  class="text-xs text-slate-700 leading-relaxed">{{ chatContext.specific.claim.body }}</p>
                <p v-else class="text-xs text-gray-400 italic">Комментарий не указан</p>
              </div>
            </template>
          </div>

          <!-- Лента сообщений -->
          <div ref="chatScroll" class="flex-1 overflow-y-auto px-5 py-4 space-y-3 bg-gray-50">
            <div v-if="chatLoading" class="flex justify-center py-8">
              <div class="w-7 h-7 border-4 border-blue-500 border-t-transparent rounded-full animate-spin"></div>
            </div>
            <div v-else-if="!chatMsgs.length" class="text-center text-gray-400 text-sm py-8">Сообщений нет</div>
            <template v-else>
              <div v-for="(msg, idx) in chatMsgs" :key="msg.id || idx"
                :class="['flex', isSeller(msg) ? 'justify-end' : 'justify-start']">
                <div :class="['max-w-[75%] rounded-2xl px-3.5 py-2',
                  isSeller(msg) ? 'bg-blue-600 text-white rounded-br-md' : 'bg-white border border-gray-200 text-gray-800 rounded-bl-md']">
                  <div v-if="msgImages(msg).length" class="mb-1.5 space-y-1.5">
                    <a v-for="(im, i) in msgImages(msg)" :key="i" :href="im" target="_blank" class="block">
                      <img :src="im" class="rounded-lg max-w-[220px] max-h-[260px] object-cover" />
                    </a>
                  </div>
                  <p v-if="msg.text" class="text-sm whitespace-pre-wrap break-words">{{ msg.text }}</p>
                  <p :class="['text-xs mt-0.5', isSeller(msg) ? 'text-white/60' : 'text-gray-400']">
                    {{ fmtTs(msg.createdAt) }}
                  </p>
                </div>
              </div>
            </template>
          </div>

          <!-- Поле ввода -->
          <div class="border-t border-gray-200 bg-white px-4 py-3 shrink-0">
            <div class="flex items-end gap-2">
              <textarea v-model="chatReply"
                @keydown.enter.exact.prevent="sendChatMessage"
                placeholder="Сообщение…" rows="1"
                class="flex-1 border border-gray-200 rounded-xl px-3.5 py-2.5 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-400 max-h-32"></textarea>
              <button @click="sendChatMessage" :disabled="!chatReply.trim() || chatSending"
                :class="['w-10 h-10 rounded-xl flex items-center justify-center shrink-0 transition-all',
                  chatReply.trim() && !chatSending ? 'bg-blue-600 hover:bg-blue-700 text-white' : 'bg-gray-100 text-gray-400 cursor-not-allowed']">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- ══ АРХИВ ══════════════════════════════════════════════════════ -->
    <div v-if="activeSection === 'archive'" class="max-w-7xl mx-auto px-4 py-4">
      <div class="flex flex-wrap items-center gap-3 mb-4">
        <div class="flex gap-1">
          <button v-for="t in [{key:'all',label:'Все'},{key:'review',label:'Отзывы'},
            {key:'question',label:'Вопросы'},{key:'chat',label:'Чаты'}]" :key="t.key"
            @click="archTypeFilter = t.key; loadArchive(1)"

            :class="['px-3 py-1.5 rounded-full text-xs font-medium border transition-all',
              archTypeFilter === t.key
                ? 'bg-gray-700 text-white border-gray-700'
                : 'bg-white text-gray-500 border-gray-200 hover:border-gray-300']">
            {{ t.label }}
          </button>
        </div>
      </div>

      <div v-if="archLoading" class="flex justify-center py-16">
        <div class="w-8 h-8 border-4 border-gray-400 border-t-transparent rounded-full animate-spin"></div>
      </div>
      <div v-else-if="archItems.length === 0" class="text-center py-16 text-gray-400">Архив пуст</div>
      <div v-else class="space-y-2">
        <div v-for="item in archItems" :key="item.mp + item.item_type + item.id"
          class="bg-white rounded-xl border border-gray-200 shadow-sm px-4 py-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex flex-wrap items-center gap-2">
              <span :class="['px-1.5 py-0.5 rounded text-xs font-medium', mpBadgeCls[item.mp] || 'bg-gray-100 text-gray-500']">{{ (item.mp||'').toUpperCase() }}</span>
              <span :class="['px-2 py-0.5 rounded-full text-xs', typeChipCls[item.item_type] || 'bg-gray-50 text-gray-500']">{{ typeLabel[item.item_type] || item.item_type }}</span>
              <span v-if="item.rating" class="flex items-center gap-px">
                <svg v-for="i in 5" :key="i" width="11" height="11" viewBox="0 0 24 24"
                  :fill="i <= item.rating ? '#f59e0b' : '#e5e7eb'"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
              </span>
              <span v-if="item.sku" class="text-xs text-gray-400">Арт. {{ item.sku }}</span>
              <span :class="['text-xs px-2 py-0.5 rounded-full border',
                item.archive_reason === 'dismissed'
                  ? 'bg-gray-100 text-gray-500 border-gray-200'
                  : 'bg-green-50 text-green-700 border-green-200']">
                {{ item.archive_reason === 'dismissed' ? 'Закрыто без ответа' : 'Отвечено' }}
              </span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-xs text-gray-400">{{ fmtDate(item.created_at) }}</span>
              <button v-if="item.archive_reason === 'dismissed'"
                @click="undismissItem(item)"
                class="text-xs text-indigo-600 hover:underline">Вернуть</button>
            </div>
          </div>
          <p v-if="item.body" class="text-sm text-gray-600 mt-2 line-clamp-2">{{ item.body }}</p>
          <p v-if="item.answer_text" class="text-xs text-green-700 mt-1 bg-green-50 rounded px-2 py-1">
            ↳ {{ item.answer_text }}
          </p>
          <p v-if="item.note" class="text-xs text-gray-400 mt-1">Заметка: {{ item.note }}</p>
        </div>
      </div>

      <div v-if="archTotalPages > 1" class="flex items-center justify-center gap-2 mt-6">
        <button @click="loadArchive(archPage - 1)" :disabled="archPage === 1"
          class="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 disabled:opacity-40">← Назад</button>
        <span class="text-sm text-gray-500">{{ archPage }} / {{ archTotalPages }}</span>
        <button @click="loadArchive(archPage + 1)" :disabled="archPage === archTotalPages"
          class="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 disabled:opacity-40">Вперёд →</button>
      </div>
    </div>

    <!-- ══ ШАБЛОНЫ ══════════════════════════════════════════════════════ -->
    <div v-if="activeSection === 'templates'" class="max-w-4xl mx-auto px-4 py-4">
      <div class="flex justify-between items-center mb-4">
        <h2 class="font-semibold text-gray-800">Шаблоны ответов</h2>
        <button @click="openTplForm()" class="px-3 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 transition-colors">
          + Создать шаблон
        </button>
      </div>

      <!-- Форма -->
      <div v-if="tplFormOpen" class="bg-white border border-gray-200 rounded-xl shadow-sm p-5 mb-4">
        <h3 class="font-semibold text-gray-800 mb-4">{{ editTpl.id ? 'Редактировать' : 'Новый шаблон' }}</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div class="md:col-span-2">
            <label class="block text-xs font-medium text-gray-600 mb-1">Название</label>
            <input v-model="editTpl.title" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400" />
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Площадка</label>
            <select v-model="editTpl.mp" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm">
              <option value="all">Все</option>
              <option value="wb">WB</option>
              <option value="ym">ЯМ</option>
              <option value="ozon">Ozon</option>
            </select>
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Тип</label>
            <select v-model="editTpl.item_type" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm">
              <option value="all">Все</option>
              <option value="review">Отзывы</option>
              <option value="question">Вопросы</option>
              <option value="chat">Чаты</option>
            </select>
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Рейтинг от</label>
            <input type="number" v-model.number="editTpl.min_rating" min="1" max="5"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm" />
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Рейтинг до</label>
            <input type="number" v-model.number="editTpl.max_rating" min="1" max="5"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm" />
          </div>
          <div class="md:col-span-2">
            <label class="block text-xs font-medium text-gray-600 mb-1">Текст шаблона</label>
            <textarea v-model="editTpl.body" rows="5"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-indigo-400"></textarea>
          </div>
          <div class="md:col-span-2">
            <label class="block text-xs font-medium text-gray-600 mb-1">Стоп-слова (по одному на строку)</label>
            <textarea v-model="editTpl.stop_words" rows="2" placeholder="брак&#10;возврат"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-indigo-400"></textarea>
          </div>
          <div class="md:col-span-2 flex flex-wrap gap-6">
            <label class="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input type="checkbox" v-model="editTpl.auto_reply" class="w-4 h-4 rounded accent-indigo-600" />
              Автоответ
            </label>
            <label class="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input type="checkbox" v-model="editTpl.requires_confirmation" class="w-4 h-4 rounded accent-amber-500" />
              Требует подтверждения
            </label>
            <label class="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input type="checkbox" v-model="editTpl.ignore_questions" class="w-4 h-4 rounded accent-indigo-600" />
              Игнорировать вопросительные
            </label>
            <label class="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input type="checkbox" v-model="editTpl.require_no_text" class="w-4 h-4 rounded accent-indigo-600" />
              Только без текста
            </label>
          </div>
        </div>
        <div class="flex gap-2 mt-4">
          <button @click="saveTpl" class="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700">
            {{ editTpl.id ? 'Сохранить' : 'Создать' }}
          </button>
          <button @click="tplFormOpen = false" class="px-4 py-2 bg-white border border-gray-200 rounded-lg text-sm font-medium hover:bg-gray-50">
            Отмена
          </button>
        </div>
      </div>

      <!-- Список шаблонов -->
      <div v-if="tplLoading" class="flex justify-center py-10">
        <div class="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
      </div>
      <div v-else-if="templates.length === 0" class="text-center py-16 text-gray-400">Шаблоны не добавлены</div>
      <div v-else class="space-y-2">
        <div v-for="t in templates" :key="t.id"
          class="bg-white rounded-xl border border-gray-200 shadow-sm px-4 py-3">
          <div class="flex items-start justify-between gap-3">
            <div class="flex-1 min-w-0">
              <div class="flex flex-wrap items-center gap-2 mb-1">
                <span class="font-medium text-gray-800 text-sm">{{ t.title }}</span>
                <span :class="['px-2 py-0.5 rounded text-xs font-bold uppercase', mpBadgeCls[t.mp] || 'bg-gray-100 text-gray-500']">{{ t.mp }}</span>
                <span v-if="t.auto_reply" class="text-xs bg-green-50 text-green-700 border border-green-200 rounded px-1.5">авто</span>
                <span v-if="t.requires_confirmation" class="text-xs bg-amber-50 text-amber-700 border border-amber-200 rounded px-1.5">подтверждение</span>
                <span v-if="t.stop_words" class="text-xs bg-red-50 text-red-600 border border-red-200 rounded px-1.5">стоп-слова</span>
              </div>
              <p class="text-sm text-gray-500 line-clamp-2">{{ t.body }}</p>
            </div>
            <div class="flex gap-2 shrink-0">
              <button @click="openTplForm(t)" class="text-xs text-indigo-600 hover:underline">Ред.</button>
              <button @click="deleteTpl(t.id)" class="text-xs text-red-500 hover:underline">Удалить</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ══ СТАТИСТИКА ══════════════════════════════════════════════════ -->
    <div v-if="activeSection === 'stats'" class="max-w-4xl mx-auto px-4 py-4">
      <div class="flex items-center gap-3 mb-4">
        <label class="text-sm text-gray-600">Период:</label>
        <select v-model.number="statsDays" @change="loadStats"
          class="border border-gray-200 rounded-lg px-2 py-1.5 text-sm">
          <option :value="7">7 дней</option>
          <option :value="14">14 дней</option>
          <option :value="30">30 дней</option>
        </select>
      </div>

      <div v-if="statsLoading" class="flex justify-center py-10">
        <div class="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
      </div>
      <div v-else>
        <!-- Сводные метрики -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <div class="bg-white rounded-xl border border-gray-200 shadow-sm px-4 py-3 text-center">
            <p class="text-2xl font-bold text-gray-900">{{ statTotal }}</p>
            <p class="text-xs text-gray-500 mt-0.5">Ответов отправлено</p>
          </div>
          <div class="bg-white rounded-xl border border-gray-200 shadow-sm px-4 py-3 text-center">
            <p class="text-2xl font-bold text-gray-900">{{ statAuto }}</p>
            <p class="text-xs text-gray-500 mt-0.5">Автоответов</p>
          </div>
          <div class="bg-white rounded-xl border border-gray-200 shadow-sm px-4 py-3 text-center">
            <p class="text-2xl font-bold text-gray-900">{{ statManual }}</p>
            <p class="text-xs text-gray-500 mt-0.5">Вручную</p>
          </div>
          <div class="bg-white rounded-xl border border-gray-200 shadow-sm px-4 py-3 text-center">
            <p class="text-2xl font-bold text-gray-900">{{ (statsData.managers || []).length }}</p>
            <p class="text-xs text-gray-500 mt-0.5">Сотрудников</p>
          </div>
        </div>

        <!-- По площадкам и типам -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
          <div class="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
            <h3 class="font-medium text-gray-700 mb-3 text-sm">По площадкам</h3>
            <div v-if="Object.keys(statsData.by_mp || {}).length" class="space-y-2">
              <div v-for="(cnt, mp) in statsData.by_mp" :key="mp" class="flex items-center justify-between text-sm">
                <span :class="['px-2 py-0.5 rounded text-xs font-bold uppercase', mpBadgeCls[mp] || 'bg-gray-100 text-gray-600']">{{ mp }}</span>
                <span class="font-semibold text-gray-800">{{ cnt }}</span>
              </div>
            </div>
            <p v-else class="text-gray-400 text-sm text-center py-4">Нет данных</p>
          </div>
          <div class="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
            <h3 class="font-medium text-gray-700 mb-3 text-sm">По типам</h3>
            <div v-if="Object.keys(statsData.by_type || {}).length" class="space-y-2">
              <div v-for="(cnt, tp) in statsData.by_type" :key="tp" class="flex items-center justify-between text-sm">
                <span :class="['px-2 py-0.5 rounded-full text-xs border', typeCls[tp] || 'text-gray-600 border-gray-200']">{{ typeLabel[tp] || tp }}</span>
                <span class="font-semibold text-gray-800">{{ cnt }}</span>
              </div>
            </div>
            <p v-else class="text-gray-400 text-sm text-center py-4">Нет данных</p>
          </div>
        </div>

        <!-- По сотрудникам -->
        <div class="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
          <h3 class="font-medium text-gray-700 mb-3 text-sm">По сотрудникам</h3>
          <div v-if="(statsData.managers || []).length" class="space-y-2">
            <div v-for="m in statsData.managers" :key="m.manager"
              class="flex items-center justify-between text-sm border-b border-gray-50 pb-2 last:border-0">
              <span class="text-gray-700 truncate">{{ m.manager === 'auto_reply' ? 'Автоответы' : m.manager }}</span>
              <div class="flex items-center gap-3 shrink-0 text-xs text-gray-400">
                <span>отзывы {{ m.reviews }}</span>
                <span>вопросы {{ m.questions }}</span>
                <span>чаты {{ m.chats }}</span>
                <span class="font-semibold text-gray-800 text-sm">{{ m.total }}</span>
              </div>
            </div>
          </div>
          <p v-else class="text-gray-400 text-sm text-center py-4">Нет данных за период</p>
        </div>
      </div>
    </div>

    <!-- ══ ОКНО ЧАТА (стиль ЛК ВБ) ══════════════════════════════════════ -->
    <transition name="fade">
      <div v-if="chatOpen" class="fixed inset-0 z-40 bg-black/40 flex items-center justify-center p-4" @click.self="closeChat">
        <div class="bg-white rounded-2xl shadow-2xl w-full max-w-2xl h-[85vh] flex flex-col overflow-hidden">

          <!-- Шапка окна: товар -->
          <div class="flex items-center gap-3 px-5 py-3 border-b border-gray-200 shrink-0">
            <div class="w-11 h-11 rounded-lg overflow-hidden bg-gray-100 border border-gray-200 shrink-0">
              <img v-if="chatItem?.photos?.length" :src="chatItem.photos[0]" class="w-full h-full object-cover" />
              <span v-else class="w-full h-full flex items-center justify-center text-gray-300">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/></svg>
              </span>
            </div>
            <div class="flex-1 min-w-0">
              <a v-if="chatItem?.nm_id"
                :href="`https://www.wildberries.ru/catalog/${chatItem.nm_id}/detail.aspx`" target="_blank"
                class="text-sm font-medium text-blue-600 hover:text-blue-700 truncate block">
                {{ chatItem?.sku || 'Товар' }}
              </a>
              <p class="text-xs text-gray-400">{{ chatItem?.contact || 'Покупатель' }} · Арт. {{ chatItem?.nm_id || '—' }}</p>
            </div>
            <button @click="closeChat" class="text-gray-400 hover:text-gray-600 shrink-0">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
            </button>
          </div>

          <!-- Контекст клиента: возврат / отзыв -->
          <div v-if="chatContextLoading" class="px-5 py-2 border-b border-gray-100 text-xs text-gray-400">Загружаем…</div>
          <template v-else-if="chatContext && (chatContext.specific?.claim || chatContext.specific?.review)">
            <div class="px-5 py-2 border-b border-gray-100 flex flex-wrap gap-1.5">
              <button v-if="chatContext.specific.claim"
                @click="expandedClaimId = expandedClaimId === chatContext.specific.claim.claim_id ? '' : chatContext.specific.claim.claim_id"
                :class="['inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-medium transition-colors',
                  expandedClaimId === chatContext.specific.claim.claim_id
                    ? 'bg-orange-100 border-orange-400 text-orange-800'
                    : 'bg-orange-50 border-orange-300 text-orange-700 hover:bg-orange-100']">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.93"/></svg>
                Возврат от {{ chatContext.specific.claim.claim_date }}
              </button>
              <button v-if="chatContext.specific.review"
                @click="openReviewFromChat(chatContext.specific.review.id)"
                class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-amber-300 bg-amber-50 text-amber-800 text-xs font-medium hover:bg-amber-100 transition-colors">
                <svg width="12" height="12" viewBox="0 0 24 24" :fill="(chatContext.specific.review.rating||0)>=4?'#f59e0b':'#ef4444'"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
                Отзыв от {{ chatContext.specific.review.created_date }}
                <span v-if="chatContext.specific.review.state==='active'" class="text-rose-600">· ждёт ответа</span>
              </button>
            </div>
            <div v-if="expandedClaimId === chatContext.specific?.claim?.claim_id"
              class="mx-5 mt-2 mb-1 rounded-lg border border-orange-200 bg-orange-50 px-3 py-2.5">
              <div class="flex items-center justify-between mb-1.5">
                <div class="flex items-center gap-2 text-xs font-semibold text-orange-800">
                  <span>{{ chatContext.specific.claim.claim_type_label || 'Возврат' }}</span>
                  <span class="text-orange-300">·</span>
                  <span :class="chatContext.specific.claim.status_label==='Одобрено' ? 'text-emerald-600' : 'text-orange-700'">
                    {{ chatContext.specific.claim.status_label }}
                  </span>
                </div>
                <button @click="expandedClaimId=''" class="text-gray-300 hover:text-gray-500 text-lg leading-none">×</button>
              </div>
              <p v-if="chatContext.specific.claim.body && chatContext.specific.claim.body !== '—'"
                class="text-xs text-slate-700 leading-relaxed">{{ chatContext.specific.claim.body }}</p>
              <p v-else class="text-xs text-gray-400 italic">Комментарий не указан</p>
            </div>
          </template>

          <!-- Лента сообщений -->
          <div ref="chatScroll" class="flex-1 overflow-y-auto px-5 py-4 space-y-3 bg-gray-50">
            <div v-if="chatLoading" class="flex justify-center py-8">
              <div class="w-7 h-7 border-4 border-blue-500 border-t-transparent rounded-full animate-spin"></div>
            </div>
            <div v-else-if="!chatMsgs.length" class="text-center text-gray-400 text-sm py-8">Сообщений нет</div>
            <template v-else>
              <div v-for="(msg, idx) in chatMsgs" :key="msg.id || idx"
                :class="['flex', isSeller(msg) ? 'justify-end' : 'justify-start']">
                <div :class="['max-w-[75%] rounded-2xl px-3.5 py-2',
                  isSeller(msg) ? 'bg-blue-600 text-white rounded-br-md' : 'bg-white border border-gray-200 text-gray-800 rounded-bl-md']">
                  <!-- Фото в сообщении -->
                  <div v-if="msgImages(msg).length" class="mb-1.5 space-y-1.5">
                    <a v-for="(im, i) in msgImages(msg)" :key="i" :href="im" target="_blank" class="block">
                      <img :src="im" class="rounded-lg max-w-[220px] max-h-[260px] object-cover" />
                    </a>
                  </div>
                  <p v-if="msg.text" class="text-sm whitespace-pre-wrap break-words">{{ msg.text }}</p>
                  <p :class="['text-xs mt-0.5', isSeller(msg) ? 'text-white/60' : 'text-gray-400']">{{ fmtTs(msg.createdAt || msg.addTime) }}</p>
                </div>
              </div>
            </template>
          </div>

          <!-- Поле ввода -->
          <div class="border-t border-gray-200 px-4 py-3 shrink-0">
            <div class="flex items-end gap-2">
              <textarea
                v-model="chatReply"
                @keydown.enter.exact.prevent="sendChatMessage"
                placeholder="Сообщение…"
                rows="1"
                class="flex-1 border border-gray-200 rounded-xl px-3.5 py-2.5 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent max-h-32"
              ></textarea>
              <button @click="sendChatMessage" :disabled="!chatReply.trim() || chatSending"
                :class="['w-10 h-10 rounded-xl flex items-center justify-center shrink-0 transition-all',
                  chatReply.trim() && !chatSending ? 'bg-blue-600 hover:bg-blue-700 text-white' : 'bg-gray-100 text-gray-400 cursor-not-allowed']">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    </transition>

    <!-- Toast -->
    <transition name="toast">
      <div v-if="toast" :class="['fixed bottom-6 right-6 z-50 px-4 py-3 rounded-xl shadow-lg text-sm font-medium text-white max-w-sm',
        toast.type === 'error' ? 'bg-red-600' : 'bg-green-600']">
        {{ toast.msg }}
      </div>
    </transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { apiFetch } from '../api'
import { usePlatformStore } from '../stores/platform'

const BASE = '/api/v1/cx-responses'
const platformStore = usePlatformStore()

// ── «Непрочитанные» чаты ─────────────────────────────────────────────────────
// Ключ: chat_id → last_ts при последнем открытии. Хранится в localStorage.
const VIEWED_KEY = 'cx_chat_viewed_ts'
const chatLastViewed = ref({})

function _loadViewed() {
  try { chatLastViewed.value = JSON.parse(localStorage.getItem(VIEWED_KEY) || '{}') } catch { chatLastViewed.value = {} }
}
function _saveViewed() {
  try { localStorage.setItem(VIEWED_KEY, JSON.stringify(chatLastViewed.value)) } catch {}
}
function hasNewMsg(item) {
  if (item.item_type !== 'chat') return false
  const lastView = chatLastViewed.value[item.id] || 0
  return (item.last_ts || 0) > lastView
}

function markAllRead() {
  const chats = queueItems.value.filter(i => i.item_type === 'chat')
  if (!chats.length) return
  chats.forEach(i => { chatLastViewed.value[i.id] = (i.last_ts || Date.now()) })
  _saveViewed()
  showToast(`Отмечено прочитанными: ${chats.length} чатов`, 'ok')
}

// ── Фото товара WB — через прокси бэкенда (обходит CORS и ищет basket) ────
function wbPhotoUrl(nmId) {
  if (!nmId) return ''
  return `/api/v1/cx-responses/wb-photo/${parseInt(nmId)}`
}

// ── Контекст клиента (другие отзывы по тому же товару) ─────────────────────
const chatContext        = ref(null)
const chatContextLoading = ref(false)

async function loadChatContext(item) {
  chatContext.value = null
  if (!item?.id) return
  chatContextLoading.value = true
  try {
    const r = await apiFetch(`${BASE}/chat-context/wb/${encodeURIComponent(item.id)}`)
    const d = await r.json()
    if (d.specific?.claim || d.specific?.review || d.reviews?.length || d.questions?.length || d.claims?.length) chatContext.value = d
  } catch { /* silent */ } finally {
    chatContextLoading.value = false
  }
}

// ── Фильтры чатов (только для вкладки Чаты) ─────────────────────────────────
const unreadOnly  = ref(false)
const pendingOnly      = ref(false)
const jumpToItemId     = ref(null)   // ID отзыва/вопроса для прыжка из чата
const expandedClaimId  = ref('')     // ID раскрытой претензии (''-= ни одна)
const unreadChatCount = computed(() =>
  queueItems.value.filter(i => i.item_type === 'chat' && hasNewMsg(i)).length
)
const pendingChatCount = computed(() =>
  queueItems.value.filter(i => i.item_type === 'chat' && i.needs_answer).length
)

// ── Выбор чата в split-panel ────────────────────────────────────────────────
async function selectChat(item) {
  chatLastViewed.value[item.id] = item.last_ts || Date.now()
  _saveViewed()
  loadChatContext(item)
  chatItem.value  = item
  chatMsgs.value  = []
  chatLoading.value = true
  try {
    const r = await apiFetch(`${BASE}/chat-messages/wb/${encodeURIComponent(item.id)}`)
    const d = await r.json()
    chatMsgs.value = (d.messages || []).slice().sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0))
    scrollChatBottom()
  } catch { chatMsgs.value = [] } finally { chatLoading.value = false }
  clearInterval(chatPollTimer)
  chatPollTimer = setInterval(() => fetchChatMsgs(true), 8000)
}

async function dismissChatItem() {
  if (!chatItem.value) return
  if (!confirm('Закрыть без ответа и перенести в архив?')) return
  const item = chatItem.value
  try {
    await apiFetch(`${BASE}/dismiss`, {
      method: 'POST',
      body: JSON.stringify({ mp: item.mp, item_type: item.item_type, item_id: String(item.id), note: '' }),
    })
    queueItems.value = queueItems.value.filter(i => i !== item)
    chatItem.value   = null
    chatContext.value = null
    showToast('Перенесено в архив', 'ok')
  } catch { showToast('Ошибка', 'error') }
}

// currentMp — читаем из глобального стора (тот же переключатель что в сайдбаре)
const currentMp = computed(() => platformStore.platform || 'all')

// ── Навигация ──────────────────────────────────────────────────────────────
const sections = [
  { key: 'queue',     label: 'Очередь'    },
  { key: 'archive',   label: 'Архив'      },
  { key: 'templates', label: 'Шаблоны'    },
  { key: 'stats',     label: 'Статистика' },
]
const activeSection = ref('queue')

function switchSection(key) {
  activeSection.value = key
  if (key === 'archive' && archItems.value.length === 0) loadArchive(1)
  if (key === 'stats' && !statsData.value.managers) loadStats()
}

// Перезагружаем очередь/архив когда меняется площадка в глобальном сторе
watch(currentMp, () => {
  if (activeSection.value === 'queue')   loadQueue()
  if (activeSection.value === 'archive') loadArchive(1)
})

const activeQTab = ref('all')

// При смене вкладки: сбросить unreadOnly, сбросить выбранный чат (если уходим из чатов)
watch(activeQTab, (newTab, oldTab) => {
  if (oldTab === 'chat') {
    chatItem.value    = null
    chatContext.value = null
    clearInterval(chatPollTimer)
    chatPollTimer = null
  }
  if (newTab !== 'chat') { unreadOnly.value = false; pendingOnly.value = false }
  // Сбрасываем jump через 3с после переключения (элемент уже на экране)
  if (newTab !== 'chat') setTimeout(() => { jumpToItemId.value = null }, 3000)
  currentPage.value = 1
})

const qTabs = [
  { key: 'all',      label: 'Все'     },
  { key: 'review',   label: 'Отзывы'  },
  { key: 'question', label: 'Вопросы' },
  { key: 'chat',     label: 'Чаты'    },
]

// ── Стили ─────────────────────────────────────────────────────────────────
const mpBadgeCls = {
  wb:   'bg-purple-100 text-purple-700',
  ym:   'bg-yellow-100 text-yellow-700',
  ozon: 'bg-blue-100 text-blue-700',
  all:  'bg-gray-100 text-gray-600',
}

const typeCls = {
  review:   'bg-rose-50   text-rose-700   border-rose-200',
  question: 'bg-sky-50    text-sky-700    border-sky-200',
  chat:     'bg-violet-50 text-violet-700 border-violet-200',
}
// компактные чипы типа без рамки — для карточек очереди
const typeChipCls = {
  review:   'bg-rose-50   text-rose-600',
  question: 'bg-sky-50    text-sky-600',
  chat:     'bg-violet-50 text-violet-600',
}
const typeLabel = { review: 'Отзыв', question: 'Вопрос', chat: 'Чат' }

// ── Очередь ────────────────────────────────────────────────────────────────
const queueLoading = ref(false)
const queueItems   = ref([])
const counts       = ref({})
const perPage      = ref(25)
const currentPage  = ref(1)
const sortDesc     = ref(true)   // true = сначала старые (по убыванию возраста)

function toggleSort() {
  sortDesc.value = !sortDesc.value
  currentPage.value = 1
}

const filteredItems = computed(() => {
  let list = Array.isArray(queueItems.value) ? queueItems.value : []
  if (activeQTab.value !== 'all') list = list.filter(i => i.item_type === activeQTab.value)
  if (pendingOnly.value) list = list.filter(i => i.needs_answer)
  else if (unreadOnly.value) list = list.filter(i => hasNewMsg(i))
  const dir = sortDesc.value ? 1 : -1
  const isChat = activeQTab.value === 'chat'
  const jumpId = jumpToItemId.value
  return [...list].sort((a, b) => {
    // Прыжок к конкретному элементу (из контекста чата) — он всегда первый
    if (jumpId) {
      const aJ = String(a.id) === String(jumpId) ? 1 : 0
      const bJ = String(b.id) === String(jumpId) ? 1 : 0
      if (aJ !== bJ) return bJ - aJ
    }
    if (isChat) {
      const aPend = a.needs_answer ? 1 : 0
      const bPend = b.needs_answer ? 1 : 0
      if (aPend !== bPend) return bPend - aPend
      return ((b.last_ts || 0) - (a.last_ts || 0)) * dir
    }
    const aN = hasNewMsg(a) ? 1 : 0
    const bN = hasNewMsg(b) ? 1 : 0
    if (aN !== bN) return bN - aN
    return ((a.age_sec || 0) - (b.age_sec || 0)) * dir
  })
})

const totalPages = computed(() => Math.max(1, Math.ceil(filteredItems.value.length / perPage.value)))

const pagedItems = computed(() => {
  const start = (currentPage.value - 1) * perPage.value
  return filteredItems.value.slice(start, start + perPage.value)
})

function getCnt(key) {
  if (!counts.value) return 0
  const c = counts.value
  if (key === 'all')      return c.total ?? 0
  if (key === 'review')   return c.reviews ?? 0
  if (key === 'question') return c.questions ?? 0
  if (key === 'chat')     return c.chats ?? 0
  return 0
}

async function loadQueue() {
  queueLoading.value = true
  currentPage.value  = 1
  try {
    const res = await apiFetch(`${BASE}/queue?mp=${currentMp.value}`)
    const data = await res.json()
    queueItems.value = (data.items || []).map(i => ({ ...i, _reply: '' }))
    counts.value = data.counts || {}
  } catch {
    showToast('Ошибка загрузки очереди', 'error')
  } finally {
    queueLoading.value = false
  }
}

// ── Окно чата (стиль ЛК ВБ) ─────────────────────────────────────────────────
const chatOpen    = ref(false)
const chatItem    = ref(null)
const chatMsgs    = ref([])
const chatLoading = ref(false)
const chatReply   = ref('')
const chatSending = ref(false)
const chatScroll  = ref(null)

function isSeller(msg) {
  const t = (msg.senderType || msg.sender || '').toLowerCase()
  return t === 'seller' || t === 'client_seller' || msg.isMine === true
}

function msgImages(msg) {
  // Бэкенд отдаёт готовый список URL (через прокси скачивания)
  return Array.isArray(msg.images) ? msg.images : []
}

let chatPollTimer = null

async function fetchChatMsgs(scroll = false) {
  if (!chatItem.value) return
  try {
    const r = await apiFetch(`${BASE}/chat-messages/wb/${chatItem.value.id}`)
    const d = await r.json()
    const msgs = (d.messages || []).slice().sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0))
    // Обновляем только если что-то поменялось (чтобы не дёргать скролл)
    if (msgs.length !== chatMsgs.value.length) {
      chatMsgs.value = msgs
      if (scroll) scrollChatBottom()
    }
  } catch { /* тихо */ }
}

function openReviewFromChat(reviewId) {
  jumpToItemId.value = String(reviewId)
  activeQTab.value   = 'review'
  currentPage.value  = 1
  chatItem.value     = null   // закрываем чат, переходим в очередь
  chatOpen.value     = false
}

async function openChat(item) {
  // Отмечаем чат как прочитанный (сохраняем last_ts на момент открытия)
  chatLastViewed.value[item.id] = item.last_ts || Date.now()
  _saveViewed()

  chatItem.value     = item
  chatOpen.value     = true
  chatReply.value    = ''
  chatMsgs.value     = []
  chatLoading.value  = true
  expandedClaimId.value = ''
  loadChatContext(item)
  try {
    const r = await apiFetch(`${BASE}/chat-messages/wb/${item.id}`)
    const d = await r.json()
    chatMsgs.value = (d.messages || []).slice().sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0))
    scrollChatBottom()
  } catch {
    chatMsgs.value = []
  } finally {
    chatLoading.value = false
  }
  // Живое обновление ленты каждые 8с, пока окно открыто
  clearInterval(chatPollTimer)
  chatPollTimer = setInterval(() => fetchChatMsgs(true), 8000)
}

function closeChat() {
  chatOpen.value    = false
  chatItem.value    = null
  chatContext.value = null
  expandedClaimId.value = ''
  clearInterval(chatPollTimer)
  chatPollTimer = null
}

function scrollChatBottom() {
  requestAnimationFrame(() => {
    if (chatScroll.value) chatScroll.value.scrollTop = chatScroll.value.scrollHeight
  })
}

async function sendChatMessage() {
  const txt = chatReply.value.trim()
  if (!txt || chatSending.value || !chatItem.value) return
  chatSending.value = true
  try {
    await apiFetch(`${BASE}/wb/chats/${encodeURIComponent(chatItem.value.id)}/message`, {
      method: 'POST',
      body: JSON.stringify({ text: txt }),
    })
    // Оптимистично добавляем в ленту
    chatMsgs.value.push({ text: txt, senderType: 'seller', createdAt: Date.now() })
    chatReply.value = ''
    scrollChatBottom()
    // Убираем из очереди — отвечено
    queueItems.value = queueItems.value.filter(i => i !== chatItem.value)
    showToast('Сообщение отправлено', 'ok')
  } catch (e) {
    showToast('Ошибка отправки: ' + (e?.message || ''), 'error')
  } finally {
    chatSending.value = false
  }
}

// ── Отправка ──────────────────────────────────────────────────────────────
// Карта роутов отправки: mp + тип → конкретный эндпоинт бэкенда
function replyEndpoint(item) {
  const id = encodeURIComponent(String(item.id))
  const map = {
    'wb:review':     `/wb/feedbacks/${id}/answer`,
    'wb:question':   `/wb/questions/${id}/answer`,
    'wb:chat':       `/wb/chats/${id}/message`,
    'ym:review':     `/ym/feedbacks/${id}/answer`,
    'ym:question':   `/ym/questions/${id}/answer`,
    'ozon:review':   `/ozon/reviews/${id}/answer`,
    'ozon:chat':     `/ozon/chats/${id}/message`,
  }
  return map[`${item.mp}:${item.item_type}`] || null
}

async function sendReply(item) {
  const txt = item._reply?.trim()
  if (!txt) return
  const path = replyEndpoint(item)
  if (!path) {
    showToast('Этот тип обращения нельзя отправить', 'error')
    return
  }
  try {
    await apiFetch(`${BASE}${path}`, {
      method: 'POST',
      body: JSON.stringify({ text: txt }),
    })
    showToast('Ответ отправлен', 'ok')
    queueItems.value = queueItems.value.filter(i =>
      !(i.mp === item.mp && i.item_type === item.item_type && String(i.id) === String(item.id))
    )
  } catch (e) {
    showToast('Ошибка отправки: ' + (e?.message || ''), 'error')
  }
}

// ── Модерация ──────────────────────────────────────────────────────────────
async function confirmMod(item) {
  try {
    await apiFetch(`${BASE}/moderation/${item.pending_mod_id}/confirm`, { method: 'POST' })
    showToast('Ответ отправлен', 'ok')
    queueItems.value = queueItems.value.filter(i => i !== item)
  } catch (e) {
    showToast('Ошибка: ' + (e?.message || ''), 'error')
  }
}
async function rejectMod(item) {
  try {
    await apiFetch(`${BASE}/moderation/${item.pending_mod_id}`, { method: 'DELETE' })
    item.pending_mod_id  = null
    item.pending_draft   = null
    showToast('Черновик отклонён', 'ok')
  } catch (e) {
    showToast('Ошибка', 'error')
  }
}

// ── Dismiss (архив без ответа) ─────────────────────────────────────────────
async function dismissItem(item) {
  if (!confirm('Закрыть без ответа и перенести в архив?')) return
  try {
    await apiFetch(`${BASE}/dismiss`, {
      method: 'POST',
      body: JSON.stringify({ mp: item.mp, item_type: item.item_type, item_id: String(item.id), note: '' }),
    })
    queueItems.value = queueItems.value.filter(i => i !== item)
    showToast('Перенесено в архив', 'ok')
  } catch {
    showToast('Ошибка', 'error')
  }
}

// ── Шаблоны ────────────────────────────────────────────────────────────────
const templates   = ref([])
const tplLoading  = ref(false)
const tplFormOpen = ref(false)
const editTpl     = ref({})

const tplDefaults = () => ({
  title: '', body: '', mp: 'all', item_type: 'all', tone: 'neutral',
  auto_reply: false, min_rating: 1, max_rating: 5, require_no_text: false,
  stop_words: '', ignore_questions: false, requires_confirmation: false,
})

async function loadTemplates() {
  tplLoading.value = true
  try {
    const r = await apiFetch(`${BASE}/templates`)
    const d = await r.json()
    templates.value = Array.isArray(d) ? d : (d.templates || [])
  } finally {
    tplLoading.value = false
  }
}

function openTplForm(t = null) {
  editTpl.value    = t ? { ...t } : tplDefaults()
  tplFormOpen.value = true
}

async function saveTpl() {
  const method = editTpl.value.id ? 'PUT' : 'POST'
  const url    = editTpl.value.id ? `${BASE}/templates/${editTpl.value.id}` : `${BASE}/templates`
  await apiFetch(url, { method, body: JSON.stringify(editTpl.value) })
  tplFormOpen.value = false
  await loadTemplates()
  showToast('Шаблон сохранён', 'ok')
}

async function deleteTpl(id) {
  if (!confirm('Удалить шаблон?')) return
  await apiFetch(`${BASE}/templates/${id}`, { method: 'DELETE' })
  await loadTemplates()
  showToast('Шаблон удалён', 'ok')
}

function relevantTemplates(item) {
  if (!Array.isArray(templates.value)) return []
  return templates.value.filter(t => {
    if (t.mp !== 'all' && t.mp !== item.mp) return false
    if (t.item_type !== 'all' && t.item_type !== item.item_type) return false
    return true
  }).slice(0, 6)
}

// ── Архив ──────────────────────────────────────────────────────────────────
const archItems      = ref([])
const archLoading    = ref(false)
const archPage       = ref(1)
const archTotalPages = ref(1)
const archTypeFilter = ref('all')

async function loadArchive(page = 1) {
  archLoading.value = true
  archPage.value = page
  try {
    const r = await apiFetch(
      `${BASE}/archive?mp=${currentMp.value}&item_type=${archTypeFilter.value}&page=${page}&per_page=25`
    )
    const d = await r.json()
    archItems.value      = d.items || []
    archTotalPages.value = d.total_pages || 1
  } finally {
    archLoading.value = false
  }
}

async function undismissItem(item) {
  await apiFetch(`${BASE}/dismiss/${item.mp}/${item.item_type}/${item.id}`, { method: 'DELETE' })
  await loadArchive(archPage.value)
  showToast('Возвращено в очередь', 'ok')
}

// ── Статистика ─────────────────────────────────────────────────────────────
const statsData    = ref({})
const statsLoading = ref(false)
const statsDays    = ref(7)

const statTotal  = computed(() => (statsData.value.managers || []).reduce((s, m) => s + (m.total || 0), 0))
const statAuto   = computed(() => (statsData.value.managers || []).reduce((s, m) => s + (m.auto_sent || 0), 0))
const statManual = computed(() => statTotal.value - statAuto.value)

async function loadStats() {
  statsLoading.value = true
  try {
    const r = await apiFetch(`${BASE}/stats?days=${statsDays.value}`)
    statsData.value = await r.json()
  } finally {
    statsLoading.value = false
  }
}

// ── Форматирование ─────────────────────────────────────────────────────────
function fmtAge(sec) {
  if (sec == null) return '?'
  if (sec < 3600)  return `${Math.round(sec / 60)} мин`
  if (sec < 86400) return `${Math.round(sec / 3600)} ч`
  return `${Math.round(sec / 86400)} дн`
}

function ageCls(sec) {
  if (sec == null)       return 'bg-gray-100 text-gray-500'
  if (sec > 86400 * 2)   return 'bg-red-100 text-red-700'
  if (sec > 86400)       return 'bg-orange-100 text-orange-700'
  if (sec > 3600 * 4)    return 'bg-yellow-100 text-yellow-700'
  return 'bg-green-100 text-green-700'
}

function fmtDate(s) {
  if (!s) return ''
  return new Date(s).toLocaleDateString('ru', { day: '2-digit', month: '2-digit', year: '2-digit' })
}

function fmtTs(s) {
  if (!s) return ''
  return new Date(s).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' })
}

// ── Toast ──────────────────────────────────────────────────────────────────
const toast = ref(null)
function showToast(msg, type = 'ok') {
  toast.value = { msg, type }
  setTimeout(() => { toast.value = null }, 3000)
}

// ── Init ───────────────────────────────────────────────────────────────────
onMounted(async () => {
  _loadViewed()
  await Promise.all([loadQueue(), loadTemplates()])
})
</script>

<style scoped>
.toast-enter-active, .toast-leave-active { transition: all .3s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(16px); }
.line-clamp-2 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.fade-enter-active, .fade-leave-active { transition: opacity .2s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
