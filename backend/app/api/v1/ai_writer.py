"""AI 写作 API：调用 DeepSeek / Agnes 生成文章、Meta、社交文案。"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import SiteContext, get_current_user, require_site_write
from app.core.database import get_db
from app.models.user import User
from app.models.article import Article
from app.schemas.article import (
    AIArticleRequest,
    AIArticleResponse,
    AIMetaRequest,
    AISocialCopyRequest,
    AISocialCopyResponse,
    ArticleOut,
)
from app.services.ai_writer import DeepSeekError, get_ai_client

router = APIRouter()


@router.post(
    "/generate",
    response_model=AIArticleResponse,
    summary="AI 生成文章初稿（仅返回内容，不入库）",
)
def generate_article(
    payload: AIArticleRequest,
    current_user: User = Depends(get_current_user),
) -> AIArticleResponse:
    try:
        client = get_ai_client(provider=payload.model)
        draft = client.generate_article(
            keyword=payload.keyword,
            outline=payload.outline or "",
            audience=payload.audience,
            tone=payload.tone,
        )
        return AIArticleResponse(
            title=draft.title,
            meta_description=draft.meta_description,
            content=draft.content,
            keywords_suggested=draft.keywords_suggested,
        )
    except DeepSeekError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI 生成失败：{e}",
        )


@router.post(
    "/generate-and-save",
    response_model=ArticleOut,
    status_code=status.HTTP_201_CREATED,
    summary="AI 生成文章初稿并入库（状态 = ai_generated）",
)
def generate_and_save(
    payload: AIArticleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    ctx: SiteContext = Depends(require_site_write),
) -> Article:
    try:
        client = get_ai_client(provider=payload.model)
        draft = client.generate_article(
            keyword=payload.keyword,
            outline=payload.outline or "",
            audience=payload.audience,
            tone=payload.tone,
        )
    except DeepSeekError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI 生成失败：{e}",
        )

    article = Article(
        user_id=current_user.id,
        site_id=ctx.site.id,
        title=draft.title,
        meta_description=draft.meta_description,
        content=draft.content,
        status="ai_generated",
    )
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


@router.post(
    "/meta",
    response_model=dict,
    summary="基于正文重新生成 Meta Description",
)
def regenerate_meta(
    payload: AIMetaRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    try:
        client = get_ai_client()
        meta = client.generate_meta_description(
            keyword=payload.keyword, content=payload.content
        )
        return {"meta_description": meta}
    except DeepSeekError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI 生成失败：{e}",
        )


@router.post(
    "/social-copy",
    response_model=AISocialCopyResponse,
    summary="为指定平台生成社交分发差异化文案",
)
def generate_social_copy(
    payload: AISocialCopyRequest,
    current_user: User = Depends(get_current_user),
) -> AISocialCopyResponse:
    try:
        client = get_ai_client()
        copy = client.generate_social_copy(
            platform=payload.platform,
            article_title=payload.article_title,
            keyword=payload.keyword,
            summary=payload.summary,
        )
        return AISocialCopyResponse(
            platform=copy.platform,
            title=copy.title,
            summary=copy.summary,
            hashtags=copy.hashtags,
        )
    except DeepSeekError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI 生成失败：{e}",
        )
