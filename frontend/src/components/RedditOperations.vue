<template>
  <div class="reddit-ops">
    <!-- 运营概览 -->
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="card-title-row">
          <span class="card-title">运营概览（量化考核）</span>
          <el-button size="small" @click="loadOverview">刷新</el-button>
        </div>
      </template>
      <el-row :gutter="12">
        <el-col :span="3"><div class="stat"><div class="stat-num">{{ overview?.posts_posted_today ?? '-' }}</div><div class="stat-label">今日发帖</div></div></el-col>
        <el-col :span="3"><div class="stat"><div class="stat-num">{{ overview?.comments_posted_today ?? '-' }}</div><div class="stat-label">今日评论</div></div></el-col>
        <el-col :span="3"><div class="stat"><div class="stat-num">{{ overview?.posts_pending ?? '-' }}</div><div class="stat-label">待审核帖</div></div></el-col>
        <el-col :span="3"><div class="stat"><div class="stat-num">{{ overview?.comments_pending ?? '-' }}</div><div class="stat-label">待审核评</div></div></el-col>
        <el-col :span="3"><div class="stat"><div class="stat-num">{{ overview?.posts_scheduled ?? '-' }}</div><div class="stat-label">定时队列</div></div></el-col>
        <el-col :span="3"><div class="stat"><div class="stat-num">{{ overview?.posts_posted ?? '-' }}</div><div class="stat-label">累计发帖</div></div></el-col>
        <el-col :span="3"><div class="stat"><div class="stat-num">{{ overview?.recent_avg_score ?? '-' }}</div><div class="stat-label">近7天均分</div></div></el-col>
        <el-col :span="3">
          <div class="stat">
            <div class="stat-num" :class="{ 'stat-danger': (overview?.accounts_warning || 0) > 0 }">{{ overview?.accounts_warning ?? '-' }}</div>
            <div class="stat-label">预警账号</div>
          </div>
        </el-col>
      </el-row>
      <el-row :gutter="12" style="margin-top: 12px">
        <el-col :span="6"><div class="stat"><div class="stat-num">{{ overview ? `${Math.round((overview.promo_ratio_7d || 0) * 100)}%` : '-' }}</div><div class="stat-label">近7天产品占比</div></div></el-col>
        <el-col :span="6"><div class="stat"><div class="stat-num">{{ overview ? (overview.persona_communities || 0) : '-' }}</div><div class="stat-label">人设社区</div></div></el-col>
        <el-col :span="6"><div class="stat"><div class="stat-num">{{ overview ? (overview.promo_communities || 0) : '-' }}</div><div class="stat-label">产品社区</div></div></el-col>
        <el-col :span="6">
          <div class="stat">
            <div class="stat-num" :class="{ 'stat-danger': (overview?.comments_likely_ai || 0) > 0 }">{{ overview ? (overview.comments_likely_ai || 0) : '-' }}</div>
            <div class="stat-label">疑似 AI 评论</div>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <el-card shadow="never" class="section-card" style="margin-top: 16px">
      <template #header>
        <div class="card-title-row">
          <span class="card-title">当前发布账号</span>
          <router-link to="/accounts">管理社交账号</router-link>
        </div>
      </template>
      <div class="account-bar">
        <el-tag v-if="status?.connected" type="success" size="small">已同步</el-tag>
        <el-tag v-else-if="status && !status.configured" type="warning" size="small">未配置 Key</el-tag>
        <el-tag v-else type="info" size="small">未同步</el-tag>
        <el-select v-model="selectedAccountId" placeholder="选择 Reddit 账号" style="width: 320px" filterable>
          <el-option
            v-for="a in status?.accounts || []"
            :key="a.id"
            :label="a.account_name"
            :value="a.id"
          />
        </el-select>
        <el-button @click="loadStatus">刷新</el-button>
      </div>
    </el-card>

    <el-tabs v-model="innerTab" type="card" style="margin-top: 16px" @tab-change="onTabChange">
      <!-- 发帖 -->
      <el-tab-pane label="发帖审核" name="posts">
        <el-row :gutter="16">
          <el-col :span="10">
            <el-form label-position="top">
              <el-form-item label="帖子类型">
                <el-radio-group v-model="postForm.post_type" class="post-type-radios">
                  <el-radio v-for="opt in postTypeOptions" :key="opt.value" :value="opt.value">
                    <span class="post-type-label">
                      {{ opt.label }}
                      <el-popover placement="bottom-start" :width="340" trigger="hover" :show-after="200">
                        <template #reference>
                          <el-icon class="post-type-help" @click.stop.prevent>
                            <QuestionFilled />
                          </el-icon>
                        </template>
                        <div class="post-type-help-body">
                          <p class="help-title">{{ opt.label }}</p>
                          <p>{{ opt.desc }}</p>
                          <p><span class="help-k">标题例</span>{{ opt.exampleTitle }}</p>
                          <p><span class="help-k">写法</span>{{ opt.exampleTip }}</p>
                          <p v-if="opt.productNote" class="help-warn">{{ opt.productNote }}</p>
                        </div>
                      </el-popover>
                    </span>
                  </el-radio>
                </el-radio-group>
                <p class="muted" style="margin: 6px 0 0">偏 Reddit 原生语气；树洞/求助主帖不提产品。点类型旁 ? 看说明与例子。</p>
              </el-form-item>
              <el-form-item label="目标社区">
                <el-select
                  v-model="postForm.subreddit"
                  filterable
                  allow-create
                  default-first-option
                  placeholder="优先人设社区；约 9 成人设、1 成产品"
                  style="width: 100%"
                >
                  <el-option-group v-if="personaCommunities.length" label="人设社区（推荐，计为闲聊）">
                    <el-option
                      v-for="c in personaCommunities"
                      :key="`p-${c.id}`"
                      :label="`r/${c.name}`"
                      :value="c.name"
                    />
                  </el-option-group>
                  <el-option-group v-if="promoCommunities.length" label="产品社区（≤10% 配额）">
                    <el-option
                      v-for="c in promoCommunities"
                      :key="`m-${c.id}`"
                      :label="`r/${c.name}`"
                      :value="c.name"
                    />
                  </el-option-group>
                </el-select>
                <p class="muted" style="margin: 6px 0 0">
                  与评论同一 90/10 原则：生成不拦；发布时选产品社区或带站点链接会计入产品配额。
                  <span v-if="overview">近 7 天产品占比 {{ mixPromoPercent }}%</span>
                </p>
              </el-form-item>
              <el-form-item label="主题关键词（可选）">
                <el-input v-model="postForm.keyword" placeholder="可选；不填则按帖子类型与目标社区自由发挥" />
              </el-form-item>
              <el-form-item v-if="postForm.post_type === 'pitfall' || postForm.post_type === 'guide'" label="带入站点链接">
                <el-switch v-model="postForm.include_site_url" />
                <span class="muted" style="margin-left: 8px">仅踩坑/干货可选；建议仅软提名称</span>
              </el-form-item>
              <el-button type="primary" :loading="postGenerating" :disabled="!selectedAccountId" @click="handleGeneratePost">
                AI 生成并入审核队列
              </el-button>
            </el-form>
          </el-col>
          <el-col :span="14">
            <div class="list-header">
              <span>审核队列（当前账号）</span>
              <el-select v-model="postStatusFilter" clearable placeholder="状态" style="width: 130px" @change="loadPosts">
                <el-option label="待审核" value="pending_review" />
                <el-option label="已批准" value="approved" />
                <el-option label="已发布" value="posted" />
                <el-option label="失败" value="failed" />
              </el-select>
            </div>
            <el-table :data="posts" v-loading="postsLoading" size="small" stripe max-height="480">
              <el-table-column prop="id" label="ID" width="50" />
              <el-table-column prop="post_type" label="类型" width="78">
                <template #default="{ row }">{{ postTypeLabel(row.post_type) }}</template>
              </el-table-column>
              <el-table-column label="意图" width="70">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.content_intent === 'promo' ? 'warning' : 'info'">{{ intentLabel(row.content_intent) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="title" label="标题" min-width="150" show-overflow-tooltip />
              <el-table-column label="定时" width="90">
                <template #default="{ row }">
                  <span v-if="row.scheduled_at">{{ formatTime(row.scheduled_at) }}</span>
                  <span v-else-if="row.published_at" class="muted">{{ formatTime(row.published_at) }}</span>
                  <span v-else class="muted">—</span>
                </template>
              </el-table-column>
              <el-table-column prop="status" label="状态" width="84">
                <template #default="{ row }">
                  <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="失败原因" min-width="140" show-overflow-tooltip>
                <template #default="{ row }">
                  <span v-if="row.error_message" class="muted">{{ row.error_message }}</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="360" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" @click="openPostEdit(row)">编辑</el-button>
                  <el-button v-if="row.status === 'pending_review'" size="small" type="warning" @click="approvePost(row.id)">批准</el-button>
                  <el-button v-if="row.status === 'pending_review'" size="small" @click="rejectPost(row.id)">拒绝</el-button>
                  <el-button
                    v-if="row.status === 'approved' || row.status === 'failed'"
                    size="small"
                    type="success"
                    :loading="publishingPostId === row.id"
                    @click="publishPost(row.id)"
                  >{{ row.status === 'failed' ? '重试发布' : '发布' }}</el-button>
                  <el-button v-if="row.status === 'approved'" size="small" @click="openSchedule(row)">定时</el-button>
                  <el-button v-if="row.scheduled_at" size="small" @click="cancelSchedule(row)">取消定时</el-button>
                  <el-button v-if="row.status === 'posted'" size="small" @click="syncMetrics(row)">同步数据</el-button>
                  <el-button v-if="row.status !== 'posting'" size="small" type="danger" @click="deletePost(row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- 评论 -->
      <el-tab-pane label="评论审核" name="comments">
        <el-tabs v-model="commentInputTab" type="border-card">
          <el-tab-pane label="智能发现" name="smart">
            <p class="muted" style="margin-bottom: 12px">
              选择产品后自动勾选已绑定社区；按产品关键词在社区中搜索相关帖并入队。也可再手动增减社区。
              <router-link to="/brands">管理品牌产品库</router-link>
            </p>
            <el-form label-position="top" style="max-width: 560px; margin-bottom: 12px">
              <el-form-item label="评论社区" required>
                <el-select
                  v-model="smartDiscoverSubreddits"
                  multiple
                  filterable
                  allow-create
                  default-first-option
                  collapse-tags
                  collapse-tags-tooltip
                  placeholder="选产品后自动填绑定社区；也可手选"
                  style="width: 100%"
                >
                  <el-option-group v-if="personaCommunities.length" label="人设社区（闲聊）">
                    <el-option
                      v-for="c in personaCommunities"
                      :key="`sd-p-${c.id}`"
                      :label="`r/${c.name}`"
                      :value="c.name"
                    />
                  </el-option-group>
                  <el-option-group v-if="promoCommunities.length" label="产品社区（产品向）">
                    <el-option
                      v-for="c in promoCommunities"
                      :key="`sd-m-${c.id}`"
                      :label="`r/${c.name}`"
                      :value="c.name"
                    />
                  </el-option-group>
                </el-select>
              </el-form-item>
              <el-row :gutter="12">
                <el-col :span="12">
                  <el-form-item label="品牌（产品社区时必选）">
                    <el-select v-model="selectedBrandId" clearable placeholder="选择品牌" style="width: 100%" @change="onBrandChange">
                      <el-option v-for="b in activeBrands" :key="b.id" :label="b.name" :value="b.id" />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="产品">
                    <el-select
                      v-model="selectedProductId"
                      clearable
                      placeholder="选择产品"
                      style="width: 100%"
                      @change="onProductChange"
                    >
                      <el-option v-for="p in productsOfSelectedBrand" :key="p.id" :label="productLabel(p)" :value="p.id" />
                    </el-select>
                  </el-form-item>
                </el-col>
              </el-row>
            </el-form>
            <el-button
              type="primary"
              :loading="smartDiscovering"
              :disabled="!selectedAccountId || !smartDiscoverSubreddits.length"
              @click="handleSmartDiscover"
            >
              智能发现并入队
            </el-button>
          </el-tab-pane>
          <el-tab-pane label="粘贴 URL" name="url">
            <el-form label-position="top" style="max-width: 560px">
              <el-form-item label="目标帖子 URL">
                <el-input v-model="commentUrlForm.target_post_url" placeholder="https://www.reddit.com/r/.../comments/..." />
              </el-form-item>
              <el-form-item label="评论中带站点链接（产品向）">
                <el-switch v-model="commentUrlForm.include_site_url" />
              </el-form-item>
              <el-row v-if="commentUrlForm.include_site_url" :gutter="12">
                <el-col :span="12">
                  <el-form-item label="品牌">
                    <el-select v-model="selectedBrandId" clearable placeholder="选择品牌" style="width: 100%" @change="onBrandChange">
                      <el-option v-for="b in activeBrands" :key="b.id" :label="b.name" :value="b.id" />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="产品">
                    <el-select v-model="selectedProductId" clearable placeholder="选择产品" style="width: 100%">
                      <el-option v-for="p in productsOfSelectedBrand" :key="p.id" :label="productLabel(p)" :value="p.id" />
                    </el-select>
                  </el-form-item>
                </el-col>
              </el-row>
              <el-button type="primary" :loading="commentGenerating" :disabled="!selectedAccountId" @click="handleGenerateCommentUrl">
                AI 生成评论
              </el-button>
            </el-form>
          </el-tab-pane>
          <el-tab-pane label="高级：关键词搜索" name="search">
            <el-form label-position="top" style="max-width: 560px">
              <el-form-item label="子版块">
                <el-input v-model="commentSearchForm.subreddit" placeholder="BabyBumps" />
              </el-form-item>
              <el-form-item label="关键词">
                <el-input v-model="commentSearchForm.keyword" placeholder="baby monitor" />
              </el-form-item>
              <el-button :loading="searchLoading" @click="handleSearch">搜索帖子</el-button>
            </el-form>
            <el-table
              v-if="searchResults.length"
              :data="searchResults"
              size="small"
              style="margin-top: 12px"
              @selection-change="onSearchSelect"
            >
              <el-table-column type="selection" width="40" />
              <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
              <el-table-column prop="score" label="Score" width="70" />
              <el-table-column prop="num_comments" label="评论" width="70" />
            </el-table>
            <el-button
              v-if="selectedSearchPosts.length"
              type="primary"
              style="margin-top: 8px"
              :loading="commentGenerating"
              :disabled="!selectedAccountId"
              @click="handleGenerateCommentBatch"
            >
              为选中 {{ selectedSearchPosts.length }} 帖生成评论
            </el-button>
          </el-tab-pane>
        </el-tabs>

        <el-divider />
        <div class="list-header">
          <span>评论审核队列（当前账号）</span>
          <el-select v-model="commentStatusFilter" clearable placeholder="状态" style="width: 130px" @change="loadComments">
            <el-option label="待审核" value="pending_review" />
            <el-option label="已批准" value="approved" />
            <el-option label="已发布" value="posted" />
          </el-select>
        </div>
        <el-table :data="comments" v-loading="commentsLoading" size="small" stripe max-height="360">
          <el-table-column prop="id" label="ID" width="50" />
          <el-table-column prop="target_post_title" label="目标帖" min-width="160" show-overflow-tooltip />
          <el-table-column prop="body" label="评论预览" min-width="200" show-overflow-tooltip />
          <el-table-column label="意图" width="70">
            <template #default="{ row }">
              <el-tag size="small" :type="row.content_intent === 'promo' ? 'warning' : 'info'">{{ intentLabel(row.content_intent) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="来源" width="80">
            <template #default="{ row }">{{ sourceLabel(row.discover_source) }}</template>
          </el-table-column>
          <el-table-column label="AI" width="70">
            <template #default="{ row }">
              <el-tag v-if="row.ai_risk === 'likely_ai'" size="small" type="danger">疑似</el-tag>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="失败原因" min-width="140" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.error_message" class="muted">{{ row.error_message }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="380" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="openCommentEdit(row)">编辑</el-button>
              <el-button v-if="row.status === 'pending_review'" size="small" type="warning" @click="approveComment(row.id)">批准</el-button>
              <el-button v-if="row.status === 'pending_review'" size="small" @click="rejectComment(row.id)">拒绝</el-button>
              <el-button
                v-if="row.status === 'approved' || row.status === 'failed'"
                size="small"
                type="success"
                :loading="publishingCommentId === row.id"
                @click="publishComment(row.id)"
              >{{ row.status === 'failed' ? '重试发布' : '发布' }}</el-button>
              <el-button v-if="row.status === 'approved'" size="small" @click="openCommentSchedule(row)">定时</el-button>
              <el-button v-if="row.scheduled_at" size="small" @click="cancelCommentSchedule(row)">取消定时</el-button>
              <el-button v-if="row.status !== 'posting'" size="small" type="danger" @click="deleteComment(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 养号互动 -->
      <el-tab-pane label="养号互动" name="engage">
        <p class="muted" style="margin: 0 0 12px">
          从人设/产品社区拉帖，展开评论后可单赞或勾选批量赞（条目间自动间隔 3–8 秒）。每次最多拉 8 条评论。须人工勾选，勿无人值守刷赞。
        </p>
        <el-form inline>
          <el-form-item label="社区">
            <el-select
              v-model="engageSubreddit"
              filterable
              placeholder="选择社区"
              style="width: 240px"
            >
              <el-option-group v-if="personaCommunities.length" label="人设社区">
                <el-option
                  v-for="c in personaCommunities"
                  :key="`eg-p-${c.id}`"
                  :label="`r/${c.name}`"
                  :value="c.name"
                />
              </el-option-group>
              <el-option-group v-if="promoCommunities.length" label="产品社区">
                <el-option
                  v-for="c in promoCommunities"
                  :key="`eg-m-${c.id}`"
                  :label="`r/${c.name}`"
                  :value="c.name"
                />
              </el-option-group>
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="engageLoading" :disabled="!selectedAccountId || !engageSubreddit" @click="loadEngageFeed">
              拉取新帖
            </el-button>
            <el-button
              type="success"
              :loading="engageVoting"
              :disabled="!engageSelectedIds.length || engageVoting"
              @click="upvoteSelectedEngage"
            >
              赞选中（{{ engageSelectedIds.length }}）
            </el-button>
          </el-form-item>
        </el-form>
        <p v-if="engageProgress" class="muted" style="margin: 0 0 8px">{{ engageProgress }}</p>
        <el-table
          :data="engagePosts"
          v-loading="engageLoading"
          size="small"
          stripe
          max-height="420"
          row-key="thing_id"
          @selection-change="onEngagePostSelection"
          @expand-change="onEngageExpand"
        >
          <el-table-column type="selection" width="40" :selectable="() => !engageVoting" />
          <el-table-column type="expand">
            <template #default="{ row }">
              <div style="padding: 8px 12px 12px 48px">
                <div class="list-header" style="margin-bottom: 6px">
                  <span>评论</span>
                  <el-button size="small" :loading="row._commentsLoading" @click="loadEngageComments(row)">刷新评论</el-button>
                </div>
                <el-table
                  v-if="row._comments?.length"
                  :data="row._comments"
                  size="small"
                  max-height="240"
                  @selection-change="(rows) => onEngageCommentSelection(row.thing_id, rows)"
                >
                  <el-table-column type="selection" width="40" :selectable="() => !engageVoting" />
                  <el-table-column prop="author" label="作者" width="100" show-overflow-tooltip />
                  <el-table-column prop="body" label="内容" min-width="220" show-overflow-tooltip />
                  <el-table-column prop="score" label="分" width="56" />
                  <el-table-column label="操作" width="80">
                    <template #default="{ row: c }">
                      <el-button
                        size="small"
                        type="primary"
                        link
                        :disabled="engageVoting || !!engageVoted[c.thing_id]"
                        @click="upvoteOne(c.thing_id)"
                      >
                        {{ engageVoted[c.thing_id] ? '已赞' : '赞' }}
                      </el-button>
                    </template>
                  </el-table-column>
                </el-table>
                <p v-else class="muted" style="margin: 0">{{ row._commentsLoaded ? '暂无评论' : '点「评论」或展开后点「刷新评论」加载' }}</p>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
          <el-table-column prop="score" label="分" width="56" />
          <el-table-column prop="num_comments" label="评" width="56" />
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button size="small" link type="primary" @click="loadEngageComments(row)">评论</el-button>
              <el-button
                size="small"
                link
                type="success"
                :disabled="engageVoting || !!engageVoted[row.thing_id]"
                @click="upvoteOne(row.thing_id)"
              >
                {{ engageVoted[row.thing_id] ? '已赞' : '赞' }}
              </el-button>
              <el-link v-if="row.url" :href="row.url" target="_blank" type="info" style="margin-left: 6px; font-size: 12px">打开</el-link>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 账号矩阵 -->
      <el-tab-pane label="账号矩阵" name="accounts">
        <div class="list-header">
          <span>分层运营：养号号 / 种草号 / 答疑号（Karma 300+ 轻度植入，500+ 稳定推广）</span>
          <el-button size="small" @click="loadProfiles">刷新</el-button>
        </div>
        <el-table :data="profiles" v-loading="profilesLoading" size="small" stripe>
          <el-table-column prop="account_name" label="账号" min-width="120" />
          <el-table-column label="角色" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="roleTag(row.role)">{{ roleLabel(row.role) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="阶段" width="110">
            <template #default="{ row }">{{ stageLabel(row.stage) }}</template>
          </el-table-column>
          <el-table-column prop="karma" label="Karma" width="70" />
          <el-table-column label="今日用量" width="120">
            <template #default="{ row }">{{ row.posts_today }} 帖 / {{ row.comments_today }} 评</template>
          </el-table-column>
          <el-table-column label="日限额" width="100">
            <template #default="{ row }">{{ row.daily_post_limit }} 帖 / {{ row.daily_comment_limit }} 评</template>
          </el-table-column>
          <el-table-column label="风控" width="120">
            <template #default="{ row }">
              <el-tooltip v-if="row.risk_status === 'warning'" :content="row.risk_reason || ''" placement="top">
                <el-tag size="small" type="danger">预警中</el-tag>
              </el-tooltip>
              <el-tag v-else size="small" type="success">正常</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="160" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="openProfileEdit(row)">配置</el-button>
              <el-button v-if="row.risk_status === 'warning'" size="small" type="warning" @click="clearWarning(row)">解除预警</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 社区库 -->
      <el-tab-pane label="社区库" name="communities">
        <div class="list-header">
          <span>人设社区只属于当前账号；产品社区全站共用</span>
          <div>
            <el-button size="small" :loading="suggesting" @click="handleSuggestCommunities">按人设建议社区</el-button>
            <el-button size="small" type="primary" @click="openCommunityEdit()">新增社区</el-button>
          </div>
        </div>
        <el-table :data="communities" v-loading="communitiesLoading" size="small" stripe>
          <el-table-column label="社区" min-width="140">
            <template #default="{ row }">r/{{ row.name }}</template>
          </el-table-column>
          <el-table-column label="用途" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.purpose === 'promo' ? 'warning' : 'success'">{{ row.purpose === 'promo' ? '产品' : '人设' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="分类" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.category === 'core' ? 'primary' : 'info'">{{ row.category === 'core' ? '核心' : '长尾' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="rules_note" label="版规备忘" min-width="180" show-overflow-tooltip />
          <el-table-column label="外链" width="70">
            <template #default="{ row }">
              <el-tag size="small" :type="row.allows_links ? 'success' : 'danger'">{{ row.allows_links ? '允许' : '禁链' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="自推广日" width="90">
            <template #default="{ row }">{{ row.promo_weekday === null || row.promo_weekday === undefined ? '不限' : weekdayLabel(row.promo_weekday) }}</template>
          </el-table-column>
          <el-table-column prop="daily_post_limit" label="日上限" width="70" />
          <el-table-column label="活跃时段(UTC)" width="110">
            <template #default="{ row }">{{ row.best_hour_utc === null || row.best_hour_utc === undefined ? '—' : `${row.best_hour_utc}:00` }}</template>
          </el-table-column>
          <el-table-column prop="priority" label="优先级" width="70" />
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="openCommunityEdit(row)">编辑</el-button>
              <el-button size="small" type="danger" @click="removeCommunity(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 编辑帖子弹窗 -->
    <el-dialog v-model="postEditDialog" title="编辑 Reddit 帖子" width="640px">
      <el-form label-position="top">
        <el-form-item label="标题"><el-input v-model="postEditForm.title" /></el-form-item>
        <el-form-item label="子版块"><el-input v-model="postEditForm.subreddit" /></el-form-item>
        <el-form-item label="正文"><el-input v-model="postEditForm.body" type="textarea" :rows="10" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="postEditDialog = false">取消</el-button>
        <el-button type="primary" :loading="postSaving" @click="savePostEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 编辑评论弹窗 -->
    <el-dialog v-model="commentEditDialog" title="编辑 Reddit 评论" width="560px">
      <el-input v-model="commentEditForm.body" type="textarea" :rows="8" />
      <template #footer>
        <el-button @click="commentEditDialog = false">取消</el-button>
        <el-button type="primary" :loading="commentSaving" @click="saveCommentEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 定时发布弹窗 -->
    <el-dialog v-model="scheduleDialog" title="设置定时发布" width="440px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="系统每 15 分钟自动扫描并发布到期的已批准内容，建议避开整点集中发布"
        style="margin-bottom: 12px"
      />
      <el-date-picker
        v-model="scheduleTime"
        type="datetime"
        placeholder="选择发布时间"
        format="YYYY-MM-DD HH:mm"
        value-format="YYYY-MM-DDTHH:mm:ss"
        style="width: 100%"
      />
      <template #footer>
        <el-button @click="scheduleDialog = false">取消</el-button>
        <el-button type="primary" :loading="scheduleSaving" @click="saveSchedule">确定</el-button>
      </template>
    </el-dialog>

    <!-- 账号配置弹窗 -->
    <el-dialog v-model="profileDialog" title="账号矩阵配置" width="520px">
      <el-form label-position="top">
        <el-form-item label="角色">
          <el-select v-model="profileForm.role" style="width: 100%">
            <el-option label="基础养号号（纯互动，不发帖）" value="warmup" />
            <el-option label="核心种草号（真实用户人设）" value="seeding" />
            <el-option label="专业答疑号（专家人设）" value="expert" />
          </el-select>
        </el-form-item>
        <el-form-item label="养号阶段">
          <el-select v-model="profileForm.stage" style="width: 100%">
            <el-option label="养号第 1-2 周（禁发帖）" value="warmup_week1_2" />
            <el-option label="养号第 3-4 周（仅无链接干货帖）" value="warmup_week3_4" />
            <el-option label="已就绪（Karma 300+）" value="ready" />
            <el-option label="稳定运营（Karma 500+）" value="active" />
            <el-option label="已暂停" value="suspended" />
          </el-select>
        </el-form-item>
        <el-form-item label="Karma 积分">
          <el-input-number v-model="profileForm.karma" :min="0" style="width: 100%" />
        </el-form-item>
        <el-form-item label="人设声音">
          <el-input v-model="profileForm.voice" placeholder="working mom / night-shift dad" />
        </el-form-item>
        <el-form-item label="兴趣标签（逗号分隔）">
          <el-input v-model="profileForm.interestsText" placeholder="parenting, cooking, remote work" />
        </el-form-item>
        <el-form-item label="口头禅 / 生活细节">
          <el-input v-model="profileForm.quirks" placeholder="always tired, drinks cold brew" />
        </el-form-item>
        <el-form-item label="不要出现的口吻">
          <el-input v-model="profileForm.never_say" placeholder="sales pitch, expert lecture" />
        </el-form-item>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="每日发帖上限">
              <el-input-number v-model="profileForm.daily_post_limit" :min="0" :max="20" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="每日评论上限">
              <el-input-number v-model="profileForm.daily_comment_limit" :min="0" :max="100" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="profileDialog = false">取消</el-button>
        <el-button type="primary" :loading="profileSaving" @click="saveProfile">保存</el-button>
      </template>
    </el-dialog>

    <!-- 社区编辑弹窗 -->
    <el-dialog v-model="communityDialog" :title="communityForm.id ? '编辑社区' : '新增社区'" width="560px">
      <el-form label-position="top">
        <el-form-item label="社区名（不含 r/）">
          <el-input v-model="communityForm.name" placeholder="BabyMonitoring" />
        </el-form-item>
        <el-form-item label="用途">
          <el-radio-group v-model="communityForm.purpose">
            <el-radio value="persona">人设兴趣（自动发现）</el-radio>
            <el-radio value="promo">产品相关（手填，约 10%）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="分类">
              <el-select v-model="communityForm.category" style="width: 100%">
                <el-option label="核心垂直" value="core" />
                <el-option label="长尾场景" value="longtail" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="优先级（1 最高）">
              <el-input-number v-model="communityForm.priority" :min="1" :max="5" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="版规备忘">
          <el-input v-model="communityForm.rules_note" type="textarea" :rows="2" placeholder="如：仅固定日期可自推广 / 禁止纯商家内容" />
        </el-form-item>
        <el-row :gutter="12">
          <el-col :span="8">
            <el-form-item label="允许外链">
              <el-switch v-model="communityForm.allows_links" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="自推广日">
              <el-select v-model="communityForm.promo_weekday" clearable placeholder="不限" style="width: 100%">
                <el-option v-for="(w, i) in weekdayNames" :key="i" :label="w" :value="i" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="每日发帖上限">
              <el-input-number v-model="communityForm.daily_post_limit" :min="0" :max="10" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="活跃时段（UTC 小时）">
              <el-input-number v-model="communityForm.best_hour_utc" :min="0" :max="23" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="启用">
              <el-switch v-model="communityForm.is_active" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="communityDialog = false">取消</el-button>
        <el-button type="primary" :loading="communitySaving" @click="saveCommunity">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { QuestionFilled } from '@element-plus/icons-vue'
import {
  getRedditStatus,
  generateRedditPost, listRedditPosts, updateRedditPost, approveRedditPost, publishRedditPost, deleteRedditPost,
  generateRedditComment, generateRedditCommentBatch, listRedditComments,
  updateRedditComment, approveRedditComment, publishRedditComment, deleteRedditComment,
  searchRedditPosts,
  getRedditOverview, listAccountProfiles, updateAccountProfile,
  listCommunities, createCommunity, updateCommunity, deleteCommunity,
  scheduleRedditPost, cancelRedditPostSchedule, syncPostMetrics,
  rejectRedditPost, rejectRedditComment, scheduleRedditComment, cancelRedditCommentSchedule,
  suggestPersonaCommunities, listBrands, smartDiscoverReddit,
  fetchEngageFeed, fetchEngageComments, upvoteRedditThing,
  type RedditStatus, type RedditPost, type RedditComment, type RedditDiscoverItem,
  type RedditAccount, type PostType,
  type RedditOverview, type RedditCommunity,
  type RedditBrand, type RedditBrandProduct,
  type RedditEngageComment,
} from '@/api/reddit'

const route = useRoute()
const status = ref<RedditStatus | null>(null)
const selectedAccountId = ref<number | null>(null)
const innerTab = ref('posts')
const overview = ref<RedditOverview | null>(null)
const promoCommunities = computed(() => communities.value.filter((c) => c.purpose === 'promo' && c.is_active))
const personaCommunities = computed(() => communities.value.filter((c) => c.purpose === 'persona' && c.is_active))
const mixPromoPercent = computed(() => {
  const r = overview.value?.promo_ratio_7d
  if (r == null || Number.isNaN(Number(r))) return '0'
  return (Number(r) * 100).toFixed(1)
})

const brands = ref<RedditBrand[]>([])
const selectedBrandId = ref<number | null>(null)
const selectedProductId = ref<number | null>(null)
const activeBrands = computed(() => brands.value.filter((b) => b.is_active !== false))
const productsOfSelectedBrand = computed(() => {
  const b = brands.value.find((x) => x.id === selectedBrandId.value)
  return (b?.products || []).filter((p) => p.is_active !== false)
})
const suggesting = ref(false)
const smartDiscovering = ref(false)
const smartDiscoverSubreddits = ref<string[]>([])

// ===== 养号互动 =====
type EngagePostRow = RedditDiscoverItem & {
  _comments?: RedditEngageComment[]
  _commentsLoading?: boolean
  _commentsLoaded?: boolean
}
const engageSubreddit = ref('')
const engagePosts = ref<EngagePostRow[]>([])
const engageLoading = ref(false)
const engageVoting = ref(false)
const engageProgress = ref('')
const engageVoted = reactive<Record<string, boolean>>({})
const engageSelectedPostIds = ref<string[]>([])
const engageSelectedCommentIds = ref<string[]>([])
const engageSelectedIds = computed(() => {
  const set = new Set([...engageSelectedPostIds.value, ...engageSelectedCommentIds.value])
  return [...set].filter((id) => !engageVoted[id])
})

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms))
}

function onEngagePostSelection(rows: EngagePostRow[]) {
  engageSelectedPostIds.value = rows.map((r) => r.thing_id)
}

function onEngageCommentSelection(postThingId: string, rows: RedditEngageComment[]) {
  const other = engageSelectedCommentIds.value.filter((id) => {
    const post = engagePosts.value.find((p) => p.thing_id === postThingId)
    return !(post?._comments || []).some((c) => c.thing_id === id)
  })
  engageSelectedCommentIds.value = [...other, ...rows.map((r) => r.thing_id)]
}

async function loadEngageFeed() {
  if (!selectedAccountId.value || !engageSubreddit.value) return
  engageLoading.value = true
  engageProgress.value = ''
  engageSelectedPostIds.value = []
  engageSelectedCommentIds.value = []
  try {
    const res = await fetchEngageFeed({
      account_id: selectedAccountId.value,
      subreddit: engageSubreddit.value,
      limit: 15,
    })
    engagePosts.value = (res.items || []).map((p) => ({ ...p }))
  } finally {
    engageLoading.value = false
  }
}

async function loadEngageComments(row: EngagePostRow) {
  if (!selectedAccountId.value) return
  row._commentsLoading = true
  try {
    const res = await fetchEngageComments({
      account_id: selectedAccountId.value,
      thing_id: row.thing_id,
      subreddit: row.subreddit || engageSubreddit.value,
      limit: 8,
    })
    row._comments = res.items || []
    row._commentsLoaded = true
  } finally {
    row._commentsLoading = false
  }
}

function onEngageExpand(row: EngagePostRow, expandedRows: EngagePostRow[]) {
  if (expandedRows.some((r) => r.thing_id === row.thing_id) && !row._commentsLoaded) {
    loadEngageComments(row)
  }
}

async function upvoteOne(thingId: string) {
  if (!selectedAccountId.value || engageVoted[thingId]) return
  try {
    await upvoteRedditThing({ account_id: selectedAccountId.value, thing_id: thingId, direction: 1 })
    engageVoted[thingId] = true
    ElMessage.success('已点赞')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '点赞失败')
  }
}

async function upvoteSelectedEngage() {
  if (!selectedAccountId.value) return
  const ids = engageSelectedIds.value.slice(0, 20)
  if (!ids.length) {
    ElMessage.warning('请先勾选帖子或评论')
    return
  }
  engageVoting.value = true
  let ok = 0
  let fail = 0
  try {
    for (let i = 0; i < ids.length; i++) {
      const tid = ids[i]
      engageProgress.value = `点赞中 ${i + 1}/${ids.length}…`
      try {
        await upvoteRedditThing({ account_id: selectedAccountId.value, thing_id: tid, direction: 1 })
        engageVoted[tid] = true
        ok += 1
      } catch {
        fail += 1
      }
      if (i < ids.length - 1) {
        const wait = 3000 + Math.floor(Math.random() * 5000)
        engageProgress.value = `间隔 ${Math.round(wait / 1000)}s 后继续（${i + 1}/${ids.length}）`
        await sleep(wait)
      }
    }
    ElMessage.success(`完成：成功 ${ok}，失败 ${fail}`)
  } finally {
    engageVoting.value = false
    engageProgress.value = ''
  }
}

// ===== 账号矩阵 =====
const profiles = ref<RedditAccount[]>([])
const profilesLoading = ref(false)
const profileDialog = ref(false)
const profileSaving = ref(false)
const profileForm = reactive({
  accountId: 0, role: 'warmup' as string, stage: 'warmup_week1_2' as string,
  karma: 0, persona: '', voice: '', interestsText: '', quirks: '', never_say: '',
  daily_post_limit: 0, daily_comment_limit: 10,
})

// ===== 社区库 =====
const communities = ref<RedditCommunity[]>([])
const communitiesLoading = ref(false)
const communityDialog = ref(false)
const communitySaving = ref(false)
const weekdayNames = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const communityForm = reactive<Partial<RedditCommunity>>({
  id: 0, name: '', category: 'core', purpose: 'persona', rules_note: '', allows_links: true,
  promo_weekday: undefined, daily_post_limit: 1, best_hour_utc: undefined, priority: 3, is_active: true,
})

// ===== 定时发布 =====
const scheduleDialog = ref(false)
const scheduleSaving = ref(false)
const scheduleTime = ref('')
let scheduleTargetPost: RedditPost | null = null
let scheduleTargetComment: RedditComment | null = null

const postForm = reactive<{ post_type: PostType; subreddit: string; keyword: string; include_site_url: boolean }>({
  post_type: 'pitfall', subreddit: '', keyword: '', include_site_url: false,
})

const postTypeOptions: {
  value: PostType
  label: string
  desc: string
  exampleTitle: string
  exampleTip: string
  productNote?: string
}[] = [
  {
    value: 'pitfall',
    label: '踩坑避雷',
    desc: '先大篇幅吐槽交智商税、试烂货的倒霉经历，产品只作为“后来意外找到的解法”轻轻出现，并带一点小槽点。',
    exampleTitle: 'Don’t buy XXX — I wasted $300 on this…',
    exampleTip: '重点写前面踩坑细节与情绪；结尾轻提解决方案，再问大家有没有类似经历。',
  },
  {
    value: 'vent',
    label: '深夜树洞',
    desc: '谈压力、焦虑、生活状态等情绪问题，拉近距离。像真实有烦恼的人，而不是品牌号。',
    exampleTitle: 'Burning out — how do you even deal with XXX?',
    exampleTip: '只倾诉和提问；产品留给评论区再用养号号轻提。',
    productNote: '主帖禁止提任何品牌/产品/链接。',
  },
  {
    value: 'unpopular',
    label: '逆反暴论',
    desc: '抛出一个稍反直觉、可辩论的观点，吸引讨论；在争论中自然暴露自己最终的选择。',
    exampleTitle: 'Unpopular opinion: you don’t need fancy XXX features',
    exampleTip: '语气带点脾气但别极端；结尾邀请反驳，别写成软文。',
  },
  {
    value: 'guide',
    label: '硬核干货',
    desc: '伪装成发烧友整理的资源清单/指南，利他分享。你的产品排在第 3–4 位，客观一句带过。',
    exampleTitle: 'Spent 3 days compiling an XXX checklist / resource list',
    exampleTip: '不要吹第一名；最好只写名称不放追踪链接，让人自己搜。',
  },
  {
    value: 'help_seek',
    label: '场景求助',
    desc: '用极细分场景求推荐，利用“好为人师”。主帖只描述需求和失败尝试，不自答产品。',
    exampleTitle: 'Anyone know an XXX that works for [very specific scenario]?',
    exampleTip: '写清预算/限制，并说明试过 A/B 为何不行；产品可留给评论区或小号互动。',
    productNote: '主帖禁止点名自家产品。',
  },
]
const posts = ref<RedditPost[]>([])
const postsLoading = ref(false)
const postGenerating = ref(false)
const postStatusFilter = ref('')
const publishingPostId = ref<number | null>(null)

const commentInputTab = ref('smart')
const commentUrlForm = reactive({ target_post_url: '', include_site_url: false })
const commentSearchForm = reactive({ subreddit: '', keyword: '' })
const searchResults = ref<RedditDiscoverItem[]>([])
const selectedSearchPosts = ref<RedditDiscoverItem[]>([])
const searchLoading = ref(false)
const comments = ref<RedditComment[]>([])
const commentsLoading = ref(false)
const commentGenerating = ref(false)
const commentStatusFilter = ref('')
const publishingCommentId = ref<number | null>(null)

const postEditDialog = ref(false)
const postEditForm = reactive({ id: 0, title: '', body: '', subreddit: '' })
const postSaving = ref(false)
const commentEditDialog = ref(false)
const commentEditForm = reactive({ id: 0, body: '' })
const commentSaving = ref(false)

onMounted(async () => {
  if (route.query.reddit === 'error') ElMessage.error(`Reddit 连接失败：${route.query.message || ''}`)
  const tab = String(route.query.tab || '')
  if (['posts', 'comments', 'accounts', 'communities'].includes(tab)) {
    innerTab.value = tab
  }
  await Promise.all([loadStatus(), loadOverview(), loadCommunities(), loadBrands()])
  await Promise.all([loadPosts(), loadComments()])
  if (innerTab.value === 'accounts') loadProfiles()
})

watch(selectedAccountId, () => {
  loadPosts()
  loadComments()
  loadCommunities()
})

async function loadStatus() {
  status.value = await getRedditStatus()
  if (!selectedAccountId.value && status.value.accounts.length) {
    selectedAccountId.value = status.value.accounts[0].id
  }
}

async function loadPosts() {
  if (!selectedAccountId.value) {
    posts.value = []
    return
  }
  postsLoading.value = true
  try {
    posts.value = await listRedditPosts({
      ...(postStatusFilter.value ? { status: postStatusFilter.value } : {}),
      account_id: selectedAccountId.value,
    })
  } finally { postsLoading.value = false }
}

async function handleGeneratePost() {
  if (!selectedAccountId.value) { ElMessage.warning('请选择 Reddit 账号'); return }
  if (!postForm.subreddit) { ElMessage.warning('请选择或填写目标社区（优先人设社区）'); return }
  const sr = postForm.subreddit.replace(/^r\//i, '').toLowerCase()
  const memeSubs = new Set(['dankmemes', 'memes', 'me_irl', 'shitposting', 'okbuddyretard', 'comedyheaven'])
  if (memeSubs.has(sr)) {
    try {
      await ElMessageBox.confirm(
        `r/${sr} 偏梗图/沙雕，不太适合长文踩坑/干货。确定仍要生成吗？更建议换到与关键词相关的人设或产品社区。`,
        '版块可能不匹配',
        { type: 'warning', confirmButtonText: '仍要生成', cancelButtonText: '换社区' },
      )
    } catch {
      return
    }
  }
  postGenerating.value = true
  try {
    await generateRedditPost({
      account_id: selectedAccountId.value,
      post_type: postForm.post_type,
      subreddit: postForm.subreddit,
      keyword: (postForm.keyword || '').trim(),
      include_site_url: postForm.include_site_url,
    })
    ElMessage.success('已生成，请在审核队列中编辑/批准')
    loadPosts()
  } finally { postGenerating.value = false }
}

function openPostEdit(row: RedditPost) {
  postEditForm.id = row.id
  postEditForm.title = row.title
  postEditForm.body = row.body
  postEditForm.subreddit = row.subreddit
  postEditDialog.value = true
}

async function savePostEdit() {
  postSaving.value = true
  try {
    await updateRedditPost(postEditForm.id, {
      title: postEditForm.title, body: postEditForm.body, subreddit: postEditForm.subreddit,
    })
    ElMessage.success('已保存')
    postEditDialog.value = false
    loadPosts()
  } finally { postSaving.value = false }
}

async function approvePost(id: number) {
  await approveRedditPost(id)
  ElMessage.success('已批准')
  loadPosts()
}

async function rejectPost(id: number) {
  await rejectRedditPost(id)
  ElMessage.success('已拒绝')
  loadPosts()
}

async function publishPost(id: number) {
  publishingPostId.value = id
  try {
    const r = await publishRedditPost(id)
    if (r.status === 'posted') ElMessage.success('发布成功')
    else if (r.status === 'posting') ElMessage.info('正在发布中，请稍后刷新')
    else ElMessage.error(`发布失败：${r.error_message || r.status}`)
    loadPosts()
  } finally { publishingPostId.value = null }
}

async function deletePost(row: RedditPost) {
  const tip = row.status === 'posted'
    ? '将永久删除本地记录，不会撤回 Reddit 上已发布的帖子。确定删除？'
    : '将永久删除该发帖任务，确定删除？'
  try {
    await ElMessageBox.confirm(tip, '删除确认', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  await deleteRedditPost(row.id)
  ElMessage.success('已删除')
  loadPosts()
}

async function loadComments() {
  if (!selectedAccountId.value) {
    comments.value = []
    return
  }
  commentsLoading.value = true
  try {
    comments.value = await listRedditComments({
      ...(commentStatusFilter.value ? { status: commentStatusFilter.value } : {}),
      account_id: selectedAccountId.value,
    })
  } finally { commentsLoading.value = false }
}

async function handleSmartDiscover() {
  if (!selectedAccountId.value) { ElMessage.warning('请选择 Reddit 账号'); return }
  if (!smartDiscoverSubreddits.value.length) {
    ElMessage.warning('请至少选择一个评论社区')
    return
  }
  const promoNames = new Set(promoCommunities.value.map((c) => c.name.toLowerCase()))
  const selectedPromo = smartDiscoverSubreddits.value.some((n) => promoNames.has(n.toLowerCase()))
  if (selectedPromo && (!selectedBrandId.value || !selectedProductId.value)) {
    ElMessage.warning('选择了产品社区时请选择品牌和产品')
    return
  }
  if (selectedPromo && selectedProductId.value) {
    const product = productsOfSelectedBrand.value.find((p) => p.id === selectedProductId.value)
    if (!product || !(product.keywords || []).length) {
      ElMessage.warning('该产品尚未绑定关键词，请先在品牌/产品库中填写')
      return
    }
  }
  smartDiscovering.value = true
  try {
    const res = await smartDiscoverReddit({
      account_id: selectedAccountId.value,
      subreddits: smartDiscoverSubreddits.value,
      brand_id: selectedBrandId.value || undefined,
      product_id: selectedProductId.value || undefined,
    })
    const n = res.meta?.queued_count || 0
    if (n === 0) {
      ElMessage.warning('没有找到与产品相关的可评论讨论。请换社区或稍后再试。')
    } else {
      ElMessage.success(`已入队 ${n} 条待审评论`)
    }
    await Promise.all([loadComments(), loadOverview()])
    commentInputTab.value = 'smart'
  } finally { smartDiscovering.value = false }
}

async function handleGenerateCommentUrl() {
  if (!selectedAccountId.value || !commentUrlForm.target_post_url) {
    ElMessage.warning('请选择账号并填写 URL'); return
  }
  if (commentUrlForm.include_site_url && (!selectedBrandId.value || !selectedProductId.value)) {
    ElMessage.warning('产品向评论请选择品牌和产品'); return
  }
  commentGenerating.value = true
  try {
    await generateRedditComment({
      account_id: selectedAccountId.value,
      ...commentUrlForm,
      brand_id: selectedBrandId.value || undefined,
      product_id: selectedProductId.value || undefined,
    })
    ElMessage.success('评论已生成')
    loadComments()
  } finally { commentGenerating.value = false }
}

async function handleSearch() {
  if (!commentSearchForm.subreddit || !commentSearchForm.keyword) {
    ElMessage.warning('请填写子版块和关键词'); return
  }
  searchLoading.value = true
  try {
    const res = await searchRedditPosts({
      ...commentSearchForm,
      account_id: selectedAccountId.value || undefined,
    })
    searchResults.value = res.items
    selectedSearchPosts.value = []
  } finally { searchLoading.value = false }
}

function onSearchSelect(rows: RedditDiscoverItem[]) {
  selectedSearchPosts.value = rows
}

async function handleGenerateCommentBatch() {
  if (!selectedAccountId.value) return
  commentGenerating.value = true
  try {
    await generateRedditCommentBatch({
      account_id: selectedAccountId.value,
      subreddit: commentSearchForm.subreddit,
      keyword: commentSearchForm.keyword,
      posts: selectedSearchPosts.value.map((p) => ({
        url: p.url,
        title: p.title,
        body: p.body || '',
      })),
    })
    ElMessage.success(`已为 ${selectedSearchPosts.value.length} 帖生成评论`)
    loadComments()
  } finally { commentGenerating.value = false }
}

function openCommentEdit(row: RedditComment) {
  commentEditForm.id = row.id
  commentEditForm.body = row.body
  commentEditDialog.value = true
}

async function saveCommentEdit() {
  commentSaving.value = true
  try {
    await updateRedditComment(commentEditForm.id, { body: commentEditForm.body })
    ElMessage.success('已保存')
    commentEditDialog.value = false
    loadComments()
  } finally { commentSaving.value = false }
}

async function approveComment(id: number) {
  await approveRedditComment(id)
  ElMessage.success('已批准')
  loadComments()
}

async function rejectComment(id: number) {
  await rejectRedditComment(id)
  ElMessage.success('已拒绝')
  loadComments()
}

async function publishComment(id: number) {
  publishingCommentId.value = id
  try {
    const r = await publishRedditComment(id)
    if (r.status === 'posted') ElMessage.success('评论发布成功')
    else if (r.status === 'posting') ElMessage.info('正在发布中，请稍后刷新')
    else ElMessage.error(`发布失败：${r.error_message || r.status}`)
    loadComments()
  } finally { publishingCommentId.value = null }
}

async function deleteComment(row: RedditComment) {
  const tip = row.status === 'posted'
    ? '将永久删除本地记录，不会撤回 Reddit 上已发布的评论。确定删除？'
    : '将永久删除该评论任务，确定删除？'
  try {
    await ElMessageBox.confirm(tip, '删除确认', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  await deleteRedditComment(row.id)
  ElMessage.success('已删除')
  loadComments()
}

function statusLabel(s: string) {
  return { pending_review: '待审核', approved: '已批准', posted: '已发布', failed: '失败', rejected: '已拒绝' }[s] || s
}
function statusTag(s: string): any {
  return { pending_review: 'warning', approved: 'primary', posted: 'success', failed: 'danger' }[s] || 'info'
}
function postTypeLabel(t: string) {
  return {
    pitfall: '踩坑',
    vent: '树洞',
    unpopular: '暴论',
    guide: '干货',
    help_seek: '求助',
    consultation: '答疑',
    experience: '测评',
    comparison: '对比',
  }[t] || t
}
function formatTime(s?: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
function roleLabel(r?: string | null) {
  return { warmup: '养号号', seeding: '种草号', expert: '答疑号' }[r || ''] || r || '-'
}
function roleTag(r?: string | null): any {
  return { warmup: 'info', seeding: 'primary', expert: 'warning' }[r || ''] || 'info'
}
function stageLabel(s?: string | null) {
  return {
    warmup_week1_2: '养号 1-2 周', warmup_week3_4: '养号 3-4 周',
    ready: '已就绪', active: '稳定运营', warning: '预警', suspended: '已暂停',
  }[s || ''] || s || '-'
}
function intentLabel(v?: string | null) {
  return v === 'promo' ? '产品' : '人设'
}
function sourceLabel(v?: string | null) {
  return { auto_discover: '自动', keyword_search: '搜索', manual_url: 'URL' }[v || ''] || v || '-'
}
function splitComma(text: string) {
  return text.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
}

function weekdayLabel(d: number) {
  return weekdayNames[d] ?? String(d)
}

// ===== 概览 / 账号矩阵 =====
async function loadOverview() {
  try { overview.value = await getRedditOverview() } catch { /* 忽略 */ }
}

async function loadProfiles() {
  profilesLoading.value = true
  try { profiles.value = await listAccountProfiles() } finally { profilesLoading.value = false }
}

function openProfileEdit(row: RedditAccount) {
  const cfg = row.persona_config || {}
  profileForm.accountId = row.id
  profileForm.role = row.role || 'warmup'
  profileForm.stage = row.stage || 'warmup_week1_2'
  profileForm.karma = row.karma || 0
  profileForm.persona = row.persona || ''
  profileForm.voice = cfg.voice || row.persona || ''
  profileForm.interestsText = (cfg.interests || []).join(', ')
  profileForm.quirks = cfg.quirks || ''
  profileForm.never_say = cfg.never_say || ''
  profileForm.daily_post_limit = row.daily_post_limit ?? 0
  profileForm.daily_comment_limit = row.daily_comment_limit ?? 10
  profileDialog.value = true
}

async function saveProfile() {
  profileSaving.value = true
  try {
    const voice = profileForm.voice.trim()
    await updateAccountProfile(profileForm.accountId, {
      role: profileForm.role as any,
      stage: profileForm.stage as any,
      karma: profileForm.karma,
      persona: voice || undefined,
      persona_config: {
        voice,
        interests: splitComma(profileForm.interestsText),
        quirks: profileForm.quirks,
        never_say: profileForm.never_say,
      },
      daily_post_limit: profileForm.daily_post_limit,
      daily_comment_limit: profileForm.daily_comment_limit,
    })
    ElMessage.success('账号配置已保存')
    profileDialog.value = false
    loadProfiles()
    loadStatus()
  } finally { profileSaving.value = false }
}

async function clearWarning(row: RedditAccount) {
  await updateAccountProfile(row.id, { clear_warning: true })
  ElMessage.success('已解除预警，账号恢复营销动作')
  loadProfiles()
}

// ===== 社区库 =====
async function loadCommunities() {
  communitiesLoading.value = true
  try {
    communities.value = await listCommunities(selectedAccountId.value || undefined)
  } finally { communitiesLoading.value = false }
}

async function loadBrands() {
  brands.value = await listBrands()
  if (!selectedBrandId.value && brands.value.length) {
    const first = brands.value.find((b) => b.is_active !== false) || brands.value[0]
    selectedBrandId.value = first.id
    const prod = (first.products || []).find((p) => p.is_active !== false)
    selectedProductId.value = prod?.id ?? null
  }
  applyProductBoundCommunities()
}

function selectedBrandName() {
  return brands.value.find((b) => b.id === selectedBrandId.value)?.name || ''
}

function productLabel(p: RedditBrandProduct) {
  return p.category ? `${p.name} (${p.category})` : p.name
}

function applyProductBoundCommunities() {
  const p = productsOfSelectedBrand.value.find((x) => x.id === selectedProductId.value)
  if (!p) return
  const names = (p.community_names || []).filter(Boolean)
  if (names.length) {
    smartDiscoverSubreddits.value = [...names]
    return
  }
  const ids = new Set(p.community_ids || [])
  if (!ids.size) return
  smartDiscoverSubreddits.value = communities.value
    .filter((c) => ids.has(c.id) && c.is_active)
    .map((c) => c.name)
}

function onBrandChange() {
  selectedProductId.value = null
  const first = productsOfSelectedBrand.value[0]
  if (first) selectedProductId.value = first.id
  applyProductBoundCommunities()
}

function onProductChange() {
  applyProductBoundCommunities()
}

async function handleSuggestCommunities() {
  if (!selectedAccountId.value) { ElMessage.warning('请先选择 Reddit 账号'); return }
  suggesting.value = true
  try {
    const r = await suggestPersonaCommunities({ account_id: selectedAccountId.value })
    ElMessage.success(`建议 ${r.suggested.length} 个社区，新增 ${r.added} 个`)
    await loadCommunities()
  } finally { suggesting.value = false }
}

function openCommunityEdit(row?: RedditCommunity) {
  if (row) {
    Object.assign(communityForm, {
      id: row.id, name: row.name, category: row.category, purpose: row.purpose || 'persona', rules_note: row.rules_note || '',
      allows_links: row.allows_links, promo_weekday: row.promo_weekday ?? undefined,
      daily_post_limit: row.daily_post_limit, best_hour_utc: row.best_hour_utc ?? undefined,
      priority: row.priority, is_active: row.is_active,
    })
  } else {
    Object.assign(communityForm, {
      id: 0, name: '', category: 'core', purpose: 'persona', rules_note: '', allows_links: true,
      promo_weekday: undefined, daily_post_limit: 1, best_hour_utc: undefined, priority: 3, is_active: true,
    })
  }
  communityDialog.value = true
}

async function saveCommunity() {
  if (!communityForm.name?.trim()) { ElMessage.warning('请填写社区名'); return }
  communitySaving.value = true
  try {
    const payload = {
      ...communityForm,
      name: communityForm.name.trim().replace(/^r\//i, ''),
      account_id: communityForm.purpose === 'persona' ? selectedAccountId.value : null,
    }
    if (communityForm.purpose === 'persona' && !selectedAccountId.value) {
      ElMessage.warning('请先选择 Reddit 账号')
      return
    }
    if (communityForm.id) {
      await updateCommunity(communityForm.id, payload)
    } else {
      await createCommunity(payload)
    }
    ElMessage.success('已保存')
    communityDialog.value = false
    loadCommunities()
  } finally { communitySaving.value = false }
}

async function removeCommunity(row: RedditCommunity) {
  try {
    await ElMessageBox.confirm(`从社区库删除 r/${row.name}？`, '删除确认', { type: 'warning' })
  } catch { return }
  await deleteCommunity(row.id)
  ElMessage.success('已删除')
  loadCommunities()
}

// ===== 定时发布 / 数据同步 =====
function openSchedule(row: RedditPost) {
  scheduleTargetPost = row
  scheduleTargetComment = null
  scheduleTime.value = ''
  scheduleDialog.value = true
}

function openCommentSchedule(row: RedditComment) {
  scheduleTargetComment = row
  scheduleTargetPost = null
  scheduleTime.value = ''
  scheduleDialog.value = true
}

async function saveSchedule() {
  if (!scheduleTime.value) { ElMessage.warning('请选择发布时间'); return }
  if (!scheduleTargetPost && !scheduleTargetComment) { ElMessage.warning('请选择发布时间'); return }
  scheduleSaving.value = true
  try {
    if (scheduleTargetPost) {
      await scheduleRedditPost(scheduleTargetPost.id, scheduleTime.value)
      loadPosts()
    } else if (scheduleTargetComment) {
      await scheduleRedditComment(scheduleTargetComment.id, scheduleTime.value)
      loadComments()
    }
    ElMessage.success('已设置定时发布，到期由系统自动发布')
    scheduleDialog.value = false
    loadOverview()
  } finally { scheduleSaving.value = false }
}

async function cancelCommentSchedule(row: RedditComment) {
  await cancelRedditCommentSchedule(row.id)
  ElMessage.success('已取消定时')
  loadComments()
  loadOverview()
}

async function cancelSchedule(row: RedditPost) {
  await cancelRedditPostSchedule(row.id)
  ElMessage.success('已取消定时')
  loadPosts()
  loadOverview()
}

async function syncMetrics(row: RedditPost) {
  try {
    const list = await syncPostMetrics(row.id)
    if (list.length) {
      const latest = list[list.length - 1]
      ElMessage.success(`数据已同步：Score ${latest.score} / 评论 ${latest.num_comments}`)
    } else {
      ElMessage.warning('暂未同步到数据（帖子可能未被搜索到）')
    }
    loadOverview()
  } catch { /* 拦截器已提示 */ }
}

// Tab 切换懒加载
function onTabChange(name: string | number) {
  if (name === 'accounts' && !profiles.value.length) loadProfiles()
  if (name === 'communities' && !communities.value.length) loadCommunities()
  if (name === 'engage') {
    if (!communities.value.length) loadCommunities()
    if (!engageSubreddit.value) {
      engageSubreddit.value = personaCommunities.value[0]?.name || promoCommunities.value[0]?.name || ''
    }
  }
}
</script>

<style lang="scss" scoped>
.section-card { margin-bottom: 0; }
.card-title { font-weight: 600; }
.card-title-row { display: flex; justify-content: space-between; align-items: center; }
.account-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.list-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-weight: 600; }
.stat { text-align: center; padding: 6px 0; }
.stat-num { font-size: 22px; font-weight: 700; line-height: 1.3; }
.stat-label { font-size: 12px; color: #909399; }
.stat-danger { color: #f56c6c; }
.muted { color: #909399; }
.post-type-radios {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  align-items: center;
}
.post-type-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.post-type-help {
  color: #909399;
  cursor: help;
  font-size: 14px;
  vertical-align: middle;
}
.post-type-help:hover { color: #409eff; }
.post-type-help-body {
  font-size: 13px;
  line-height: 1.55;
  color: #303133;
  p { margin: 0 0 8px; }
  p:last-child { margin-bottom: 0; }
  .help-title { font-weight: 600; margin-bottom: 6px; }
  .help-k {
    display: inline-block;
    min-width: 3em;
    color: #909399;
    margin-right: 4px;
  }
  .help-warn { color: #e6a23c; }
}
</style>
