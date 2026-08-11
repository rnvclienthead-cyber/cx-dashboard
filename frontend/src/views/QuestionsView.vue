<template>
  <div class="min-h-screen bg-gray-50">

    <!-- ══ ШАПКА ══════════════════════════════════════════════════════════ -->
    <div class="bg-white border-b border-gray-200 sticky top-0 z-20">
      <div class="max-w-7xl mx-auto px-5">

        <!-- Строка 1: заголовок + счётчики -->
        <div class="flex items-center justify-between gap-4 py-3">
          <div class="flex items-center gap-3">
            <div class="w-9 h-9 rounded-xl bg-sky-50 flex items-center justify-center text-sky-600 shrink-0">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>
              </svg>
            </div>
            <div>
              <h1 class="text-sm font-medium text-gray-900 leading-tight">Вопросы</h1>
              <p class="text-xs text-gray-400 leading-tight mt-0.5">WB · ЯМ — ответ публикуется на карточке товара</p>
            </div>
          </div>

          <div v-if="activeSection === 'queue' && counts.questions != null && counts.questions > 0"
            class="flex items-center gap-2">
            <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-sky-50 text-sky-700 border border-sky-200">
              <span class="w-1.5 h-1.5 rounded-full bg-sky-500 animate-pulse"></span>
              {{ counts.questions }} вопросов
            </span>
          </div>
        </div>

        <!-- Строка 2: вкладки секций -->
        <div class="flex -mb-px">
          <button v-for="s in visibleSections" :key="s.key"
            @click="switchSection(s.key)"
            :class="['px-4 py-2.5 text-sm font-medium border-b-2 transition-all',
              activeSection === s.key
                ? 'border-sky-500 text-sky-600'
                : 'border-transparent text-gray-500 hover:text-gray-700']">
            {{ s.label }}
          </button>
        </div>
      </div>

      <!-- Строка 3: обновить (только очередь) -->
      <div v-if="activeSection === 'queue'" class="border-t border-gray-100 bg-gray-50/60">
        <div class="max-w-7xl mx-auto px-5 py-2 flex items-center justify-end gap-3">
          <button @click="loadQueue()" class="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1 transition-colors">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
            Обновить
          </button>
        </div>
      </div>
    </div>

    <!-- ══ ОЧЕРЕДЬ ════════════════════════════════════════════════════════ -->
    <div v-if="activeSection === 'queue'" class="max-w-7xl mx-auto px-4 py-4">

      <div v-if="queueLoading" class="flex items-center justify-center py-20">
        <div class="text-center">
          <div class="w-10 h-10 border-4 border-sky-500 border-t-transparent rounded-full animate-spin mx-auto mb-3"></div>
          <p class="text-gray-400 text-sm">Загружаем вопросы…</p>
        </div>
      </div>

      <div v-else-if="filteredItems.length === 0" class="flex flex-col items-center justify-center py-20">
        <div class="text-5xl mb-4">🎉</div>
        <p class="text-gray-500 font-medium">Вопросов нет</p>
        <p class="text-gray-400 text-sm mt-1">Все вопросы обработаны</p>
      </div>

      <div v-else class="flex flex-wrap items-center justify-between gap-3 mb-4">
        <p class="text-sm text-gray-500">
          Показано {{ pagedItems.length }} из {{ filteredItems.length }}
        </p>
        <div class="flex items-center gap-2">
          <button @click="toggleSort"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-sm text-gray-600 hover:border-gray-300 hover:text-gray-800 transition-all">
            <span>{{ sortDesc ? 'Сначала старые' : 'Сначала новые' }}</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path v-if="sortDesc" d="M12 5v14M19 12l-7 7-7-7"/>
              <path v-else d="M12 19V5M5 12l7-7 7 7"/>
            </svg>
          </button>
          <select v-model.number="perPage" @change="currentPage = 1"
            class="border border-gray-200 rounded-lg px-2 py-1.5 text-sm text-gray-600 focus:outline-none focus:ring-1 focus:ring-sky-400">
            <option v-for="n in [10,25,50,100]" :key="n" :value="n">{{ n }} на стр.</option>
          </select>
        </div>
      </div>

      <!-- Карточки -->
      <div v-if="pagedItems.length > 0" class="space-y-2.5">
        <div v-for="item in pagedItems" :key="item.mp + item.item_type + item.id"
          class="bg-white rounded-xl border border-gray-200 hover:border-gray-300 transition-colors overflow-hidden">

          <div class="flex gap-3 p-4">
            <!-- Превью товара -->
            <div class="shrink-0">
              <div v-if="item.product_photo"
                class="w-[72px] h-24 rounded-lg overflow-hidden bg-gray-100 border border-gray-200">
                <img :src="item.product_photo" class="w-full h-full object-cover" />
              </div>
              <div v-else class="w-[72px] h-24 rounded-lg bg-gray-100 border border-gray-200 flex items-center justify-center text-gray-300">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/></svg>
              </div>
            </div>

            <!-- Основной блок -->
            <div class="flex-1 min-w-0">
              <div class="flex items-center justify-between gap-2 mb-1.5">
                <div class="flex items-center gap-2 min-w-0">
                  <span :class="['px-1.5 py-0.5 rounded text-xs font-medium', mpBadgeCls[item.mp]]">{{ item.mp.toUpperCase() }}</span>
                  <span class="px-2 py-0.5 rounded-full text-xs font-medium bg-sky-50 text-sky-600">Вопрос</span>
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

              <p v-if="item.sku" class="text-xs text-gray-400 mb-1">Арт. {{ item.sku }}</p>
              <p class="text-sm text-gray-700 leading-relaxed">{{ item.body }}</p>
            </div>
          </div>

          <!-- Модерация черновика -->
          <div v-if="item.pending_mod_id" class="border-t border-amber-200 bg-amber-50/70 px-4 py-3 space-y-2">
            <p class="text-xs font-medium text-amber-700">Черновик автоответа ожидает подтверждения</p>
            <p class="text-sm text-gray-700 whitespace-pre-wrap bg-white border border-amber-200 rounded-lg px-3 py-2">{{ item.pending_draft }}</p>
            <div class="flex flex-wrap gap-2">
              <button v-if="isWbQuestion(item)" @click="confirmMod(item, 'private')"
                title="Ответ увидит только автор вопроса"
                :aria-describedby="`question-moderation-publish-help-${item.mp}-${item.id}`"
                class="min-h-11 px-3 py-1.5 bg-white hover:bg-gray-50 text-gray-700 border border-gray-200 text-sm rounded-lg transition-colors font-medium">
                Подтвердить и ответить автору
              </button>
              <button @click="confirmMod(item, 'public')" :title="publicAnswerHelp(item)"
                :aria-describedby="`question-moderation-publish-help-${item.mp}-${item.id}`"
                class="min-h-11 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg transition-colors font-medium">
                {{ moderationPublishLabel(item) }}
              </button>
              <button @click="rejectMod(item)"
                class="px-3 py-1.5 bg-white hover:bg-gray-50 text-gray-600 border border-gray-200 text-sm rounded-lg transition-colors font-medium">
                Отклонить
              </button>
            </div>
            <p :id="`question-moderation-publish-help-${item.mp}-${item.id}`"
              class="text-[11px] text-amber-800 leading-snug">
              {{ questionReplyHelp(item) }}
            </p>
          </div>

          <!-- Форма ответа -->
          <div v-else class="border-t border-gray-100 px-4 py-3 bg-gray-50/40">
            <!-- Автоподбор как в чатах -->
            <div v-if="item._suggest?.reply_text"
              class="mb-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-900">
              <div class="flex items-start justify-between gap-2">
                <p class="font-medium">
                  Подобран шаблон
                  <span v-if="item._suggest.theme_name" class="font-normal opacity-80">
                    · {{ item._suggest.theme_name }}
                  </span>
                  <span v-if="item._suggest.confidence != null" class="font-normal opacity-70">
                    · {{ Math.round((item._suggest.confidence || 0) * 100) }}%
                  </span>
                  <span v-if="item._suggest.need_human" class="ml-1 text-rose-600">· лучше проверить</span>
                </p>
                <button type="button" @click="generateSuggest(item)" :disabled="item._suggestGenerating"
                  class="shrink-0 text-[11px] font-medium px-2 py-1 rounded-md bg-white border border-sky-200 text-sky-700 hover:bg-sky-100 disabled:opacity-50">
                  {{ item._suggestGenerating ? '…' : 'Сгенерировать' }}
                </button>
              </div>
            </div>
            <div v-else-if="item._suggestLoading" class="mb-2 text-[10px] text-gray-400">Подбираем шаблон…</div>
            <div v-else-if="item._suggestMiss"
              class="mb-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 flex items-center justify-between gap-2">
              <span>{{ item._suggestMiss.reason || 'Нет подходящего шаблона' }}</span>
              <button v-if="item._suggestMiss.can_generate !== false"
                type="button" @click="generateSuggest(item)" :disabled="item._suggestGenerating"
                class="shrink-0 text-[11px] font-medium px-2 py-1 rounded-md bg-white border border-amber-200 text-amber-800 hover:bg-amber-100 disabled:opacity-50">
                {{ item._suggestGenerating ? '…' : 'Сгенерировать ответ' }}
              </button>
            </div>

            <div v-if="chipsFor(item).length" class="mb-2 flex flex-wrap gap-1.5">
              <button v-for="t in chipsFor(item)" :key="t.id"
                type="button"
                @click="applyChip(item, t)"
                :class="[
                  'px-2.5 py-1 rounded-full text-xs font-medium transition-colors border',
                  isChipActive(item, t)
                    ? 'bg-sky-600 text-white border-sky-600'
                    : 'bg-white hover:bg-sky-50 text-gray-600 hover:text-sky-700 border-gray-200 hover:border-sky-200'
                ]">
                {{ t.title }}
              </button>
            </div>
            <textarea
              v-model="item._reply"
              placeholder="Написать ответ…"
              :rows="replyRows(item._reply)"
              class="w-full min-h-[6.5rem] border border-gray-200 rounded-lg px-3 py-2.5 text-sm leading-relaxed resize-y focus:outline-none focus:ring-2 focus:ring-sky-400 focus:border-transparent"
            ></textarea>
            <div class="mt-2 flex flex-wrap justify-end gap-2">
              <button v-if="isWbQuestion(item)" @click="sendReply(item, 'private')"
                :disabled="!item._reply?.trim()"
                title="Ответ увидит только автор вопроса"
                :aria-describedby="`question-publish-help-${item.mp}-${item.id}`"
                :class="['min-h-11 px-4 py-2 rounded-lg text-sm font-medium transition-all border',
                  item._reply?.trim()
                    ? 'bg-white hover:bg-gray-50 text-gray-700 border-gray-300'
                    : 'bg-gray-100 text-gray-400 border-gray-100 cursor-not-allowed']">
                Ответить автору
              </button>
              <button @click="sendReply(item, 'public')"
                :disabled="!item._reply?.trim()"
                :title="publicAnswerHelp(item)"
                :aria-describedby="`question-publish-help-${item.mp}-${item.id}`"
                :class="['min-h-11 px-4 py-2 rounded-lg text-sm font-medium transition-all',
                  item._reply?.trim()
                    ? 'bg-sky-600 hover:bg-sky-700 text-white'
                    : 'bg-gray-100 text-gray-400 cursor-not-allowed']">
                {{ publicAnswerLabel(item) }}
              </button>
            </div>
            <p :id="`question-publish-help-${item.mp}-${item.id}`"
              class="mt-2 text-[11px] text-gray-600 leading-snug">
              {{ questionReplyHelp(item) }}
            </p>
          </div>
        </div>
      </div>

      <!-- Пагинация -->
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
            class="w-14 text-center border border-gray-200 rounded-lg px-1 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-sky-400 focus:border-transparent [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none" />
          <span class="text-sm text-gray-400">/ {{ totalPages }}</span>
        </div>
        <button @click="currentPage = Math.min(totalPages, currentPage + 1)"
          :disabled="currentPage === totalPages"
          class="px-3 py-1.5 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed">
          Вперёд →
        </button>
      </div>
    </div>

    <!-- ══ ШАБЛОНЫ (плейбук вопросов) ═══════════════════════════════════════ -->
    <div v-if="activeSection === 'templates'" class="max-w-4xl mx-auto px-4 py-4">
      <div class="flex flex-wrap justify-between items-start gap-3 mb-4">
        <div>
          <h2 class="font-semibold text-gray-800">Плейбук вопросов</h2>
          <p class="text-xs text-gray-400 mt-0.5 max-w-xl">
            Копия шаблонов из чатов. Правки здесь влияют только на вопросы — чатовый плейбук не меняется.
          </p>
        </div>
        <div class="flex flex-wrap gap-2" v-if="canEditQuestionTemplates">
          <button @click="syncPlaybookFromChats" :disabled="pbSyncing"
            class="px-3 py-2 bg-white border border-gray-200 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50">
            {{ pbSyncing ? '…' : 'Подтянуть недостающие из чатов' }}
          </button>
          <button @click="openVariantForm()" class="px-3 py-2 bg-sky-600 text-white rounded-lg text-sm font-medium hover:bg-sky-700">
            + Вариант
          </button>
        </div>
        <p v-else class="text-[11px] text-gray-400 self-center">Только просмотр</p>
      </div>

      <div v-if="tplFormOpen && canEditQuestionTemplates" class="bg-white border border-gray-200 rounded-xl shadow-sm p-5 mb-4">
        <h3 class="font-semibold text-gray-800 mb-4">{{ editVariant.id ? 'Редактировать вариант' : 'Новый вариант' }}</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Тема</label>
            <select v-model="editVariant.theme_key" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm">
              <option v-for="th in pbThemes" :key="th.theme_key" :value="th.theme_key">{{ th.name }}</option>
            </select>
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Площадка</label>
            <select v-model="editVariant.mp" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm">
              <option value="">Все / WB</option>
              <option value="wb">WB</option>
              <option value="ym">ЯМ</option>
            </select>
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Ветка (ключ)</label>
            <input v-model="editVariant.branch_key" placeholder="пусто = основная"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400" />
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Подпись ветки</label>
            <input v-model="editVariant.branch_label"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400" />
          </div>
          <div class="md:col-span-2">
            <label class="block text-xs font-medium text-gray-600 mb-1">Текст ответа</label>
            <textarea v-model="editVariant.body" rows="6"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm resize-y focus:outline-none focus:ring-2 focus:ring-sky-400"></textarea>
          </div>
        </div>
        <div class="flex gap-2 mt-4">
          <button @click="saveVariant" class="px-4 py-2 bg-sky-600 text-white rounded-lg text-sm font-medium hover:bg-sky-700">
            {{ editVariant.id ? 'Сохранить' : 'Создать' }}
          </button>
          <button @click="tplFormOpen = false" class="px-4 py-2 bg-white border border-gray-200 rounded-lg text-sm font-medium hover:bg-gray-50">
            Отмена
          </button>
        </div>
      </div>

      <div v-if="pbLoading" class="flex justify-center py-10">
        <div class="w-8 h-8 border-4 border-sky-500 border-t-transparent rounded-full animate-spin"></div>
      </div>
      <div v-else-if="pbThemes.length === 0" class="text-center py-16 text-gray-400">
        Плейбук пуст.
        <button @click="syncPlaybookFromChats" class="ml-1 text-sky-600 hover:underline">Скопировать из чатов</button>
      </div>
      <div v-else class="space-y-3">
        <div v-for="th in pbThemes" :key="th.theme_key"
          class="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <button type="button" @click="toggleTheme(th.theme_key)"
            class="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-gray-50">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-medium text-gray-800 text-sm">{{ th.name }}</span>
                <span class="text-[10px] uppercase tracking-wide text-gray-400">{{ th.theme_key }}</span>
                <span class="text-xs text-gray-400">{{ (th.variants || []).length }} вар.</span>
              </div>
            </div>
            <span class="text-gray-400 text-sm">{{ expandedThemes[th.theme_key] ? '▾' : '▸' }}</span>
          </button>
          <div v-if="expandedThemes[th.theme_key]" class="border-t border-gray-100 divide-y divide-gray-50">
            <div v-for="v in th.variants" :key="v.id" class="px-4 py-3">
              <div class="flex items-start justify-between gap-3 mb-1.5">
                <div class="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <span v-if="v.branch_label || v.branch_key" class="px-1.5 py-0.5 rounded bg-gray-100">
                    {{ v.branch_label || v.branch_key || 'основной' }}
                  </span>
                  <span v-else class="px-1.5 py-0.5 rounded bg-gray-100">основной</span>
                  <span v-if="v.mp" class="uppercase font-medium">{{ v.mp }}</span>
                </div>
                <div v-if="canEditQuestionTemplates" class="flex gap-2 shrink-0">
                  <button @click="openVariantForm(v)" class="text-xs text-sky-600 hover:underline">Ред.</button>
                  <button @click="deleteVariant(v.id)" class="text-xs text-red-500 hover:underline">Удалить</button>
                </div>
              </div>
              <p class="text-sm text-gray-600 whitespace-pre-wrap leading-relaxed">{{ v.body }}</p>
            </div>
            <div v-if="!(th.variants || []).length" class="px-4 py-3 text-sm text-gray-400">Нет вариантов</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ══ АВТООТВЕТЫ ═══════════════════════════════════════════════════════ -->
    <div v-if="activeSection === 'settings' && !isCsManager" class="max-w-3xl mx-auto px-4 py-6 space-y-4">
      <div class="bg-white border border-gray-200 rounded-xl p-5">
        <h2 class="font-semibold text-gray-800 mb-1">Автоответы на вопросы</h2>
        <p class="text-sm text-gray-500 leading-relaxed mb-4">
          В очереди шаблон подбирается классификатором по плейбуку вопросов (вкладка «Шаблоны»).
          Автоотправка без проверки пока готовится — сейчас тексты подставляются в поле ответа.
        </p>
        <ul class="text-sm text-gray-600 space-y-2 list-disc pl-5">
          <li>WB: можно опубликовать ответ на карточке или отправить его только автору вопроса.</li>
          <li>ЯМ: ответ публикуется на карточке товара.</li>
          <li>Ozon в очереди вопросов пока нет (только отзывы/чаты).</li>
          <li>Плейбук вопросов — отдельная копия чатового; можно править независимо.</li>
        </ul>
        <div class="mt-4 flex flex-wrap gap-3 text-xs">
          <span class="px-2.5 py-1 rounded-full bg-sky-50 text-sky-700 border border-sky-200">
            Тем: {{ pbThemes.length }}
          </span>
          <span class="px-2.5 py-1 rounded-full bg-slate-50 text-slate-600 border border-slate-200">
            Вариантов: {{ pbVariantCount }}
          </span>
        </div>
      </div>
    </div>

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
import { usePermissionsStore } from '../stores/permissions'
import { useToast } from '../composables/useToast'
import { useCxQueueActions } from '../composables/useCxQueueActions'
import { CX_BASE as BASE, MP_BADGE_CLASSES as mpBadgeCls, fmtAge, ageCls } from '../utils/cx'

