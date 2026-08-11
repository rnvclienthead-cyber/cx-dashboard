<script setup>
import { ref, computed, onMounted } from 'vue'
import {
  HelpCircle, Search, ChevronDown, ChevronUp,
  Package, Ruler, Wrench, RotateCcw, ShoppingBag, Dumbbell, Clock,
  MessageCircle, X, TrendingUp, ClipboardList, UserCheck, Copy, Check,
  LayoutGrid, ChevronRight, Loader2, ExternalLink, AlertTriangle
} from 'lucide-vue-next'
import { apiFetch } from '../api'

// ─── ВКЛАДКИ ─────────────────────────────────────────────────────────
const activeTab = ref('topics')  // 'topics' | 'cards'

// ─── FAQ ПО ТЕМАМ ─────────────────────────────────────────────────────
const searchQuery    = ref('')
const openItems      = ref(new Set())
const activeCategory = ref('all')
const copiedId       = ref(null)

const toggle = (id) => {
  if (openItems.value.has(id)) openItems.value.delete(id)
  else openItems.value.add(id)
  openItems.value = new Set(openItems.value)
}

const copyTemplate = (text, id) => {
  navigator.clipboard.writeText(text)
  copiedId.value = id
  setTimeout(() => { copiedId.value = null }, 2000)
}

const categories = [
  { id: 'all',        label: 'Все вопросы',        icon: HelpCircle,  color: 'slate'  },
  { id: 'missing',    label: 'Некомплект',          icon: Package,     color: 'red'    },
  { id: 'dimensions', label: 'Размеры',             icon: Ruler,       color: 'blue'   },
  { id: 'assembly',   label: 'Сборка',              icon: Wrench,      color: 'amber'  },
  { id: 'returns',    label: 'Возврат и брак',      icon: RotateCcw,   color: 'orange' },
  { id: 'spareparts', label: 'Запчасти и доп.',     icon: ShoppingBag, color: 'violet' },
  { id: 'capacity',   label: 'Нагрузка и условия',  icon: Dumbbell,    color: 'emerald'},
  { id: 'delivery',   label: 'Доставка',            icon: Clock,       color: 'sky'    },
]

const colorMap = {
  red:     { bg: 'bg-red-50',     border: 'border-red-200',    text: 'text-red-700',    badge: 'bg-red-100 text-red-700',     active: 'bg-red-600 text-white border-red-600'     },
  blue:    { bg: 'bg-blue-50',    border: 'border-blue-200',   text: 'text-blue-700',   badge: 'bg-blue-100 text-blue-700',   active: 'bg-blue-600 text-white border-blue-600'   },
  amber:   { bg: 'bg-amber-50',   border: 'border-amber-200',  text: 'text-amber-700',  badge: 'bg-amber-100 text-amber-700', active: 'bg-amber-500 text-white border-amber-500' },
  orange:  { bg: 'bg-orange-50',  border: 'border-orange-200', text: 'text-orange-700', badge: 'bg-orange-100 text-orange-700',active: 'bg-orange-500 text-white border-orange-500'},
  violet:  { bg: 'bg-violet-50',  border: 'border-violet-200', text: 'text-violet-700', badge: 'bg-violet-100 text-violet-700',active: 'bg-violet-600 text-white border-violet-600'},
  emerald: { bg: 'bg-emerald-50', border: 'border-emerald-200',text: 'text-emerald-700',badge: 'bg-emerald-100 text-emerald-700',active:'bg-emerald-600 text-white border-emerald-600'},
  sky:     { bg: 'bg-sky-50',     border: 'border-sky-200',    text: 'text-sky-700',    badge: 'bg-sky-100 text-sky-700',     active: 'bg-sky-600 text-white border-sky-600'     },
  slate:   { bg: 'bg-slate-50',   border: 'border-slate-200',  text: 'text-slate-700',  badge: 'bg-slate-100 text-slate-700', active: 'bg-slate-700 text-white border-slate-700' },
}

const topicColorMap = {
  'Некомплект':      { bg: 'bg-red-100',    text: 'text-red-700',    bar: 'bg-red-400'    },
  'Размеры':         { bg: 'bg-blue-100',   text: 'text-blue-700',   bar: 'bg-blue-400'   },
  'Сборка':          { bg: 'bg-amber-100',  text: 'text-amber-700',  bar: 'bg-amber-400'  },
  'Возврат/брак':    { bg: 'bg-orange-100', text: 'text-orange-700', bar: 'bg-orange-400' },
  'Запчасти':        { bg: 'bg-violet-100', text: 'text-violet-700', bar: 'bg-violet-400' },
  'Нагрузка/условия':{ bg: 'bg-emerald-100',text: 'text-emerald-700',bar: 'bg-emerald-400'},
  'Доставка':        { bg: 'bg-sky-100',    text: 'text-sky-700',    bar: 'bg-sky-400'    },
  'Наличие/цвет':    { bg: 'bg-teal-100',   text: 'text-teal-700',   bar: 'bg-teal-400'   },
  'Инструкция':      { bg: 'bg-indigo-100', text: 'text-indigo-700', bar: 'bg-indigo-400' },
}

