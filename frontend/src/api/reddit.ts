import http from './http'

export type PostType = 'pitfall' | 'vent' | 'unpopular' | 'guide' | 'help_seek'
export type ReviewStatus =
  | 'draft' | 'pending_review' | 'approved' | 'rejected' | 'posting' | 'posted' | 'failed'
export type AccountRole = 'warmup' | 'seeding' | 'expert'
export type AccountStage = 'warmup_week1_2' | 'warmup_week3_4' | 'ready' | 'active' | 'warning' | 'suspended'
export type CommunityCategory = 'core' | 'longtail'
export type CommunityPurpose = 'persona' | 'promo'
export type ContentIntent = 'casual' | 'promo'

export interface PersonaConfig {
  voice?: string
  interests?: string[]
  quirks?: string
  never_say?: string
}

export interface RedditAccount {
  id: number
  account_name: string
  is_active: boolean
  zernio_key_id?: number | null
  zernio_key_label?: string | null
  role?: AccountRole | null
  stage?: AccountStage | null
  karma: number
  persona?: string | null
  persona_config?: PersonaConfig | null
  daily_post_limit: number
  daily_comment_limit: number
  risk_status: string
  risk_reason?: string | null
  posts_today: number
  comments_today: number
}

export interface RedditStatus {
  configured: boolean
  connected: boolean
  accounts: RedditAccount[]
  zernio_key_count?: number
  sync_errors?: string[]
}

export interface ZernioKey {
  id: number
  label: string
  api_key_masked: string
  profile_id?: string | null
  is_enabled: boolean
  created_at: string
}

export interface RedditPost {
  id: number
  site_id: number
  account_id: number
  post_type: PostType
  subreddit: string
  keyword: string
  title: string
  body: string
  site_url?: string | null
  status: ReviewStatus
  reddit_post_id?: string | null
  reddit_permalink?: string | null
  scheduled_at?: string | null
  published_at?: string | null
  error_message?: string | null
  content_intent?: ContentIntent
  account_name?: string | null
  created_at: string
  updated_at: string
}

export interface RedditComment {
  id: number
  site_id: number
  account_id: number
  target_post_url: string
  target_thing_id: string
  subreddit: string
  target_post_title?: string | null
  body: string
  keyword?: string | null
  discover_source: string
  content_intent?: ContentIntent
  ai_risk?: string | null
  brand_id?: number | null
  product_id?: number | null
  brand_name?: string | null
  product_name?: string | null
  status: ReviewStatus
  reddit_comment_id?: string | null
  scheduled_at?: string | null
  published_at?: string | null
  error_message?: string | null
  account_name?: string | null
  created_at: string
  updated_at: string
}

export interface RedditDiscoverItem {
  title: string
  url: string
  thing_id: string
  subreddit: string
  score: number
  num_comments: number
  created_utc: number
  body?: string
}