const platformStore = usePlatformStore()
const permissionsStore = usePermissionsStore()
const currentMp = computed(() => platformStore.platform || 'all')
const canEditQuestionTemplates = computed(() =>
  ['admin', 'moderator'].includes(permissionsStore.role)
)
const isCsManager = computed(() => permissionsStore.role === 'cs_manager')

const { toast, showToast } = useToast()

// ── Навигация ──────────────────────────────────────────────────────────────
const sections = [
  { key: 'queue',     label: 'Очередь'  },
  { key: 'templates', label: 'Шаблоны'  },
  { key: 'settings',  label: 'Автоответы' },
]
const visibleSections = computed(() =>
  isCsManager.value
    ? sections.filter(s => s.key !== 'settings')
    : sections
)
const activeSection = ref('queue')

function switchSection(key) {
  activeSection.value = key
}

watch(currentMp, () => {
  loadPlaybookTemplates()
  if (activeSection.value === 'queue') loadQueue()
})

// ── Очередь ────────────────────────────────────────────────────────────────
const queueLoading = ref(false)
const queueItems   = ref([])
const counts       = ref({})
const perPage      = ref(25)
const currentPage  = ref(1)
const sortDesc     = ref(true)

function toggleSort() {
  sortDesc.value = !sortDesc.value
  currentPage.value = 1
}

