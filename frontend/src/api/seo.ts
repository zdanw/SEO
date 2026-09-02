import http from './http'

export interface SeoCheckItem {
  key: string
  name: string
  score: number
  max_score: number
  status: 'pass' | 'warning' | 'fail'
  message: string
  suggestion: string
}

export interface AiDetectDetail {
  score: number
  verdict: string
  engine: string
  message: string
  lmscan?: {
    score: number
    verdict?: string
    confidence?: string
    model_attribution?: Array<{ model: string; confidence: number; evidence: string[] }>
    high_risk_sentences?: Array<{ text: string; score: number }>
  }
  signs_of_ai?: {
    score: number
    verdict?: string
    patterns_matched?: number
    patterns?: Array<{
      key: string
      name: string
      severity: string
      count: number
      matches: string[]
      suggestion: string
    }>
    suggestions?: string[]
  }
}

export interface SerpDetail {
  overall_score: number
  data_source?: 'scrapingbee' | 'simulated'
  top_competitors?: Array<{
    position: number
    title: string
    domain: string
    word_count: number
    heading_count: number
    url: string
  }>
  skipped_competitors?: Array<{
    position: number
    title: string
    url: string
    domain: string
    reason: string
  }>
  categories: Record<string, { score: number; max_score: number; details: string; key?: string }>
  recommendations: string[]
  serp_benchmark?: {
    avg_word_count: number
    recommended_word_count: number
    avg_headings: number
    pages_analyzed?: number
    organic_count?: number
    serp_features?: Record<string, boolean | number | string>
  }
  missing_topics?: string[]
  covered_topics?: string[]
  coverage_percentage?: number
  engine?: string
}

export interface TechnicalAudit {
  engine: string
  url: string
  score: number
  grade?: string
  title?: string
  categories: Array<{ key: string; name: string; score: number; weight?: number; weighted?: number }>
  top_issues?: string[]
}

export interface SeoCheckResult {
  total_score: number
  items: SeoCheckItem[]
  ai_detected_score: number
  ai_detail?: AiDetectDetail
  serp_detail?: SerpDetail
  technical_audit?: TechnicalAudit
  cwv_estimate: {
    source?: 'psi' | 'estimated' | 'none'
    data_type?: 'field' | 'field_origin' | 'lab' | 'estimated'
    strategy?: string
    performance_score?: number
    cwv_passed?: boolean
    lcp_ms: number
    lcp_risk: string
    cls: number
    cls_risk: string
    inp_ms: number
    inp_risk: string
    image_count: number
    cover_image: boolean
    note: string
  }
  internal_links_recommended?: any[]
}

export interface SeoCheckRequest {
  title: string
  content: string
  meta_description?: string
  keyword?: string
  cover_image_url?: string
}

export function checkSeo(payload: SeoCheckRequest) {
  return http.post<any, SeoCheckResult>('/seo/check', payload)
}

export function checkSeoAndSave(articleId: number, keyword?: string) {
  return http.post<any, SeoCheckResult>(`/seo/check/${articleId}`, { keyword }, {
    headers: { 'Content-Type': 'application/json' },
  })
}

export function recommendInternalLinks(articleId: number, limit?: number) {
  return http.get<any, any[]>(`/seo/internal-links/${articleId}`, { params: { limit } })
}

export function checkUrlSeo(url: string, keyword?: string) {
  return http.post<any, SeoCheckResult>('/seo/check-url', { url, keyword }, { timeout: 180000 })
}

export function scoreSerpContent(content: string, keyword: string, targetWordCount?: number) {
  return http.post<any, SerpDetail>('/seo/serp-score', {
    content,
    keyword,
    target_word_count: targetWordCount,
  })
}

export interface SeoAudit {
  id: number
  site_id: number
  status: string
  total_pages: number
  scanned_pages: number
  avg_score?: number
  summary?: string
  created_at: string
  completed_at?: string
  pages: Array<{
    id: number
    url: string
    title?: string
    score?: number
    issues_count: number
  }>
}

export function createSeoAudit(maxPages = 50) {
  return http.post<any, SeoAudit>('/seo/audits', { max_pages: maxPages })
}

export function listSeoAudits() {
  return http.get<any, SeoAudit[]>('/seo/audits')
}

export function getSeoAudit(id: number) {
  return http.get<any, SeoAudit>(`/seo/audits/${id}`)
}
