from typing import Optional
from fastapi import APIRouter, Query

from services.news_intelligence.news_service import news_service

router = APIRouter(prefix="/api/v1/news", tags=["News Intelligence"])


@router.get("/feed")
async def get_news_feed(
    category: Optional[str] = Query(default="all", description="Kategori filtresi: all, wire, kripto, onchain, macro, ai, regulation"),
    force: bool = Query(default=False, description="Önbelleği zorla tazele")
):
    """
    Canlı haber, on-chain ve küresel istihbarat akışı.
    """
    data = await news_service.get_live_news(force_refresh=force, category_filter=category)
    return data


@router.get("/categories")
async def get_categories():
    """
    Mevcut istihbarat kategorileri listesi.
    """
    return {
        "categories": [
            {"id": "all", "name": "⚡ Tümü (All Alpha)"},
            {"id": "wire", "name": "🚨 Flaş & Wire"},
            {"id": "kripto", "name": "🪙 Kripto & Altcoin"},
            {"id": "onchain", "name": "🔗 On-Chain & Balina"},
            {"id": "macro", "name": "🏛️ Makro & Fed"},
            {"id": "ai", "name": "🤖 Yapay Zeka & Çip"},
            {"id": "regulation", "name": "⚖️ Regülasyon & SEC"}
        ]
    }