const filteredItems = computed(() => {
  let list = (Array.isArray(queueItems.value) ? queueItems.value : [])
    .filter(i => i.item_type === 'question')
  const dir = sortDesc.value ? 1 : -1
  return [...list].sort((a, b) => ((a.age_sec || 0) - (b.age_sec || 0)) * dir)
})

const totalPages = computed(() => Math.max(1, Math.ceil(filteredItems.value.length / perPage.value)))
const pagedItems = computed(() => {
  const start = (currentPage.value - 1) * perPage.value
  return filteredItems.value.slice(start, start + perPage.value)
})

async function loadQueue() {
  queueLoading.value = true
  currentPage.value  = 1
  try {
    const res = await apiFetch(`${BASE}/queue?mp=${currentMp.value}`)
    const data = await res.json()
    queueItems.value = (data.items || []).map(i => ({
      ...i,
      _reply: '',
      _suggest: null,
      _suggestMiss: null,
      _suggestLoading: false,
      _suggestGenerating: false,
    }))
    counts.value = data.counts || {}
    // Автоподбор для первой страницы
    suggestForPage()
  } catch {
    showToast('Ошибка загрузки очереди', 'error')
  } finally {
    queueLoading.value = false
  }
}

const BOT = '/api/v1/chat-bot'
const QPB = '/api/v1/question-playbook'
const PB  = '/api/v1/chat-playbook'
const playbookTemplates = ref([])
const pbThemes = ref([])
const pbLoading = ref(false)
const pbSyncing = ref(false)
const tplFormOpen = ref(false)
const editVariant = ref({})
const expandedThemes = ref({})
const replySignature = ref('')

