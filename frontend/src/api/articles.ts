import http from './http'

// ============ Types ============
export interface Keyword {
  id: number
  user_id: number
  keyword: string
  target_url?: string
  search_engine: string
  region: string
  priority: number
  status: string
  created_at: string
}

export interface KeywordCreate {
  keyword: string
  target_url?: string
  search_engine?: string
  region?: string
  priority?: number
}

export interface Article {
  id: number
  user_id: number
  keyword_id?: number | null
  title: string
  meta_description?: string
  slug?: string
  content?: string
  cover_image_url?: string
  status: 'draft' | 'ai_generated' | 'reviewed' | 'published'
  seo_score?: number | null
  ai_detected_score?: number | null
  seo_detail?: any
  target_url?: string
  source?: 'internal' | 'external'
  source_url?: string
  sync_status?: string
  cms_type?: string
  published_at?: string | null
  created_at: string
  updated_at: string
}

export interface ArticleCreate {
  title: string
  meta_description?: string
  slug?: string
  content?: string
  cover_image_url?: string
  target_url?: string
  keyword_id?: number
}

export interface ArticleUpdate {
  title?: string
  meta_description?: string
  slug?: string
  content?: string
  cover_image_url?: string
  target_url?: string
  keyword_id?: number
}

// ============ Keywords API ============
export function listKeywords() {
  return http.get<any, Keyword[]>('/keywords')
}

export function createKeyword(payload: KeywordCreate) {
  return http.post<any, Keyword>('/keywords', payload)
}

export function deleteKeyword(id: number) {
  return http.delete(`/keywords/${id}`)
}

// ============ Articles API ============
export function listArticles(params?: { status?: string; keyword_id?: number; page?: number; size?: number }) {
  return http.get<any, Article[]>('/articles', { params })
}

export function getArticle(id: number) {
  return http.get<any, Article>(`/articles/${id}`)
}

export function createArticle(payload: ArticleCreate) {
  return http.post<any, Article>('/articles', payload)
}

export function updateArticle(id: number, payload: ArticleUpdate) {
  return http.patch<any, Article>(`/articles/${id}`, payload)
}

export function changeArticleStatus(id: number, status: string) {
  return http.post<any, Article>(`/articles/${id}/status`, { status })
}

export function deleteArticle(id: number) {
  return http.delete(`/articles/${id}`)
}

export function syncArticleToCms(id: number) {
  return http.post<any, Article>(`/articles/${id}/sync-cms`)
}

export function importArticleFromUrl(url: string) {
  return http.post<any, Article>('/articles/import-url', null, { params: { url } })
}
