import http from './http'

// ============ Types ============
export interface DashboardCard {
  label: string
  value: number | string
  trend: number | null
  color: string
}

export interface DashboardSummary {
  period_days: number
  cards: DashboardCard[]
}

export interface TrendPoint {
  time: string
  rank: number | null
}

export type RankTrendsData = Record<number, TrendPoint[]>

export interface SocialFunnelItem {
  platform: string
  clicks: number
}

export interface SocialFunnel {
  days: number
  articles_published: number
  total_posts: number
  posted_posts: number
  total_clicks: number
  by_platform: SocialFunnelItem[]
}

export interface BacklinkStats {
  period_days: number
  total_backlinks: number
  alive_backlinks: number
  new_this_period: number
  lost_this_period: number
  net_change: number
  loss_rate: number
  alive_rate: number
}

export interface Recommendation {
  id: number
  user_id: number | null
  article_id: number | null
  keyword_id: number | null
  category: string
  severity: 'info' | 'warning' | 'critical'
  title: string
  description?: string | null
  suggestion?: string | null
  status: 'open' | 'in_progress' | 'resolved' | 'ignored'
  created_at: string | null
  resolved_at: string | null
}

export interface RecommendationsList {
  total: number
  page: number
  size: number
  items: Recommendation[]
}

// ============ Dashboard Summary ============
export function getDashboardSummary(days = 7) {
  return http.get<any, DashboardSummary>('/dashboard/summary', { params: { days } })
}

// ============ Rank Trends ============
export function getRankTrends(keyword_ids?: number[], days = 14) {
  const params: Record<string, any> = { days }
  if (keyword_ids && keyword_ids.length > 0) {
    params.keyword_ids = keyword_ids.join(',')
  }
  return http.get<any, RankTrendsData>('/dashboard/rank-trends', { params })
}

// ============ Social Funnel ============
export function getSocialFunnel(days = 7) {
  return http.get<any, SocialFunnel>('/dashboard/social-funnel', { params: { days } })
}

// ============ Backlink Stats ============
export function getBacklinkStats(days = 30) {
  return http.get<any, BacklinkStats>('/dashboard/backlink-stats', { params: { days } })
}

// ============ Recommendations ============
export function listRecommendations(params?: {
  category?: string
  severity?: string
  status?: string
  page?: number
  size?: number
}) {
  return http.get<any, RecommendationsList>('/dashboard/recommendations', { params })
}

export function generateRecommendations(days_lookback = 7) {
  return http.post<any, { created: number; ids: number[] }>(
    '/dashboard/recommendations/generate',
    {},
    { params: { days_lookback } },
  )
}

export function updateRecommendation(id: number, payload: { status: string }) {
  return http.patch<any, { id: number; status: string; resolved_at: string | null }>(
    `/dashboard/recommendations/${id}`,
    payload,
  )
}

export function deleteRecommendation(id: number) {
  return http.delete(`/dashboard/recommendations/${id}`)
}