const pbVariantCount = computed(() =>
  pbThemes.value.reduce((n, th) => n + ((th.variants || []).length), 0)
)

function replyRows(text) {
  const lines = String(text || '').split('\n').length
  const approx = Math.ceil(String(text || '').length / 72)
  return Math.min(14, Math.max(4, Math.max(lines, approx) + 1))
}

function isWbQuestion(item) {
  return item.mp === 'wb' && item.item_type === 'question'
}

function publicAnswerLabel(item) {
  return isWbQuestion(item) ? 'Опубликовать на карточке' : 'Опубликовать ответ'
}

function moderationPublishLabel(item) {
  return `Подтвердить и ${publicAnswerLabel(item).toLowerCase()}`
}

function publicAnswerHelp(item) {
  return isWbQuestion(item)
    ? 'Ответ будет виден всем покупателям в разделе «Вопросы» на карточке товара.'
    : 'Ответ публикуется на карточке товара.'
}

function questionReplyHelp(item) {
  return isWbQuestion(item)
    ? '«Опубликовать» увидят все покупатели на карточке. «Ответить автору» увидит только автор вопроса.'
    : publicAnswerHelp(item)
}

function normalizeBody(s) {
  return String(s || '').replace(/\s+/g, ' ').trim()
}

function withSignature(body) {
  const text = String(body || '').trim()
  const sig = (replySignature.value || '').trim()
  if (!text || !sig) return text
  if (text.includes(sig) || /команда\s+видовито/i.test(text)) return text
  return `${text}\n\n${sig}`
}