export interface RedditCommunity {
  id: number
  name: string
  category: CommunityCategory
  purpose?: CommunityPurpose
  account_id?: number | null
  rules_note?: string | null
  allows_links: boolean
  promo_weekday?: number | null
  daily_post_limit: number
  best_hour_utc?: number | null
  priority: number
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface RedditMetric {
  id: number
  post_id: number
  score: number
  num_comments: number
  upvote_ratio?: number | null
  synced_at: string
}

export interface RedditOverview {
  posts_pending: number
  posts_approved: number
  posts_posted: number
  posts_posted_today: number
  posts_scheduled: number
  comments_pending: number
  comments_posted_today: number
  accounts_warning: number
  recent_avg_score?: number | null
  recent_total_score: number
  recent_total_comments: number
  promo_ratio_7d?: number
  promo_count_7d?: number
  casual_count_7d?: number
  persona_communities?: number
  promo_communities?: number
  comments_likely_ai?: number
}

export interface RedditBrandProduct {
  id: number
  brand_id: number
  name: string
  category: string
  talking_points: string[]
  keywords?: string[]
  is_active: boolean
  community_ids?: number[]
  community_names?: string[]
  created_at: string
  updated_at: string
}

export interface RedditBrand {
  id: number
  site_id: number
  name: string
  is_active: boolean
  products: RedditBrandProduct[]
  created_at: string
  updated_at: string
}

// ============ 状态 / 账号 ============
export function getRedditStatus() {
  return http.get<any, RedditStatus>('/reddit/status')
}

export function syncRedditAccounts() {
  return http.post<any, RedditStatus>('/reddit/accounts/sync', {})
}

export function listAccountProfiles() {
  return http.get<any, RedditAccount[]>('/reddit/accounts/profiles')
}

export function updateAccountProfile(
  accountId: number,
  payload: {
    role?: AccountRole
    stage?: AccountStage
    karma?: number
    persona?: string
    persona_config?: PersonaConfig
    daily_post_limit?: number
    daily_comment_limit?: number
    apply_role_defaults?: boolean
    clear_warning?: boolean
  },
) {
  return http.patch<any, RedditAccount>(`/reddit/accounts/${accountId}/profile`, payload)
}

// ============ 社区库 ============
export function listCommunities(accountId?: number) {
  return http.get<any, RedditCommunity[]>('/reddit/communities', {
    params: accountId ? { account_id: accountId } : undefined,
  })
}

export function createCommunity(payload: Partial<RedditCommunity>) {
  return http.post<any, RedditCommunity>('/reddit/communities', payload)
}

export function updateCommunity(id: number, payload: Partial<RedditCommunity>) {
  return http.patch<any, RedditCommunity>(`/reddit/communities/${id}`, payload)
}

export function deleteCommunity(id: number) {
  return http.delete<any, void>(`/reddit/communities/${id}`)
}

// ============ Zernio Key ============
export function listZernioKeys() {
  return http.get<any, ZernioKey[]>('/reddit/zernio-keys')
}

export function createZernioKey(payload: { label: string; api_key: string; profile_id?: string }) {
  return http.post<any, ZernioKey>('/reddit/zernio-keys', payload)
}

export function updateZernioKey(id: number, payload: {
  label?: string
  api_key?: string
  profile_id?: string
  is_enabled?: boolean
}) {
  return http.patch<any, ZernioKey>(`/reddit/zernio-keys/${id}`, payload)
}

export function deleteZernioKey(id: number) {
  return http.delete<any, void>(`/reddit/zernio-keys/${id}`)
}

export function suggestPersonaCommunities(payload: { account_id?: number; interests?: string[] }) {
  return http.post<any, { suggested: string[]; added: number }>('/reddit/communities/suggest', payload)
}

export function listBrands() {
  return http.get<any, RedditBrand[]>('/reddit/brands')
}

export function createBrand(payload: { name: string; is_active?: boolean }) {
  return http.post<any, RedditBrand>('/reddit/brands', payload)
}

export function updateBrand(id: number, payload: { name?: string; is_active?: boolean }) {
  return http.patch<any, RedditBrand>(`/reddit/brands/${id}`, payload)
}

export function deleteBrand(id: number) {
  return http.delete<any, void>(`/reddit/brands/${id}`)
}

export function createBrandProduct(brandId: number, payload: {
  name: string
  category?: string
  talking_points?: string[]
  keywords?: string[]
  is_active?: boolean
  community_ids?: number[]
}) {
  return http.post<any, RedditBrandProduct>(`/reddit/brands/${brandId}/products`, payload)
}

export function updateBrandProduct(productId: number, payload: {
  name?: string
  category?: string
  talking_points?: string[]
  keywords?: string[]
  is_active?: boolean
  community_ids?: number[]
}) {
  return http.patch<any, RedditBrandProduct>(`/reddit/products/${productId}`, payload)
}

export function deleteBrandProduct(productId: number) {
  return http.delete<any, void>(`/reddit/products/${productId}`)
}

export function smartDiscoverReddit(payload: {
  account_id: number
  subreddits: string[]
  brand_id?: number
  product_id?: number
}) {
  return http.post<any, { items: RedditDiscoverItem[]; meta: { auto_mode: boolean; queued_count: number; skipped_count?: number; errors?: number } }>(
    '/reddit/discover/smart',
    payload,
    { timeout: 180000 },
  )
}

// ============ 发帖 ============
export function generateRedditPost(payload: {
  account_id: number
  post_type: PostType
  subreddit: string
  keyword?: string
  include_site_url?: boolean
}) {
  return http.post<any, RedditPost>('/reddit/posts/generate', payload)
}

export function listRedditPosts(params?: { status?: string; account_id?: number }) {
  return http.get<any, RedditPost[]>('/reddit/posts', { params })
}

export function updateRedditPost(id: number, payload: { title?: string; body?: string; subreddit?: string }) {
  return http.patch<any, RedditPost>(`/reddit/posts/${id}`, payload)
}

export function approveRedditPost(id: number) {
  return http.post<any, RedditPost>(`/reddit/posts/${id}/approve`, {})
}

export function rejectRedditPost(id: number) {
  return http.post<any, RedditPost>(`/reddit/posts/${id}/reject`, {})
}

export function deleteRedditPost(id: number) {
  return http.delete<any, void>(`/reddit/posts/${id}`)
}

export function publishRedditPost(id: number) {
  return http.post<any, RedditPost>(`/reddit/posts/${id}/publish`, {})
}

export function scheduleRedditPost(id: number, scheduled_at: string) {
  return http.post<any, RedditPost>(`/reddit/posts/${id}/schedule`, { scheduled_at })
}

export function cancelRedditPostSchedule(id: number) {
  return http.post<any, RedditPost>(`/reddit/posts/${id}/schedule/cancel`, {})
}

export function listPostMetrics(id: number) {
  return http.get<any, RedditMetric[]>(`/reddit/posts/${id}/metrics`)
}

export function syncPostMetrics(id: number) {
  return http.post<any, RedditMetric[]>(`/reddit/posts/${id}/metrics/sync`, {})
}

// ============ 评论 ============
export function generateRedditComment(payload: {
  account_id: number
  target_post_url: string
  include_site_url?: boolean
  content_intent?: ContentIntent
  brand_id?: number
  product_id?: number
}) {
  return http.post<any, RedditComment>('/reddit/comments/generate', payload)
}

export function generateRedditCommentBatch(payload: {
  account_id: number
  subreddit: string
  keyword: string
  posts: Array<{ url: string; title?: string; body?: string }>
  include_site_url?: boolean
  content_intent?: ContentIntent
  brand_id?: number
  product_id?: number
}) {
  return http.post<any, RedditComment[]>('/reddit/comments/generate-batch', payload)
}

export function listRedditComments(params?: { status?: string; account_id?: number }) {
  return http.get<any, RedditComment[]>('/reddit/comments', { params })
}

export function updateRedditComment(id: number, payload: { body?: string }) {
  return http.patch<any, RedditComment>(`/reddit/comments/${id}`, payload)
}

export function approveRedditComment(id: number) {
  return http.post<any, RedditComment>(`/reddit/comments/${id}/approve`, {})
}

export function rejectRedditComment(id: number) {
  return http.post<any, RedditComment>(`/reddit/comments/${id}/reject`, {})
}

export function deleteRedditComment(id: number) {
  return http.delete<any, void>(`/reddit/comments/${id}`)
}

export function publishRedditComment(id: number) {
  return http.post<any, RedditComment>(`/reddit/comments/${id}/publish`, {})
}

export function scheduleRedditComment(id: number, scheduled_at: string) {
  return http.post<any, RedditComment>(`/reddit/comments/${id}/schedule`, { scheduled_at })
}

export function cancelRedditCommentSchedule(id: number) {
  return http.post<any, RedditComment>(`/reddit/comments/${id}/schedule/cancel`, {})
}

// ============ 搜索发现 ============
export function searchRedditPosts(params: {
  subreddit: string
  keyword: string
  limit?: number
  account_id?: number
}) {
  return http.get<any, { items: RedditDiscoverItem[]; meta: { auto_mode: boolean; queued_count: number } }>(
    '/reddit/discover/search',
    { params },
  )
}

// ============ 运营概览 ============
export function getRedditOverview() {
  return http.get<any, RedditOverview>('/reddit/overview')
}

// ============ 养号互动 ============
export interface RedditEngageComment {
  thing_id: string
  body: string
  author: string
  score: number
  created_utc: number
  url: string
}

export function fetchEngageFeed(params: {
  account_id: number
  subreddit: string
  limit?: number
}) {
  return http.get<any, { items: RedditDiscoverItem[] }>('/reddit/engage/feed', { params })
}

export function fetchEngageComments(params: {
  account_id: number
  thing_id: string
  subreddit?: string
  limit?: number
}) {
  return http.get<any, { items: RedditEngageComment[] }>('/reddit/engage/comments', { params })
}

export function upvoteRedditThing(payload: {
  account_id: number
  thing_id: string
  direction?: number
}) {
  return http.post<any, { ok: boolean; thing_id: string; direction: number }>(
    '/reddit/engage/vote',
    { direction: 1, ...payload },
  )
}
