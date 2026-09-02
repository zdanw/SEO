"""社区互动 API：生成"像真人"的评论草稿供人工审核。"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_site_context
from app.core.database import get_db
from app.models.user import User
from app.models.article import Article
from app.services.ai_writer import DeepSeekError, get_ai_client
from app.schemas.social import CommunityCommentsResponse, CommunityCommentDraft

router = APIRouter()


class CommentDraftRequest(BaseModel):
    article_id: int
    platforms: list[str] = Field(
        default=["Reddit", "PulseForge"],
        description="目标平台列表",
    )
    count: int = Field(default=3, ge=1, le=5)


@router.post(
    "/comments-draft",
    response_model=CommunityCommentsResponse,
    summary="为文章生成社区互动评论草稿",
)
def generate_comment_drafts(
    payload: CommentDraftRequest,
    db: Session = Depends(get_db),
    ctx: SiteContext = Depends(get_site_context),
) -> CommunityCommentsResponse:
    article = (
        db.query(Article)
        .filter(Article.id == payload.article_id, Article.site_id == ctx.site.id)
        .first()
    )
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")

    try:
        client = get_ai_client()
    except DeepSeekError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI 服务不可用：{e}",
        )

    keyword = article.keyword.keyword if article.keyword else article.title
    drafts: list[CommunityCommentDraft] = []

    for platform in payload.platforms:
        for i in range(payload.count):
            prompt = f"""你是 {platform} 平台的一位真实用户，刚看了这篇关于「{keyword}」的文章：
《{article.title}》

文章摘要：{(article.meta_description or (article.content or "")[:200])}

请以第一人称写一条 50-120 字的评论，要求：
- 像真人：有具体细节、分享个人经验或提一个相关问题
- 避免硬广：不要直接推荐或夸大
- 语气自然：{('Reddit 风格：直率、技术性强、可能略带质疑' if platform == 'Reddit' else 'PulseForge 风格：友善、有见地')}
- 第 {i+1} 条：请在视角/侧重点上与其他几条不同

只输出评论内容，不要任何前缀或解释。"""

            try:
                text = client.chat(prompt, temperature=0.85, max_tokens=200).strip()
                drafts.append(CommunityCommentDraft(
                    platform=platform,
                    article_title=article.title,
                    comment=text,
                    tone="helpful",
                ))
            except DeepSeekError:
                continue

    return CommunityCommentsResponse(article_id=payload.article_id, comments=drafts)