function isChipActive(item, t) {
  const reply = normalizeBody(item._reply)
  if (!reply) return false
  const body = normalizeBody(t.body)
  return reply.includes(body.slice(0, Math.min(60, body.length))) || body.includes(reply.slice(0, 60))
}

function applyChip(item, t) {
  item._reply = withSignature(t.body || '')
  item._chipId = t.id
}

async function loadSignature() {
  try {
    const r = await apiFetch(PB)
    const d = await r.json()
    const row = (d.settings || []).find(s => s.key === 'signature')
    replySignature.value = (row?.value || '').trim()
  } catch {
    replySignature.value = ''
  }
}

function chipsFor(item) {
  const fromPb = (playbookTemplates.value || []).filter(t =>
    t.mp === 'all' || !t.mp || t.mp === item.mp
  )
  const theme = item._suggest?.theme
  const ordered = [...fromPb].sort((a, b) => {
    if (theme && a.theme_key === theme) return -1
    if (theme && b.theme_key === theme) return 1
    return 0
  })
  const seen = new Set()
  return ordered.filter(t => {
    const k = t.id || t.title
    if (seen.has(k)) return false
    seen.add(k)
    return true
  }).slice(0, 12)
}

async function loadPlaybookTemplates() {
  try {
    const mp = currentMp.value === 'all' ? 'all' : currentMp.value
    const r = await apiFetch(`${BOT}/question-templates?mp=${mp}`)
    const d = await r.json()
    playbookTemplates.value = d.templates || []
  } catch {
    playbookTemplates.value = []
  }
}

