import { createRouter, createWebHistory } from 'vue-router'

// Импортируем наши готовые страницы
import SynchronizerView from '../views/SynchronizerView.vue'
import AITaggingView from '../views/AITaggingView.vue'
import ModerationView from '../views/ModerationView.vue'
import AITrainingView from '../views/AITrainingView.vue'
import SystemLogsView from '../views/SystemLogsView.vue'
import ProductionView from '../views/ProductionView.vue'
import PPMView from '../views/PPMView.vue' // <-- 1. Добавили импорт страницы PPM

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'sync', component: SynchronizerView },
    { path: '/ai-tagging', name: 'tagging', component: AITaggingView },
    { path: '/moderation', name: 'moderation', component: ModerationView },
    { path: '/ai-training', name: 'training', component: AITrainingView },
    { path: '/logs', name: 'logs', component: SystemLogsView },
    { path: '/production', name: 'production', component: ProductionView },
    
    // 2. Привязали компонент к пути
    { path: '/ppm', name: 'ppm', component: PPMView },
    
    // Блок Рейтингов пока остается заглушкой
    { path: '/ratings', name: 'ratings', component: () => import('../views/PlaceholderView.vue') }
  ]
})

export default router