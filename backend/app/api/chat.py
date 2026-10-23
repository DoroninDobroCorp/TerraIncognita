"""Story 2.1 — Chat API endpoint.

POST /api/chat — natural language discovery with conversational AI.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from app.models.chat import ChatRequest, ChatResponse
from app.services.llm_discovery import process_chat
from app.utils.safe_route import SafeValidationRoute

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"], route_class=SafeValidationRoute)


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Natural language place discovery.

    Send a message like "хочу заброшку у воды" or "something creepy nearby"
    and get AI-curated place recommendations with conversational context.
    """
    t0 = time.monotonic()
    try:
        result = await process_chat(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as exc:
        logger.exception("Chat pipeline error")
        raise HTTPException(status_code=500, detail="Chat pipeline failed") from exc

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "POST /api/chat → %d places, lang=%s in %.0fms",
        len(result.places), result.language, elapsed_ms,
    )
    return result
