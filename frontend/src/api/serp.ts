import http from './http'

export type CrawlStatus = 'success' | 'blocked' | 'timeout' | 'error'

export interface SerpRankSnapshot {
  time: string
  keyword_id: number
  target_url?: string | null
  rank?: number | null
  page?: number | null
  serp_features?: Record<string, any> | null
  search_region: string
  proxy_used?: string | null
  crawl_status: CrawlStatus
  error_message?: string | null
  keyword?: string | null
}

export interface LatestRank {
  keyword_id: number
  keyword: string
  target_url?: string | null
  rank?: number | null
  crawl_status: string
  last_crawled?: string | null
}

export interface TrendPoint {
  time: string
  rank: number | null
  crawl_status: string
}

export type TrendsData = Record<number, TrendPoint[]>

export type SerpMode = 'mock' | 'scrapingbee' | 'proxy'

export interface SerpConfig {
  mode: SerpMode
}

export function getSerpConfig() {
  return http.get<any, SerpConfig>('/serp/config')
}

export function listSnapshots(params?: { keyword_id?: number; crawl_status?: string; page?: number; size?: number }) {
  return http.get<any, SerpRankSnapshot[]>('/serp/snapshots', { params })
}

export function getTrends(keyword_ids: number[], days = 30) {
  return http.get<any, TrendsData>('/serp/trends', {
    params: { keyword_ids: keyword_ids.join(','), days },
  })
}

export function getLatestRanks() {
  return http.get<any, LatestRank[]>('/serp/latest')
}

export function triggerCrawl(keyword_id: number) {
  return http.post<any, { task_id: string; keyword: string }>(`/serp/crawl/${keyword_id}`)
}
