<template>
  <el-container class="default-layout">
    <el-aside :width="collapsed ? '64px' : '240px'" class="aside">
      <div class="logo">
        <el-icon :size="22"><TrendCharts /></el-icon>
        <span v-show="!collapsed" class="logo-text">SEO Platform</span>
      </div>
      <el-scrollbar class="menu-scroll">
        <el-menu
          :default-active="activeMenu"
          :collapse="collapsed"
          router
          class="side-menu"
          background-color="#001529"
          text-color="#cdd9e5"
          active-text-color="#409eff"
        >
          <el-menu-item-group v-for="group in menuGroups" :key="group.title">
            <template #title>
              <span v-show="!collapsed">{{ group.title }}</span>
            </template>
            <el-menu-item
              v-for="item in group.items"
              :key="item.path"
              :index="item.path"
            >
              <el-icon><component :is="item.icon" /></el-icon>
              <template #title>{{ item.title }}</template>
            </el-menu-item>
          </el-menu-item-group>
        </el-menu>
      </el-scrollbar>
    </el-aside>

    <el-container class="main-shell">
      <el-header class="header">
        <div class="header-left">
          <el-button class="collapse-btn" text @click="collapsed = !collapsed">
            <el-icon :size="18">
              <Expand v-if="collapsed" />
              <Fold v-else />
            </el-icon>
          </el-button>
          <div class="page-heading">
            <h1 class="heading-title">{{ currentTitle || 'SEO Platform' }}</h1>
            <p v-if="currentSubtitle" class="heading-subtitle">{{ currentSubtitle }}</p>
          </div>
        </div>
        <div class="user-info">
          <el-avatar :size="32" :icon="UserFilled" />
          <span class="user-email">{{ email }}</span>
          <el-button type="danger" link size="small" @click="logout">退出</el-button>
        </div>
      </el-header>
      <el-main class="main-content">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  DataBoard, EditPen, Check, Share, TrendCharts,
  Document, Link, Compass, Bell, UserFilled, Fold, Expand, Monitor,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

const route = useRoute()
const router = useRouter()
const collapsed = ref(false)

const menuGroups = [
  {
    title: '工作台',
    items: [
      { path: '/dashboard', title: '综合大屏', icon: DataBoard },
      { path: '/recommendations', title: '优化建议', icon: Bell },
    ],
  },
  {
    title: '内容运营',
    items: [
      { path: '/articles', title: '文章创作', icon: EditPen },
      { path: '/seo-checker', title: 'SEO 检查', icon: Check },
      { path: '/social', title: '社交分发', icon: Share },
    ],
  },
  {
    title: '数据监控',
    items: [
      { path: '/serp', title: '排名监控', icon: TrendCharts },
      { path: '/search-console', title: 'Search Console', icon: Monitor },
      { path: '/backlinks', title: '外链监控', icon: Link },
      { path: '/competitors', title: '竞品对标', icon: Compass },
    ],
  },
]

const pageSubtitles: Record<string, string> = {
  '/dashboard': '关键词、排名、社交与外链核心指标一览',
  '/recommendations': '策略引擎生成的优化工单',
  '/articles': 'AI 辅助写作与文章状态管理',
  '/seo-checker': 'Title、Meta、结构与健康度评分',
  '/social': 'PulseForge 账号连接与发帖调度',
  '/serp': 'Google 排名趋势与抓取快照',
  '/search-console': '客户网站在 Google 搜索中的真实表现数据',
  '/backlinks': '外链新增、丢失与存活监控',
  '/competitors': '竞品域名排名对比',
}

const activeMenu = computed(() => {
  const path = route.path
  if (path.startsWith('/articles')) return '/articles'
  return path
})

const currentTitle = computed(() => (route.meta.title as string) || '')
const currentSubtitle = computed(() => pageSubtitles[activeMenu.value] || '')
const email = computed(() => localStorage.getItem('user_email') || 'admin@example.com')

function logout() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('user_email')
  ElMessage.success('已退出登录')
  router.replace('/login')
}
</script>

<style lang="scss" scoped>
.default-layout {
  height: 100vh;
  overflow: hidden;
}

.aside {
  background-color: #001529;
  color: #fff;
  display: flex;
  flex-direction: column;
  transition: width 0.25s ease;
  overflow: hidden;

  .logo {
    height: 56px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    font-size: 15px;
    font-weight: 700;
    color: #fff;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    flex-shrink: 0;

    .logo-text {
      white-space: nowrap;
    }
  }

  .menu-scroll {
    flex: 1;
    overflow: hidden;
  }

  .side-menu {
    border-right: none;

    :deep(.el-menu-item-group__title) {
      padding: 12px 20px 6px;
      color: rgba(255, 255, 255, 0.35);
      font-size: 12px;
      letter-spacing: 0.5px;
    }

    :deep(.el-menu-item) {
      margin: 2px 8px;
      border-radius: 6px;
      height: 44px;
    }

    :deep(.el-menu-item.is-active) {
      background-color: rgba(64, 158, 255, 0.18) !important;
    }
  }
}

.main-shell {
  min-width: 0;
}

.header {
  height: 56px;
  background-color: #fff;
  border-bottom: 1px solid #e8eaed;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px 0 12px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  z-index: 10;

  .header-left {
    display: flex;
    align-items: center;
    gap: 4px;
    min-width: 0;
  }

  .collapse-btn {
    padding: 8px;
    color: #606266;
  }

  .page-heading {
    min-width: 0;

    .heading-title {
      margin: 0;
      font-size: 18px;
      font-weight: 600;
      color: #1f2d3d;
      line-height: 1.3;
    }

    .heading-subtitle {
      margin: 2px 0 0;
      font-size: 12px;
      color: #909399;
      line-height: 1.3;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
  }

    .user-info {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
    font-size: 14px;

    .user-email {
      max-width: 180px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: #606266;
    }
  }
}

.main-content {
  padding: 0;
  background-color: #f0f2f5;
  overflow-y: auto;
  overflow-x: hidden;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 768px) {
  .header .heading-subtitle {
    display: none;
  }

  .header .user-email {
    display: none;
  }
}
</style>
