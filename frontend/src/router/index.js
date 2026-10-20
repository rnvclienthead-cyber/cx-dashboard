import { createRouter, createWebHistory } from 'vue-router'
import { usePermissionsStore } from '../stores/permissions'
import { getAuthToken, getAuthRole, rememberAfterLogin, markSessionUse } from '../authStorage'

// Порядок совпадает с порядком в сайдбаре — первый доступный модуль станет стартовой страницей
export const MODULE_ROUTES = [
  { module: 'dashboard',   path: '/' },
  { module: 'sync',        path: '/sync' },
  { module: 'ai_tagging',  path: '/ai-tagging' },
  { module: 'moderation',  path: '/moderation' },
  { module: 'ai_training', path: '/ai-training' },
  { module: 'logs',        path: '/logs' },
  { module: 'production',  path: '/production' },
  { module: 'ppm',         path: '/ppm' },
  { module: 'ppm',         path: '/registry' },
  { module: 'voc',         path: '/voc' },
  { module: 'cx_control',  path: '/cx-control' },
  { module: 'ratings',     path: '/ratings' },
  { module: 'finances',    path: '/finances' },
  { module: 'reshipment',  path: '/reshipment' },
  { module: 'warehouse_intake', path: '/intake' },
  { module: 'shop_orders', path: '/shop-orders' },
  { module: 'pnl',         path: '/pnl' },
  { module: 'faq',         path: '/faq' },
  { module: 'docs',        path: '/docs' },
  { module: 'cx_responses', path: '/reviews' },
  { module: 'cx_responses', path: '/questions' },
  { module: 'cx_responses', path: '/chats' },
  { module: 'tshopping', path: '/tshopping' },
  { module: 'cx_responses', path: '/returns' },
  { module: 'quality_questions', path: '/questions/quality' },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => window.location.hostname.includes('vidovito.com')
        ? import('../views/ReshipmentFormView.vue')
        : import('../views/ExecutiveDashboardView.vue'),
      meta: window.location.hostname.includes('vidovito.com') ? { public: true } : { requiresAuth: true },
    },
    { path: '/sync',                     name: 'sync',              component: () => import('../views/SynchronizerView.vue'),       meta: { requiresAuth: true } },
    { path: '/ai-tagging',               name: 'tagging',           component: () => import('../views/AITaggingView.vue'),          meta: { requiresAuth: true } },
    { path: '/moderation',               name: 'moderation',        component: () => import('../views/ModerationView.vue'),         meta: { requiresAuth: true } },
    { path: '/ai-training',              name: 'training',          component: () => import('../views/AITrainingView.vue'),         meta: { requiresAuth: true } },
    { path: '/logs',                     name: 'logs',              component: () => import('../views/SystemLogsView.vue'),         meta: { requiresAuth: true } },
    { path: '/production',               name: 'production',        component: () => import('../views/ProductionView.vue'),         meta: { requiresAuth: true } },
    { path: '/ratings',                  name: 'ratings',           component: () => import('../views/RatingsView.vue'),            meta: { requiresAuth: true } },
    { path: '/ppm',                      name: 'ppm',               component: () => import('../views/PPMView.vue'),                meta: { requiresAuth: true } },
    { path: '/login',                    name: 'login',             component: () => import('../views/LoginView.vue') },
    {
      path: '/admin',
      name: 'admin',
      meta: { public: true },
      beforeEnter() {
        window.location.replace('https://hub.cxvo.ru')
        return false
      },
      component: { render: () => null },
    },
    { path: '/finances',                 name: 'finances',          component: () => import('../views/FinancialLossView.vue'),      meta: { requiresAuth: true } },
    { path: '/voc',                      name: 'voc',               component: () => import('../views/VocView.vue'),                meta: { requiresAuth: true } },
    { path: '/cx-control',               name: 'cx-control',         component: () => import('../views/CxControlView.vue'),          meta: { requiresAuth: true } },
    { path: '/registry',                 name: 'registry',          component: () => import('../views/RegistryView.vue'),           meta: { requiresAuth: true } },
    { path: '/reshipment',               name: 'reshipment',        component: () => import('../views/ReshipmentView.vue'),         meta: { requiresAuth: true } },
    { path: '/intake',                   name: 'intake',            component: () => import('../views/IntakeView.vue'),             meta: { requiresAuth: true, adminOnly: true } },
    { path: '/shop-orders',              name: 'shop-orders',       component: () => import('../views/ShopOrdersView.vue'),         meta: { requiresAuth: true } },
    { path: '/reshipment/form',          name: 'reshipment-form',   component: () => import('../views/ReshipmentFormView.vue'),     meta: { public: true } },
    { path: '/reshipment/confirm/:token',name: 'reshipment-confirm',component: () => import('../views/ReshipmentConfirmView.vue'),  meta: { public: true } },
    { path: '/reset-password',           name: 'reset-password',    component: () => import('../views/ResetPasswordView.vue'),      meta: { public: true } },
    { path: '/docs',                      name: 'docs',              component: () => import('../views/DocsView.vue'),               meta: { requiresAuth: true } },
    { path: '/pnl',                       name: 'pnl',               component: () => import('../views/PnLView.vue'),                meta: { requiresAuth: true } },
    { path: '/faq',                       name: 'faq',               component: () => import('../views/FAQView.vue'),                meta: { requiresAuth: true } },
    { path: '/reviews',                  name: 'reviews',           component: () => import('../views/ReviewsView.vue'),            meta: { requiresAuth: true } },
    { path: '/questions',                name: 'questions',         component: () => import('../views/QuestionsView.vue'),          meta: { requiresAuth: true } },
    { path: '/questions/quality',        name: 'quality-questions', component: () => import('../views/QuestionsView.vue'),          meta: { requiresAuth: true } },
    { path: '/chats',                    name: 'chats',             component: () => import('../views/ChatsView.vue'),              meta: { requiresAuth: true } },
    { path: '/tshopping',                name: 'tshopping',         component: () => import('../views/TShoppingView.vue'),          meta: { requiresAuth: true } },
    { path: '/returns',                  name: 'returns',           component: () => import('../views/ReturnsView.vue'),            meta: { requiresAuth: true } },
    { path: '/return/scan',              name: 'return-scan',       component: () => import('../views/ReturnScanView.vue'),         meta: { public: true } },
    { path: '/return/batch/:incomeId',   name: 'return-batch',      component: () => import('../views/ReturnBatchView.vue'),        meta: { public: true } },
    { path: '/return/:srid',             name: 'return-card',       component: () => import('../views/ReturnCardView.vue'),         meta: { public: true } },
  ]
})

