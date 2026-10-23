"""POST /api/place/ask — ask an LLM about one place."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.models.place_ask import PlaceAskRequest, PlaceAskResponse
from app.services.llm_client import seconds_until_budget_reset
from app.services.llm_support import LLMUnavailableError
from app.services.place_ask import ask_place

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["place-ask"])

_CIRCUIT_RETRY_AFTER_S = 60


@router.post("/place/ask", response_model=PlaceAskResponse)
async def ask_about_place(req: PlaceAskRequest) -> PlaceAskResponse:
    """Answer a question about the given place ("tell me its history", "worth it in rain?").

    Grounded in the supplied facts, honest about what is unknown, and immune to instructions
    hidden in the place fields or question. Identical questions are cached for 7 days.
    Returns 503 when the AI is unavailable (no key, daily budget spent, service down).
    """
    try:
        answer = await ask_place(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="The question is empty.") from exc
    except LLMUnavailableError as exc:
        headers = {}
        if exc.reason == "budget":
            headers["Retry-After"] = str(seconds_until_budget_reset())
        elif exc.reason == "circuit_open":
            headers["Retry-After"] = str(_CIRCUIT_RETRY_AFTER_S)
        raise HTTPException(status_code=503, detail=exc.message, headers=headers) from exc
    except Exception as exc:
        logger.exception("Ask-the-place error")
        raise HTTPException(status_code=500, detail="Could not answer the question") from exc
    return PlaceAskResponse(answer=answer)