async function loadQuestionPlaybook() {
  pbLoading.value = true
  try {
    const r = await apiFetch(QPB)
    const d = await r.json()
    pbThemes.value = d.themes || []
    if (!Object.keys(expandedThemes.value).length && pbThemes.value[0]) {
      expandedThemes.value = { [pbThemes.value[0].theme_key]: true }
    }
  } catch {
    pbThemes.value = []
    showToast('Не удалось загрузить плейбук вопросов', 'error')
  } finally {
    pbLoading.value = false
  }
}

function toggleTheme(key) {
  expandedThemes.value = {
    ...expandedThemes.value,
    [key]: !expandedThemes.value[key],
  }
}

function openVariantForm(v = null) {
  if (v) {
    editVariant.value = { ...v }
  } else {
    editVariant.value = {
      theme_key: pbThemes.value[0]?.theme_key || 'nekomplekt',
      branch_key: '',
      branch_label: '',
      body: '',
      mp: '',
      active: true,
    }
  }
  tplFormOpen.value = true
}

async function saveVariant() {
  const v = editVariant.value
  if (!v.body?.trim()) {
    showToast('Введите текст шаблона', 'error')
    return
  }
  try {
    if (v.id) {
      await apiFetch(`${QPB}/variants/${v.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          body: v.body,
          branch_label: v.branch_label || '',
          mp: v.mp || '',
          theme_key: v.theme_key,
          active: v.active !== false,
        }),
      })
    } else {
      await apiFetch(`${QPB}/variants`, {
        method: 'POST',
        body: JSON.stringify({
          theme_key: v.theme_key,
          branch_key: v.branch_key || '',
          branch_label: v.branch_label || '',
          body: v.body,
          mp: v.mp || '',
          active: true,
        }),
      })
    }
    tplFormOpen.value = false
    await Promise.all([loadQuestionPlaybook(), loadPlaybookTemplates()])
    showToast('Шаблон сохранён', 'ok')
  } catch {
    showToast('Ошибка сохранения', 'error')
  }
}

async function deleteVariant(id) {
  if (!confirm('Удалить вариант?')) return
  try {
    await apiFetch(`${QPB}/variants/${id}`, { method: 'DELETE' })
    await Promise.all([loadQuestionPlaybook(), loadPlaybookTemplates()])
    showToast('Удалено', 'ok')
  } catch {
    showToast('Ошибка удаления', 'error')
  }
}

async function syncPlaybookFromChats() {
  pbSyncing.value = true
  try {
    const r = await apiFetch(`${QPB}/sync-from-chats`, { method: 'POST' })
    const d = await r.json()
    await Promise.all([loadQuestionPlaybook(), loadPlaybookTemplates()])
    showToast(
      `Добавлено: тем ${d.added_themes || 0}, вариантов ${d.added_variants || 0}`,
      'ok',
    )
  } catch {
    showToast('Не удалось синхронизировать', 'error')
  } finally {
    pbSyncing.value = false
  }
}

async function suggestOne(item, { forceAi = false } = {}) {
  const text = (item.body || '').trim()
  if (!text) return
  if (forceAi) item._suggestGenerating = true
  else item._suggestLoading = true
  try {
    const r = await apiFetch(`${BOT}/suggest-question`, {
      method: 'POST',
      body: JSON.stringify({
        text,
        mp: item.mp || 'wb',
        article: item.sku || '',
        nm_id: item.nm_id ? Number(item.nm_id) : null,
        force_ai: forceAi,
      }),
    })
    const d = await r.json()
    if (d?.reply_text) {
      item._suggest = d
      item._suggestMiss = null
      if (!item._reply?.trim() || forceAi) item._reply = String(d.reply_text).replace(/^\s+/, '')
    } else {
      item._suggest = null
      item._suggestMiss = {
        reason: d?.reason || 'Нет подходящего шаблона',
        can_generate: d?.can_generate !== false,
      }
    }
  } catch {
    item._suggestMiss = { reason: 'Ошибка подбора шаблона', can_generate: true }
  } finally {
    item._suggestLoading = false
    item._suggestGenerating = false
  }
}

function suggestForPage() {
  const list = pagedItems.value || []
  const chunk = list.slice(0, 8)
  chunk.forEach(item => {
    if (item._suggest || item._suggestMiss || item._suggestLoading) return
    suggestOne(item)
  })
}

function generateSuggest(item) {
  return suggestOne(item, { forceAi: true })
}

watch([currentPage, perPage], () => {
  suggestForPage()
})

watch(activeSection, (s) => {
  if (s === 'templates' && !pbThemes.value.length) loadQuestionPlaybook()
})

// ── Отправка / модерация / dismiss (общая логика — см. composables/useCxQueueActions) ──
function replyEndpoint(item, visibility = 'public') {
  const id = encodeURIComponent(String(item.id))
  const map = {
    'wb:question': `/wb/questions/${id}/answer?visibility=${visibility === 'private' ? 'private' : 'public'}`,
    'ym:question': `/ym/questions/${id}/answer`,
  }
  return map[`${item.mp}:${item.item_type}`] || null
}

const { sendReply: _sendReply, confirmMod: _confirmMod, rejectMod, dismissItem } = useCxQueueActions(queueItems, showToast)
function sendReply(item, visibility = 'public') {
  return _sendReply(item, entry => replyEndpoint(entry, visibility))
}
function confirmMod(item, visibility = 'public') {
  return _confirmMod(item, isWbQuestion(item) ? visibility : '')
}

// ── Init ───────────────────────────────────────────────────────────────────
onMounted(async () => {
  await Promise.all([loadQueue(), loadPlaybookTemplates(), loadQuestionPlaybook(), loadSignature()])
})
</script>

<style scoped>
.toast-enter-active, .toast-leave-active { transition: all .3s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(16px); }
.line-clamp-2 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
</style>