// Уникальные проблемы, выявленные анализом ~900 нетипичных вопросов по карточкам
const CARD_INSIGHTS = {
  'Ведро со шваброй прямоугольное': [
    { tag: '🔧 Запчасти к швабре', text: 'Самый массовый уникальный запрос — покупатели хотят купить отдельно швабру (ломается крепление/флаундер), заглушки/пробки ведра (теряются, деформируются) и сменные насадки-тряпки. Прямой ответ: у нас не продаётся, рекомендовать возврат или хозяйственный магазин.' },
    { tag: '💧 Течь заглушек', text: 'Частая жалоба: заглушки не держатся, под ведром лужа. Ответ: проверить, что заглушки вставлены до щелчка, боком; если деформированы — оформить возврат как брак.' },
    { tag: '❓ Как работает система', text: 'Клиенты не понимают принцип двух отсеков (чистая/грязная вода) и прозрачной крышки. Нужна краткая текстовая инструкция: «Чистая вода — в закрытый отсек, отжим — в центр ведра, крышка закрывается поворотом».' },
  ],
  'Вешалка напольная 2 рейла': [
    { tag: '⚠️ Некомплект болтов/ключа', text: 'ГЛАВНАЯ боль карточки — 50+ вопросов о нехватке болтов, ключа-шестигранника, накладок на ножки. Клиенты не проверяют на ПВЗ. Рекомендация: добавить в описание карточки точный список деталей; в ответе на вопрос давать инструкцию по возврату с пометкой «Брак».' },
    { tag: '🛡️ Чехол для вешалки', text: 'Регулярный запрос — есть ли чехол от пыли. Ответа «да» нет. Рекомендовать поискать универсальный чехол на WB по размеру вешалки (поиск: «чехол для напольной вешалки 110 см»).' },
    { tag: '🎡 Колёсики', text: 'Покупатели спрашивают, можно ли докупить колёсики к обычной версии. Ответ: колёсики отдельно не продаём.' },
  ],
  'Вешалка напольная 2 рейла на колесиках': [
    { tag: '🎡 Проблема с колёсиками', text: 'Острая тема карточки: колёсики не входят в ножки (жёсткие пластиковые накладки), выпадают при передвижении, нет фиксации. В ответе: накладки вставляются ударом ладони, колёсики вставить в гнездо и повернуть до щелчка; если не получается — фото в Чат с продавцом.' },
    { tag: '⚠️ Некомплект болтов/колёс', text: 'Те же проблемы, что у обычной модели: нет болтов, нет колёс в комплекте. Дополнительно: иногда приходят заглушки вместо колёс (ошибка склада WB). Решение — возврат с пометкой «Брак/некомплект».' },
    { tag: '⚖️ Нагрузка', text: 'Клиенты спрашивают, выдержит ли вешалка тяжёлые вещи (пальто, пуховики). Ответ: до 30 кг, тяжёлые вещи — на верхней штанге, более лёгкие — на нижней.' },
  ],
  'Вешалка напольная шкаф': [
    { tag: '🧵 Тканевые полки отдельно', text: 'Самый частый уникальный запрос — купить тканевые полки отдельно (порвались, кот испортил, хотят добавить). Продаются ли — у нас нет. Рекомендовать поиск «тканевые вставки для вешалки-шкафа» на WB.' },
    { tag: '🔩 Соединители и крючки', text: 'Крючки (держат штангу для плечиков) и соединители E/F ломаются при перегрузке или падении. Покупатели ищут арт. запчастей. У нас не продаются — рекомендовать возврат или поиск «тройник для труб 25 мм» в хозяйственных магазинах.' },
    { tag: '↕️ Конструкция наклоняется', text: 'После длительной эксплуатации шкаф начинает давать крен. Решение: подтянуть все соединения, проверить равномерность нагрузки (не перегружать одну секцию), при необходимости — крепление к стене через анкер.' },
  ],
  'Обувница пластик 8 полок': [
    { tag: '🐱 Тканевые полки (кот!)', text: 'Самая яркая уникальная проблема: коты рвут когтями тканевые вставки полок. Продаём ли отдельно — нет. Рекомендация покупателю: поискать нетканый материал аналогичного размера на WB или в ткане-хозяйственном магазине.' },
    { tag: '🌱 Использование для рассады', text: 'Регулярный нестандартный запрос — подходит ли обувница под рассаду. Ответ: конструктивно подходит (полки выдерживают вес горшков), но тканевые вставки могут намокать; лучше убрать их при таком использовании.' },
    { tag: '🛡️ Чехол', text: 'Спрашивают чехол от пыли. У нас нет. Рекомендовать поиск «чехол для обувницы 8 ярусов» на WB.' },
  ],
  'Стеллаж широкий 5 полок': [
    { tag: '➕ Дополнительные полки', text: 'Самый частый уникальный запрос — купить дополнительные полки отдельно (хотят больше ярусов, не хватает полок). Ответ: дополнительные полки отдельно не продаются; для большего числа ярусов — заказать два стеллажа и компоновать.' },
    { tag: '↕️ Регулировка высоты', text: 'Покупатели ожидают регулировку расстояния между полками (как у некоторых конкурентов). Уточнить в ответе: на данной модели полки фиксированные, расположение не меняется.' },
    { tag: '🌱 Нестандартное применение', text: 'Спрашивают: можно ли использовать для рассады, в ванную, как дровник, для банок с заготовками. Ответ: металл может ржаветь при постоянной влаге (ванная, уличный балкон) — не рекомендуется без защитного покрытия; для сухих мест — подходит.' },
  ],
  'Сушилка пластик 3 уровня': [
    { tag: '⚠️ Болты и колёсики — некомплект', text: 'ОСТРЕЙШАЯ боль карточки: 80+ вопросов о нехватке болтов (12 шт.), колёсиков (4 шт.), боковых креплений. Выше всех других карточек по доле жалоб на некомплект. Рекомендовать проверять пакетик с фурнитурой на дне коробки; если реально нет — возврат с пометкой «Брак».' },
    { tag: '🔄 Боковые крепления не складываются', text: 'Популярная жалоба: боковые полки-крылья складываются самостоятельно под весом белья или не фиксируются. Решение: болты нужно затянуть туже (не до упора, но плотно); при полностью зажатых болтах боковины жёсткие.' },
    { tag: '🛏️ Постельное бельё', text: 'Спрашивают, влезет ли пододеяльник/простыня. Ответ: при раскрытых боковых крыльях — да, пододеяльник можно перебросить через несколько прутьев. Прутья достаточно толстые для прищепок.' },
  ],
  'Этажерка широкая 4 полки': [
    { tag: '🎡 Проставки для колёсиков', text: 'Уникальная проблема: проставки (адаптеры) для колёсиков часто отсутствуют или приходит 3 вместо 4. Без них колёсики не вставить в ножки. Решение: в чат с продавцом с фото; возврат с пометкой «Брак/некомплект».' },
    { tag: '🧺 Материал корзин', text: 'Клиенты не понимают, пластик или металл (сетка выглядит как пластик на фото). Ответ: корзины металлические сетчатые, не прогибаются под обычной нагрузкой.' },
    { tag: '⚖️ Устойчивость', text: 'Жалоба: этажерка заваливается на бок или шатается. Решение: колёсики с фиксаторами нужно заблокировать; при отсутствии фиксаторов — подложить резиновый коврик под ножки.' },
  ],
}