router.beforeEach(async (to, from, next) => {
  const token = getAuthToken()
  if (token && to.path !== '/login') markSessionUse()

  if (!token) {
    if (to.meta.requiresAuth) {
      rememberAfterLogin(to.fullPath)
      return next('/login')
    }
    return next()
  }

  if (getAuthRole() === 'tshopping_partner' && (to.path === '/' || to.path === '/login')) {
    if (to.path === '/') return next('/tshopping')
  }

  const permStore = usePermissionsStore()
  // Сканер и другие открытые страницы не ждут права — на телефоне это может висеть долго.
  if (to.meta.public) {
    if (!permStore.loaded) permStore.load()
    return next()
  }

  // Грузим права один раз — до первого рендера защищённой страницы
  if (!permStore.loaded) {
    await permStore.load()
  }

  if (to.meta.requiresAuth) {
    // Проверяем, есть ли у пользователя доступ к запрошенному модулю
    const moduleRoute = MODULE_ROUTES.find(r => r.path === to.path)
    // Старая ссылка /questions у сотрудника качества ведёт в его очередь.
    // У пользователей с обоими правами этот маршрут остаётся рабочим столом КС.
    if (to.path === '/questions' && !permStore.can('cx_responses') && permStore.can('quality_questions')) {
      return next('/questions/quality')
    }
    const qualityRoleFallback =
      to.path === '/questions/quality' && permStore.role === 'qa_manager'
    if (to.meta.adminOnly && !permStore.isAdmin) {
      const first = MODULE_ROUTES.find(r => permStore.can(r.module))
      if (first) return next(first.path)
      return next('/login')
    }
    if (moduleRoute && !permStore.can(moduleRoute.module) && !qualityRoleFallback) {
      // Ищем первый доступный модуль для редиректа
      const first = MODULE_ROUTES.find(r => permStore.can(r.module))
      if (first) return next(first.path)
    }
    return next()
  }
  next()
})

export default router
