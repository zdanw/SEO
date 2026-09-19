# Reddit 社区硬门槛校验（方案 3 → C：Zernio + LLM）

日期：2026-09-19  
范围：存在性 + 活跃度 + 人设匹配（建议社区主路径）。

## 主路径（suggest / 带账号 create）

1. AI/回退表生成候选 subreddit 名  
2. 用当前账号 Zernio：`list_feed` + `search_posts`（无 search 则客户端自行回退 feed）收集证据：条数、近 7 日帖、标题样本、错误  
3. **一次批量**交给 LLM：判定 `exists` / `active` / `persona_fit`，仅 `ok=true`（三者皆真）入库并 `is_active=True`  
4. 无 AI 时用启发式（有帖 + 标题/名称沾边兴趣）  
5. 理由写入 `verify_error`，分数写入 `activity_score`

## 弱回退

无 Zernio 账号时仍可走公开 `about.json`（常 403 → `exists=None` 软放行，仅作兜底）。

## 封锁说明

Reddit 公开接口 403 **不得**当作社区不存在。主路径已改走 Zernio。

## 非目标

版规全文抓取、精细评分模型训练（后续可增强）。
