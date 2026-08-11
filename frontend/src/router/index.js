import { createRouter, createWebHistory } from 'vue-router'
import { usePermissionsStore } from '../stores/permissions'

import SynchronizerView from '../views/SynchronizerView.vue'
import AITaggingView from '../views/AITaggingView.vue'
import ModerationView from '../views/ModerationView.vue'
import AITrainingView from '../views/AITrainingView.vue'
import SystemLogsView from '../views/SystemLogsView.vue'
import ProductionView from '../views/ProductionView.vue'
import PPMView from '../views/PPMView.vue'
import RatingsView from '../views/RatingsView.vue'
import LoginView from '../views/LoginView.vue'
import AdminView from '../views/AdminView.vue'
import FinancialLossView from '../views/FinancialLossView.vue'
import VocView from '../views/VocView.vue'
import CxControlView from '../views/CxControlView.vue'
import ExecutiveDashboardView from '../views/ExecutiveDashboardView.vue'
import RegistryView from '../views/RegistryView.vue'
import ReshipmentView from '../views/ReshipmentView.vue'
import ReshipmentFormView from '../views/ReshipmentFormView.vue'
import ReshipmentConfirmView from '../views/ReshipmentConfirmView.vue'
import ResetPasswordView from '../views/ResetPasswordView.vue'
import DocsView from '../views/DocsView.vue'
import FAQView from '../views/FAQView.vue'
import ReturnScanView from '../views/ReturnScanView.vue'
import ReturnCardView from '../views/ReturnCardView.vue'
import ReturnBatchView from '../views/ReturnBatchView.vue'
import PnLView from '../views/PnLView.vue'
import ReviewsView from '../views/ReviewsView.vue'
import QuestionsView from '../views/QuestionsView.vue'
import ChatsView from '../views/ChatsView.vue'
import ReturnsView from '../views/ReturnsView.vue'

// Порядок совпадает с порядком в сайдбаре — первый доступный модуль станет стартовой страницей
export const MODULE_ROUTES = [
  { module: 'dashboard',   path: '/' },
  { module: 'admin_panel', path: '/admin' },
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
  { module: 'pnl',         path: '/pnl' },
  { module: 'faq',         path: '/faq' },
  { module: 'docs',        path: '/docs' },
  { module: 'cx_responses', path: '/reviews' },
  { module: 'cx_responses', path: '/questions' },
  { module: 'cx_responses', path: '/chats' },
  { module: 'cx_responses', path: '/returns' },
  { module: 'quality_questions', path: '/questions/quality' },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: window.location.hostname.includes('vidovito.com') ? ReshipmentFormView : ExecutiveDashboardView,
      meta: window.location.hostname.includes('vidovito.com') ? { public: true } : { requiresAuth: true },
    },
    { path: '/sync',                     name: 'sync',              component: SynchronizerView,       meta: { requiresAuth: true } },
    { path: '/ai-tagging',               name: 'tagging',           component: AITaggingView,          meta: { requiresAuth: true } },
    { path: '/moderation',               name: 'moderation',        component: ModerationView,         meta: { requiresAuth: true } },
    { path: '/ai-training',              name: 'training',          component: AITrainingView,         meta: { requiresAuth: true } },
    { path: '/logs',                     name: 'logs',              component: SystemLogsView,         meta: { requiresAuth: true } },
    { path: '/production',               name: 'production',        component: ProductionView,         meta: { requiresAuth: true } },
    { path: '/ratings',                  name: 'ratings',           component: RatingsView,            meta: { requiresAuth: true } },
    { path: '/ppm',                      name: 'ppm',               component: PPMView,                meta: { requiresAuth: true } },
    { path: '/login',                    name: 'login',             component: LoginView },
    { path: '/admin',                    name: 'admin',             component: AdminView,              meta: { requiresAuth: true } },
    { path: '/finances',                 name: 'finances',          component: FinancialLossView,      meta: { requiresAuth: true } },
    { path: '/voc',                      name: 'voc',               component: VocView,                meta: { requiresAuth: true } },
    { path: '/cx-control',               name: 'cx-control',         component: CxControlView,          meta: { requiresAuth: true } },
    { path: '/registry',                 name: 'registry',          component: RegistryView,           meta: { requiresAuth: true } },
    { path: '/reshipment',               name: 'reshipment',        component: ReshipmentView,         meta: { requiresAuth: true } },
    { path: '/reshipment/form',          name: 'reshipment-form',   component: ReshipmentFormView,     meta: { public: true } },
    { path: '/reshipment/confirm/:token',name: 'reshipment-confirm',component: ReshipmentConfirmView,  meta: { public: true } },
    { path: '/reset-password',           name: 'reset-password',    component: ResetPasswordView,      meta: { public: true } },
    { path: '/docs',                      name: 'docs',              component: DocsView,               meta: { requiresAuth: true } },
    { path: '/pnl',                       name: 'pnl',               component: PnLView,                meta: { requiresAuth: true } },
    { path: '/faq',                       name: 'faq',               component: FAQView,                meta: { requiresAuth: true } },
    { path: '/reviews',                  name: 'reviews',           component: ReviewsView,            meta: { requiresAuth: true } },
    { path: '/questions',                name: 'questions',         component: QuestionsView,          meta: { requiresAuth: true } },
    { path: '/questions/quality',        name: 'quality-questions', component: QuestionsView,          meta: { requiresAuth: true } },
    { path: '/chats',                    name: 'chats',             component: ChatsView,              meta: { requiresAuth: true } },
    { path: '/returns',                  name: 'returns',           component: ReturnsView,            meta: { requiresAuth: true } },
    { path: '/return/scan',              name: 'return-scan',       component: ReturnScanView,         meta: { public: true } },
    { path: '/return/batch/:incomeId',   name: 'return-batch',      component: ReturnBatchView,        meta: { public: true } },
    { path: '/return/:srid',             name: 'return-card',       component: ReturnCardView,         meta: { public: true } },
  ]
})

router.beforeEach(async (to, from, next) => {
  const token = localStorage.getItem('token')

  if (!token) {
    if (to.meta.requiresAuth) return next('/login')
    return next()
  }

  // Грузим права один раз — до первого рендера защищённой страницы
  const permStore = usePermissionsStore()
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
    if (moduleRoute && !permStore.can(moduleRoute.module)) {
      // Ищем первый доступный модуль для редиректа
      const first = MODULE_ROUTES.find(r => permStore.can(r.module))
      if (first) return next(first.path)
    }
    return next()
  }
  next()
})

export default router