const wbCardUrl = (nmId) =>
  nmId ? `https://www.wildberries.ru/catalog/${nmId}/detail.aspx` : null

const faqTopicToRoute = {
  'Некомплект':      'missing-1',
  'Размеры':         'dim-1',
  'Сборка':          'assembly-1',
  'Возврат/брак':    'returns-1',
  'Запчасти':        'spare-1',
  'Нагрузка/условия':'cap-1',
  'Доставка':        'delivery-1',
}

const faqs = [
  {
    id: 'dim-1', category: 'dimensions', freq: 20,
    q: 'Покупатель спрашивает размеры: глубину, ширину, высоту, диаметр',
    template: `Здравствуйте! Благодарим за вопрос. Пожалуйста, уточните артикул товара — точные размеры конкретной модели указаны в карточке на Wildberries. Если вам нужна помощь с выбором подходящего размера, напишите нам в Чат с продавцом. С уважением, Софья из команды Видовито.`,
    clientFlow: [
      'Открыть карточку товара на WB → раздел «Характеристики» — там есть все размеры.',
      'Если размер не указан — написать в «Чат с продавцом» с артикулом товара.',
      'Для вешалок: высота 140–170 см (с колёсами +5–7 см), ширина 80–120 см, глубина 40–45 см.',
      'Диаметр труб на всех вешалках — 25 мм (стандарт).',
    ],
    staffFlow: [
      'Уточнить артикул товара у покупателя.',
      'Открыть карточку товара на WB и дать конкретные цифры — не отправлять смотреть самому.',
      'Если карточка не открывается — проверить в таблице артикулов в рабочей папке.',
      'Если покупатель уточняет «подойдёт ли в нишу X см» — дать ответ с запасом 2–3 см с каждой стороны.',
    ],
  },
  {
    id: 'missing-1', category: 'missing', freq: 17,
    q: 'Не хватает деталей в комплекте: болты, трубки, крепления, колёса, заглушки, ножки',
    template: `Здравствуйте. Благодарим Вас за обратную связь! Перед упаковкой продукции мы внимательно проверяем комплектацию, поэтому крайне огорчены Вашей ситуацией. Но также мы допускаем факт, что вы получили товар, который был возвращён предыдущим покупателем без пометки «брак». К сожалению, у нас нет возможности отправить вам нужные комплектующие. Рекомендуем оформить заявку на возврат в личном кабинете во вкладке «Покупки» с пометкой «брак», чтобы данный товар не попал к другому покупателю. Приносим свои искренние извинения за доставленные хлопоты. Надеемся, что следующие покупки товаров Видовито принесут Вам только радостные эмоции. С уважением, Софья из команды Видовито.`,
    clientFlow: [
      'Сначала тщательно проверить пакетик с фурнитурой — мелкие детали (болты, заглушки, колёсики) часто прячутся на дне.',
      'Если деталь точно отсутствует — оформить возврат: Профиль WB → Покупки → нужный заказ → «Вернуть товар» → причина «Брак».',
      'Сфотографировать вскрытую упаковку и отсутствующую деталь — это ускорит рассмотрение заявки WB.',
      'Сдать товар на ПВЗ в любой упаковке. Деньги вернутся в течение 3–10 рабочих дней.',
    ],
    staffFlow: [
      'Уточнить у покупателя: какая именно деталь отсутствует и есть ли у него возможность отправить фото через Чат с продавцом.',
      'Проверить в разделе «Отправка деталей» — есть ли техническая возможность доотправить нужную деталь через СДЭК. Если есть — предложить, это лучше для покупателя, чем возврат.',
      'Если доотправка невозможна — дать стандартный шаблонный ответ с инструкцией по возврату.',
      'Зафиксировать в комментарии к заказу тип недостающей детали — помогает выявлять системные проблемы упаковки.',
    ],
  },
  {
    id: 'assembly-1', category: 'assembly', freq: 11,
    q: 'Проблемы при сборке: детали не сходятся, не вставляются, нет инструкции, или конструкция шатается',
    template: `Здравствуйте! Благодарим за обращение. Инструкция по сборке вложена в коробку (небольшой листок). Порядок сборки вешалки: ножки → вертикальные стойки → горизонтальные перекладины снизу вверх → крючки. Трубки вставляются до характерного щелчка — лицевой стороной наружу, швом внутрь. После установки всех деталей равномерно затяните все болты, и конструкция станет жёсткой. Если останутся вопросы — напишите нам в Чат с продавцом, с удовольствием поможем. С уважением, Софья из команды Видовито.`,
    clientFlow: [
      'Найти инструкцию внутри коробки — небольшой листок с нумерованными деталями.',
      'Для вешалок: собирать снизу вверх — ножки, стойки, перекладины, крючки.',
      'Если деталь не входит — попробовать развернуть на 180°, не применять силу.',
      'Если конструкция шатается — затянуть все болты равномерно крест-накрест, как колёса у машины.',
      'Поставить изделие на ровную поверхность — на неровном полу шатание неизбежно.',
    ],
    staffFlow: [
      'Спросить, на каком шаге возникла проблема — чаще всего это неправильное направление детали или незатянутые болты.',
      'Если шатается после полной затяжки — возможен брак соединительного узла, предложить возврат.',
      'Если нет инструкции в коробке — описать порядок сборки устно, предложить связаться через Чат с продавцом для видео-подсказки.',
    ],
  },
  {
    id: 'delivery-1', category: 'delivery', freq: 9,
    q: 'Товар долго в статусе «Оформление» / «Продавец собирает заказ» / задерживается доставка',
    template: `Здравствуйте. Статус доставки до пункта выдачи смотрите в карточке заказа в приложении. Если срок сдвинулся или статус долго не меняется — актуальные детали быстрее подскажут в поддержке WB: Профиль → Задать вопрос → раздел про доставку. По самому товару (комплектация, сборка, качество) мы на связи здесь. С уважением, Софья из команды Видовито.`,
    clientFlow: [
      'Если статус «Продавец собирает заказ» более 5 рабочих дней — покупатель вправе отменить заказ: Профиль → Покупки → Отменить.',
      'Если товар уже передан на WB и задерживается в пути — обратиться в поддержку WB: Профиль → Задать вопрос.',
      'Если дата доставки переносится — уточнять сроки в поддержке WB по карточке заказа.',
    ],
    staffFlow: [
      'Уточнить статус заказа: «Оформление» (у нас на складе) или «В пути» (уже у WB).',
      'Если «Оформление» более 5 рабочих дней — проверить наличие товара на складе, при необходимости эскалировать к руководителю склада.',
      'Если товар уже у WB — спокойно направить в поддержку WB и предложить помощь по самому товару.',
      'Не обещать сроки доставки, которыми управляет логистика маркетплейса; формулировки без негатива в адрес WB.',
    ],
  },
  {
    id: 'returns-1', category: 'returns', freq: 8,
    q: 'Как оформить возврат? Брак, некомплект, не тот цвет, товар не понравился',
    template: `Здравствуйте. Очень жаль, что у Вас возникла эта ситуация. Просим оформить заявку на возврат в приложении Wildberries: Профиль → Покупки → нужный заказ → «Вернуть товар» → причина «Брак» (или «Не соответствует описанию» при пересорте). Сфотографируйте проблему — это ускорит рассмотрение. Упаковка не обязательна. Деньги вернутся в течение 3–10 рабочих дней после получения товара WB. Приносим искренние извинения. С уважением, Софья из команды Видовито.`,
    clientFlow: [
      'Открыть WB → Профиль → Покупки → нужный заказ → «Вернуть товар».',
      'Выбрать причину: «Брак» (если дефект/некомплект), «Не соответствует описанию» (если не тот цвет/размер).',
      'Сфотографировать дефект — ускоряет рассмотрение.',
      'Сдать на ПВЗ в любой упаковке — оригинальная не обязательна даже при вскрытии.',
      'Деньги возвращаются за 3–10 рабочих дней.',
    ],
    staffFlow: [
      'Уточнить причину: брак, некомплект, пересорт или просто не понравилось — от этого зависит подсказка.',
      'При браке/некомплекте — всегда рекомендовать пометку «Брак», не «Не понравился», так товар не уйдёт следующему покупателю.',
      'Возврат принимается и без оригинальной упаковки, и при частичной сборке — не вводить покупателя в заблуждение.',
      'Если покупатель не может оформить самостоятельно — объяснить пошагово, предложить Чат с продавцом.',
    ],
  },
  {
    id: 'spare-1', category: 'spareparts', freq: 6,
    q: 'Можно ли купить запчасти отдельно: крючки, болты, колёсики, швабру без ведра, полки',
    template: `Здравствуйте! К сожалению, комплектующие и запчасти отдельно у нас не продаются. Рекомендуем поискать подходящие детали в хозяйственных магазинах или на маркетплейсах — большинство стандартных деталей (крючки 25 мм, болты М6, колёсики) доступны в свободной продаже. С уважением, Софья из команды Видовито.`,
    clientFlow: [
      'Уточнить у продавца через «Чат с продавцом» — иногда возможна точечная доотправка нужной детали.',
      'Крючки для труб 25 мм — стандартные, есть в хозяйственных магазинах и на WB (поиск: «крючки для гардеробной трубы 25 мм»).',
      'Болты М6×12 или М4×10 — стандартные метизы, продаются везде.',
      'Колёсики М8 — универсальные, есть в хозяйственных магазинах.',
      'Швабра без ведра — в текущем ассортименте не выставлена, поискать аналог на WB по диаметру стакана для отжима (28–32 мм).',
    ],
    staffFlow: [
      'Сначала проверить через раздел «Отправка деталей» — есть ли возможность доотправить нужную деталь через СДЭК.',
      'Если доотправка невозможна — дать шаблонный ответ и порекомендовать хозяйственный магазин.',
      'Не обещать того, чего нет в наличии — стандартный ответ: «у нас, к сожалению, комплектующих отдельно нет».',
    ],
  },
  {
    id: 'cap-1', category: 'capacity', freq: 5,
    q: 'Какую нагрузку выдерживает товар? Можно ли использовать на балконе или на улице?',
    template: `Здравствуйте! Благодарим за вопрос. Точные характеристики нагрузки указаны в карточке товара на WB в разделе «Характеристики». Изделия предназначены для использования в помещении. На остеклённом балконе допустимо, на открытом — не рекомендуется (ржавчина от перепадов температур и влаги не является гарантийным случаем). С уважением, Софья из команды Видовито.`,
    clientFlow: [
      'Точные данные по нагрузке — в карточке товара на WB, раздел «Характеристики».',
      'Ориентировочно: одна полка этажерки — 10–15 кг, одна штанга вешалки — 15–20 кг, сушилка — до 30 кг влажного белья.',
      'На остеклённом тёплом балконе — можно, условия близки к помещению.',
      'На открытом балконе или улице — не рекомендуется, металл ржавеет. Если всё же планируется — обработать WD-40 или антикоррозийной смазкой.',
    ],
    staffFlow: [
      'Дать конкретные цифры из карточки — не ограничиваться «смотрите в описании».',
      'Если балкон открытый — честно предупредить о ржавчине и что это не гарантийный случай.',
      'Если покупатель спрашивает про устойчивость на улице — уточнить тип балкона (открытый/закрытый).',
    ],
  },
]

