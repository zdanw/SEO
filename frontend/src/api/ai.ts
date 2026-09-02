import http from './http'

export type AIModelProvider = 'deepseek' | 'agnes'

export interface AIArticleRequest {
  keyword: string
  outline?: string
  audience?: string
  tone?: string
  model?: AIModelProvider
}

export interface AIArticleResponse {
  title: string
  meta_description: string
  content: string
  keywords_suggested: string[]
}

export interface AISocialCopyRequest {
  platform: 'LinkedIn' | 'Twitter/X' | 'Facebook' | 'PulseForge' | 'Reddit'
  article_title: string
  keyword: string
  summary: string
  model?: AIModelProvider
}

export interface AISocialCopyResponse {
  platform: string
  title: string
  summary: string
  hashtags: string[]
}

export function generateArticle(payload: AIArticleRequest) {
  return http.post<any, AIArticleResponse>('/ai/generate', payload)
}

export function generateAndSaveArticle(payload: AIArticleRequest) {
  return http.post<any, any>('/ai/generate-and-save', payload)
}

export function regenerateMeta(keyword: string, content: string, model?: AIModelProvider) {
  return http.post<any, { meta_description: string }>('/ai/meta', { keyword, content, model })
}

export function generateSocialCopy(payload: AISocialCopyRequest) {
  return http.post<any, AISocialCopyResponse>('/ai/social-copy', payload)
}
