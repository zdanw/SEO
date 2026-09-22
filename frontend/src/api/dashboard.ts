import http from './http'

export interface DashboardCard {
  label: string
  value: number | string
  trend: number | null
  color: string
}

export interface DashboardSummary {
  period_days: number
  cards: DashboardCard[]
  sample?: {
    rank_scope: string
    top10_definition: string
    time_range: string
    disclaimer: string
  }
}

export interface TrendPoint {
  time: string
  rank: number | null
}

export type RankTrendsData = Record<number, TrendPoint[]>

export function getDashboardSummary(days = 7) {
  return http.get<any, DashboardSummary>('/dashboard/summary', { params: { days } })
}

export function getRankTrends(keyword_ids?: number[], days = 14) {
  const params: Record<string, any> = { days }
  if (keyword_ids && keyword_ids.length > 0) {
    params.keyword_ids = keyword_ids.join(',')
  }
  return http.get<any, RankTrendsData>('/dashboard/rank-trends', { params })
}