const getCat = (catId) => categories.find(c => c.id === catId)

const filteredFaqs = computed(() => {
  let list = [...faqs].sort((a, b) => b.freq - a.freq)
  if (activeCategory.value !== 'all') list = list.filter(f => f.category === activeCategory.value)
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.toLowerCase()
    list = list.filter(f =>
      f.q.toLowerCase().includes(q) ||
      f.template.toLowerCase().includes(q) ||
      f.clientFlow.some(s => s.toLowerCase().includes(q)) ||
      f.staffFlow.some(s => s.toLowerCase().includes(q))
    )
  }
  return list
})

const expandAll   = () => { openItems.value = new Set(filteredFaqs.value.map(f => f.id)) }
const collapseAll = () => { openItems.value = new Set() }

// ─── ПО КАРТОЧКАМ ─────────────────────────────────────────────────────
const cards         = ref([])
const cardsLoading  = ref(false)
const cardsError    = ref(null)
const expandedCard  = ref(null)
const cardSearch    = ref('')
const cardCategory  = ref('all')

const cardCategories = computed(() => {
  const cats = [...new Set(cards.value.map(c => c.category).filter(Boolean))]
  return cats.sort()
})

const filteredCards = computed(() => {
  let list = cards.value
  if (cardCategory.value !== 'all') list = list.filter(c => c.category === cardCategory.value)
  if (cardSearch.value.trim()) {
    const q = cardSearch.value.toLowerCase()
    list = list.filter(c => c.card.toLowerCase().includes(q))
  }
  return list
})

const loadCards = async () => {
  if (cards.value.length || cardsLoading.value) return
  cardsLoading.value = true
  cardsError.value   = null
  try {
    const res  = await apiFetch('/api/v1/faq/by-card')
    const data = await res.json()
    cards.value = data.cards || []
  } catch (e) {
    cardsError.value = 'Ошибка загрузки данных'
  } finally {
    cardsLoading.value = false
  }
}

const switchTab = (tab) => {
  activeTab.value = tab
  if (tab === 'cards') loadCards()
}

const toggleCard = (name) => {
  expandedCard.value = expandedCard.value === name ? null : name
}

const jumpToFaq = (topic) => {
  const faqId = faqTopicToRoute[topic]
  if (!faqId) return
  activeTab.value = 'topics'
  openItems.value = new Set([faqId])
  setTimeout(() => {
    document.getElementById('faq-item-' + faqId)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, 100)
}

const maxTotal = computed(() => Math.max(...(filteredCards.value.map(c => c.total) || [1])))
</script>

<template>
  <div class="min-h-screen bg-slate-50">

    <!-- Шапка -->
    <div class="bg-white border-b border-slate-200 px-6 py-5">
      <div class="max-w-4xl mx-auto">
        <div class="flex items-start justify-between gap-4">
          <div>
            <div class="flex items-center gap-2 mb-1">
              <HelpCircle class="w-5 h-5 text-emerald-600" />
              <h1 class="text-xl font-black text-slate-800">База знаний FAQ</h1>
            </div>
            <p class="text-sm text-slate-500">
              Ответы на самые частые вопросы клиентов — на основе анализа
              <strong class="text-slate-700">10 000+ обращений</strong> за 18 месяцев.
            </p>
          </div>
          <div class="flex items-center gap-1.5 text-xs text-slate-400 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 flex-shrink-0">
            <TrendingUp class="w-3.5 h-3.5 text-emerald-500" />
            <span>~120 вопросов/неделю</span>
          </div>
        </div>

        <!-- Статистика (только в теме) -->
        <div v-if="activeTab === 'topics'" class="mt-4 grid grid-cols-3 sm:grid-cols-7 gap-2">
          <div v-for="cat in categories.slice(1)" :key="cat.id"
               :class="['rounded-xl border p-2.5 text-center cursor-pointer transition-all hover:shadow-sm', colorMap[cat.color].bg, colorMap[cat.color].border]"
               @click="activeCategory = cat.id">
            <div :class="['text-xl font-black', colorMap[cat.color].text]">
              {{ faqs.filter(f => f.category === cat.id).reduce((s, f) => s + f.freq, 0) }}
            </div>
            <div :class="['text-[10px] font-semibold leading-tight mt-0.5', colorMap[cat.color].text, 'opacity-70']">вопр/нед</div>
            <div class="text-[10px] text-slate-500 leading-tight mt-1">{{ cat.label }}</div>
          </div>
        </div>

        <!-- Вкладки -->
        <div class="mt-4 flex gap-1 bg-slate-100 rounded-xl p-1 w-fit">
          <button @click="switchTab('topics')"
            :class="['flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-all',
              activeTab === 'topics' ? 'bg-white shadow-sm text-slate-800' : 'text-slate-500 hover:text-slate-700']">
            <HelpCircle class="w-4 h-4" />
            По темам
          </button>
          <button @click="switchTab('cards')"
            :class="['flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-all',
              activeTab === 'cards' ? 'bg-white shadow-sm text-slate-800' : 'text-slate-500 hover:text-slate-700']">
            <LayoutGrid class="w-4 h-4" />
            По карточкам товаров
          </button>
        </div>
      </div>
    </div>

    <!-- ══ ВКЛАДКА: ПО ТЕМАМ ══════════════════════════════════════════ -->
    <div v-if="activeTab === 'topics'" class="max-w-4xl mx-auto px-4 py-6 space-y-4">

      <div class="relative">
        <Search class="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
        <input v-model="searchQuery" type="text" placeholder="Поиск по вопросам, ответам и инструкциям..."
          class="w-full pl-10 pr-10 py-3 bg-white border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400 shadow-sm" />
        <button v-if="searchQuery" @click="searchQuery = ''" class="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
          <X class="w-4 h-4" />
        </button>
      </div>

      <div class="flex flex-wrap gap-2">
        <button v-for="cat in categories" :key="cat.id" @click="activeCategory = cat.id"
          :class="['flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-semibold border transition-all',
            activeCategory === cat.id
              ? (cat.id === 'all' ? 'bg-slate-700 text-white border-slate-700' : colorMap[cat.color].active)
              : 'bg-white text-slate-600 border-slate-200 hover:border-slate-300 hover:bg-slate-50']">
          <component :is="cat.icon" class="w-3.5 h-3.5" />
          {{ cat.label }}
          <span v-if="cat.id !== 'all'" class="text-[11px] opacity-75">({{ faqs.filter(f => f.category === cat.id).length }})</span>
        </button>
      </div>

      <div class="flex items-center justify-between">
        <p class="text-sm text-slate-500">
          <span v-if="searchQuery || activeCategory !== 'all'">Найдено: <strong class="text-slate-700">{{ filteredFaqs.length }}</strong> из {{ faqs.length }}</span>
          <span v-else>Вопросов: <strong class="text-slate-700">{{ faqs.length }}</strong> · сортировка по частоте</span>
        </p>
        <div class="flex gap-2">
          <button @click="expandAll"   class="text-xs text-slate-500 hover:text-slate-700 underline underline-offset-2">Развернуть все</button>
          <span class="text-slate-300">·</span>
          <button @click="collapseAll" class="text-xs text-slate-500 hover:text-slate-700 underline underline-offset-2">Свернуть все</button>
        </div>
      </div>

      <div v-if="filteredFaqs.length === 0" class="text-center py-16">
        <HelpCircle class="w-12 h-12 text-slate-300 mx-auto mb-3" />
        <p class="text-slate-500 font-semibold">Ничего не найдено</p>
        <button @click="searchQuery = ''; activeCategory = 'all'" class="mt-4 text-sm text-emerald-600 hover:text-emerald-700 font-semibold">Сбросить фильтры</button>
      </div>

      <div class="space-y-3">
        <div v-for="faq in filteredFaqs" :key="faq.id" :id="'faq-item-' + faq.id"
          :class="['bg-white rounded-2xl border overflow-hidden shadow-sm transition-all',
            openItems.has(faq.id) ? colorMap[getCat(faq.category)?.color || 'slate'].border : 'border-slate-200 hover:border-slate-300']">

          <button @click="toggle(faq.id)" class="w-full text-left px-5 py-4 flex items-start gap-4 group">
            <div class="flex flex-col items-center gap-1 flex-shrink-0 pt-0.5">
              <div :class="['w-8 h-8 rounded-xl flex items-center justify-center', colorMap[getCat(faq.category)?.color || 'slate'].bg]">
                <component :is="getCat(faq.category)?.icon || HelpCircle" :class="['w-4 h-4', colorMap[getCat(faq.category)?.color || 'slate'].text]" />
              </div>
              <span :class="['text-[10px] font-bold leading-none', colorMap[getCat(faq.category)?.color || 'slate'].text]">~{{ faq.freq }}/нед</span>
            </div>
            <div class="flex-1 min-w-0">
              <p class="text-sm font-bold text-slate-800 leading-snug group-hover:text-emerald-700 transition-colors">{{ faq.q }}</p>
              <span :class="['inline-block mt-1.5 text-[11px] font-semibold px-2 py-0.5 rounded-full', colorMap[getCat(faq.category)?.color || 'slate'].badge]">{{ getCat(faq.category)?.label }}</span>
            </div>
            <ChevronDown v-if="!openItems.has(faq.id)" class="w-4 h-4 text-slate-400 flex-shrink-0 mt-1" />
            <ChevronUp   v-else                        class="w-4 h-4 text-slate-500 flex-shrink-0 mt-1" />
          </button>

          <div v-if="openItems.has(faq.id)" :class="['border-t', colorMap[getCat(faq.category)?.color || 'slate'].border]">
            <div class="p-5 space-y-4">
              <!-- Шаблон -->
              <div class="rounded-xl border border-blue-200 bg-blue-50 overflow-hidden">
                <div class="flex items-center justify-between px-4 py-2.5 border-b border-blue-200 bg-blue-100/60">
                  <div class="flex items-center gap-2">
                    <MessageCircle class="w-4 h-4 text-blue-600" />
                    <span class="text-xs font-black text-blue-800 uppercase tracking-wide">Шаблонный ответ клиенту</span>
                  </div>
                  <button @click="copyTemplate(faq.template, faq.id + '-t')"
                    :class="['flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-lg transition-all',
                      copiedId === faq.id + '-t' ? 'bg-green-100 text-green-700' : 'bg-white text-blue-600 hover:bg-blue-50 border border-blue-200']">
                    <Check v-if="copiedId === faq.id + '-t'" class="w-3 h-3" />
                    <Copy  v-else class="w-3 h-3" />
                    {{ copiedId === faq.id + '-t' ? 'Скопировано!' : 'Копировать' }}
                  </button>
                </div>
                <p class="px-4 py-3 text-sm text-slate-700 leading-relaxed italic">{{ faq.template }}</p>
              </div>
              <!-- Клиент -->
              <div class="rounded-xl border border-emerald-200 bg-emerald-50 overflow-hidden">
                <div class="flex items-center gap-2 px-4 py-2.5 border-b border-emerald-200 bg-emerald-100/60">
                  <ClipboardList class="w-4 h-4 text-emerald-700" />
                  <span class="text-xs font-black text-emerald-800 uppercase tracking-wide">Алгоритм действий для клиента</span>
                </div>
                <ol class="px-4 py-3 space-y-1.5">
                  <li v-for="(step, i) in faq.clientFlow" :key="i" class="flex items-start gap-2.5 text-sm text-slate-700">
                    <span class="flex-shrink-0 w-5 h-5 rounded-full bg-emerald-200 text-emerald-800 text-[11px] font-black flex items-center justify-center mt-0.5">{{ i + 1 }}</span>
                    <span class="leading-snug">{{ step }}</span>
                  </li>
                </ol>
              </div>
              <!-- Сотрудник -->
              <div class="rounded-xl border border-violet-200 bg-violet-50 overflow-hidden">
                <div class="flex items-center gap-2 px-4 py-2.5 border-b border-violet-200 bg-violet-100/60">
                  <UserCheck class="w-4 h-4 text-violet-700" />
                  <span class="text-xs font-black text-violet-800 uppercase tracking-wide">Порядок реагирования сотрудника</span>
                </div>
                <ol class="px-4 py-3 space-y-1.5">
                  <li v-for="(step, i) in faq.staffFlow" :key="i" class="flex items-start gap-2.5 text-sm text-slate-700">
                    <span class="flex-shrink-0 w-5 h-5 rounded-full bg-violet-200 text-violet-800 text-[11px] font-black flex items-center justify-center mt-0.5">{{ i + 1 }}</span>
                    <span class="leading-snug">{{ step }}</span>
                  </li>
                </ol>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="bg-emerald-50 border border-emerald-200 rounded-2xl p-5">
        <div class="flex items-start gap-3">
          <MessageCircle class="w-5 h-5 text-emerald-600 flex-shrink-0 mt-0.5" />
          <div>
            <p class="text-sm font-bold text-emerald-800">Не нашли нужный вопрос?</p>
            <p class="text-sm text-emerald-700 mt-1">Обратитесь к руководителю или используйте Чат с продавцом на WB. FAQ обновляется по мере накопления новых типовых ситуаций.</p>
          </div>
        </div>
      </div>
    </div>

    <!-- ══ ВКЛАДКА: ПО КАРТОЧКАМ ══════════════════════════════════════ -->
    <div v-else class="max-w-4xl mx-auto px-4 py-6 space-y-4">

      <!-- Загрузка -->
      <div v-if="cardsLoading" class="flex items-center justify-center py-20">
        <Loader2 class="w-8 h-8 text-emerald-500 animate-spin" />
        <span class="ml-3 text-slate-500 font-semibold">Анализируем вопросы по карточкам…</span>
      </div>

      <!-- Ошибка -->
      <div v-else-if="cardsError" class="text-center py-12 text-red-500">{{ cardsError }}</div>

      <template v-else>
        <!-- Поиск + фильтр категории -->
        <div class="flex gap-3">
          <div class="relative flex-1">
            <Search class="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <input v-model="cardSearch" type="text" placeholder="Поиск по названию карточки…"
              class="w-full pl-10 pr-4 py-3 bg-white border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400 shadow-sm" />
          </div>
          <select v-model="cardCategory"
            class="bg-white border border-slate-200 rounded-xl px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-400 shadow-sm">
            <option value="all">Все категории</option>
            <option v-for="cat in cardCategories" :key="cat" :value="cat">{{ cat }}</option>
          </select>
        </div>

        <p class="text-sm text-slate-500">
          Карточек: <strong class="text-slate-700">{{ filteredCards.length }}</strong> ·
          данные за последние 6 месяцев · нажмите карточку для детальной разбивки
        </p>

        <!-- Список карточек -->
        <div class="space-y-2">
          <div v-for="card in filteredCards" :key="card.card"
            class="bg-white rounded-2xl border border-slate-200 overflow-hidden shadow-sm hover:border-slate-300 transition-all">

            <!-- Строка карточки -->
            <div class="px-5 py-3.5 flex items-center gap-3">
              <button @click="toggleCard(card.card)" class="flex-1 min-w-0 text-left group flex items-center gap-3">
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2 mb-1.5">
                    <p class="text-sm font-bold text-slate-800 truncate group-hover:text-emerald-700 transition-colors">{{ card.card }}</p>
                    <span class="flex-shrink-0 text-[11px] text-slate-400 bg-slate-100 rounded px-1.5 py-0.5">{{ card.category }}</span>
                    <span v-if="CARD_INSIGHTS[card.card]" class="flex-shrink-0 text-[10px] font-bold text-amber-600 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5 flex items-center gap-0.5">
                      <AlertTriangle class="w-2.5 h-2.5" />уник.
                    </span>
                  </div>
                  <div class="flex items-center gap-3">
                    <div class="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                      <div class="h-full bg-emerald-400 rounded-full transition-all"
                        :style="{ width: Math.round((card.total / maxTotal) * 100) + '%' }" />
                    </div>
                    <span class="flex-shrink-0 text-sm font-black text-slate-700">{{ card.total }}</span>
                  </div>
                </div>
                <ChevronDown v-if="expandedCard !== card.card" class="w-4 h-4 text-slate-400 flex-shrink-0" />
                <ChevronUp   v-else                           class="w-4 h-4 text-slate-500 flex-shrink-0" />
              </button>
              <!-- WB ссылка -->
              <a v-if="wbCardUrl(card.nm_id)" :href="wbCardUrl(card.nm_id)" target="_blank" rel="noopener"
                class="flex-shrink-0 flex items-center gap-1 text-xs font-semibold text-violet-600 hover:text-violet-800 bg-violet-50 hover:bg-violet-100 border border-violet-200 rounded-lg px-2.5 py-1.5 transition-all"
                title="Открыть карточку на Wildberries" @click.stop>
                <ExternalLink class="w-3 h-3" />
                WB
              </a>
            </div>

            <!-- Детальная разбивка -->
            <div v-if="expandedCard === card.card" class="border-t border-slate-100 px-5 py-4 space-y-4">
              <!-- Темы -->
              <div>
                <p class="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-3">
                  Разбивка по темам (топ-4) · всего {{ card.total }} вопросов за 6 мес.
                </p>
                <div class="space-y-2.5">
                  <div v-for="t in (card.topics || [])" :key="t.topic" class="flex items-center gap-3">
                    <button @click="jumpToFaq(t.topic)"
                      :class="['flex-shrink-0 text-[11px] font-bold px-2.5 py-1 rounded-lg w-36 text-left hover:opacity-80 transition-opacity flex items-center justify-between gap-1',
                        topicColorMap[t.topic]?.bg || 'bg-slate-100',
                        topicColorMap[t.topic]?.text || 'text-slate-700']"
                      :title="faqTopicToRoute[t.topic] ? 'Открыть FAQ по этой теме' : ''">
                      <span class="truncate">{{ t.topic }}</span>
                      <ChevronRight v-if="faqTopicToRoute[t.topic]" class="w-3 h-3 flex-shrink-0" />
                    </button>
                    <div class="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
                      <div :class="['h-full rounded-full transition-all', topicColorMap[t.topic]?.bar || 'bg-slate-400']"
                        :style="{ width: Math.round((t.cnt / card.total) * 100) + '%' }" />
                    </div>
                    <span class="flex-shrink-0 text-xs font-bold text-slate-600 w-8 text-right">{{ t.cnt }}</span>
                    <span class="flex-shrink-0 text-[11px] text-slate-400 w-8">{{ Math.round((t.cnt / card.total) * 100) }}%</span>
                  </div>
                  <p v-if="!card.topics?.length" class="text-sm text-slate-400 italic">Темы не определены</p>
                </div>
              </div>

              <!-- Уникальные инсайты по карточке -->
              <div v-if="CARD_INSIGHTS[card.card]">
                <p class="text-[11px] font-bold text-amber-600 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                  <AlertTriangle class="w-3.5 h-3.5" />
                  Уникальные проблемы этой карточки
                </p>
                <div class="space-y-2">
                  <div v-for="(insight, idx) in CARD_INSIGHTS[card.card]" :key="idx"
                    class="rounded-xl border border-amber-200 bg-amber-50 overflow-hidden">
                    <div class="flex items-center gap-2 px-3 py-2 bg-amber-100/60 border-b border-amber-200">
                      <span class="text-xs font-black text-amber-800">{{ insight.tag }}</span>
                    </div>
                    <p class="px-3 py-2.5 text-xs text-slate-700 leading-relaxed">{{ insight.text }}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>

  </div>
</template>
