"""
Astra Fill Routes — with Voice Transcript Confirmation Gate

Voice Safety Architecture (Two-Phase):
  PHASE 1  POST /transcribe-voice      STT only -> returns raw transcript + pending_id
                                       Flutter shows: 'I heard: "...". Correct? [Yes/Edit]'
  PHASE 2  POST /confirm-transcript    Patient confirms/edits -> AI extraction runs
  
  PHASE 1  POST /transcribe-chat       STT only -> returns raw transcript for voice chat
  PHASE 2  POST /confirm-chat          Patient confirms -> transcript sent to AI Brain

  The old /process-voice endpoint is preserved for backward compatibility but now
  internally uses the confirmation gate with auto-confirm=True for non-medical text.
  New clients MUST use the two-phase flow for medical data.
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from typing import Optional, Dict, Any

from app.astra_fill.models import (
    AstraFillExtraction,
    AstraFillConfirmationRequest,
    HealthIntakeSchema,
    VoiceTranscriptPending,
    VoiceTranscriptConfirmation,
    VoiceChatTranscriptPending,
    VoiceChatConfirmation,
)
from app.astra_fill.service import astra_fill_service
from app.security.auth.dependencies import require_patient, require_patient_or_doctor
import logging

router = APIRouter(prefix="/astra-fill", tags=["Astra Fill"])
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — Voice Transcription Endpoints (STT Only, No AI Processing)
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/transcribe-voice",
    response_model=VoiceTranscriptPending,
    summary="[PHASE 1] Transcribe voice to text for patient confirmation"
)
async def transcribe_voice(
    audio: UploadFile = File(...),
    user_id: str = Form(...),
    language_code: str = Form("en-IN"),
    user: Dict[str, Any] = Depends(require_patient())
):
    """
    PHASE 1 of the voice safety gate for the Astra Fill intake process.

    Runs Speech-to-Text ONLY. Does NOT process the transcript through the AI
    extraction pipeline. Returns the raw transcript with a `pending_id` and
    a ready-to-display `ui_prompt` for the Flutter confirmation card.

    The Flutter UI MUST show:
        'I heard: "<transcript>". Is this correct? [Yes / Edit]'
    
    The patient must tap [Yes] or edit before calling /confirm-transcript.
    This prevents noise-induced transcription errors from entering medical records.
    """
    try:
        audio_data = await audio.read()
        pending = await astra_fill_service.transcribe_voice_for_confirmation(
            audio_data=audio_data,
            user_id=user_id,
            language_code=language_code
        )
        return pending
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:

        raise

    except Exception as e:
        logger.error(f"[VoiceGate] Phase 1 transcription failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/transcribe-chat",
    response_model=VoiceChatTranscriptPending,
    summary="[PHASE 1] Transcribe voice for chat confirmation"
)
async def transcribe_chat_voice(
    audio: UploadFile = File(...),
    user_id: str = Form(...),
    language_code: str = Form("en-IN"),
    user: Dict[str, Any] = Depends(require_patient())
):
    """
    PHASE 1 of the voice safety gate for the AI companion voice chat.

    Runs Speech-to-Text ONLY. Does NOT send the transcript to the AI Brain.
    Returns the raw transcript for the Flutter confirmation card before
    the message is routed to the AI.
    """
    try:
        audio_data = await audio.read()
        pending = await astra_fill_service.transcribe_voice_for_chat_confirmation(
            audio_data=audio_data,
            user_id=user_id,
            language_code=language_code
        )
        return pending
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:

        raise

    except Exception as e:
        logger.error(f"[VoiceGate] Chat Phase 1 transcription failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — Confirmation Endpoints (AI Processing after Human Approval)
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/confirm-transcript",
    response_model=Dict[str, Any],
    summary="[PHASE 2] Process confirmed voice transcript through AI extraction"
)
async def confirm_transcript(request: VoiceTranscriptConfirmation, user: Dict[str, Any] = Depends(require_patient())):
    """
    PHASE 2 of the voice safety gate for Astra Fill intake.

    Called ONLY after the patient has confirmed (or edited) the transcript
    shown on the Flutter confirmation card.

    - `user_confirmed=True`:  AI extraction runs on the verified transcript.
    - `user_confirmed=False`: Transcript is discarded. No AI runs.
    - If the `pending_id` has expired (>10 minutes), the request is rejected
      and the patient must re-record.
    """
    try:
        result = await astra_fill_service.confirm_and_process_voice(request)
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:

        raise

    except Exception as e:
        logger.error(f"[VoiceGate] Phase 2 confirm-transcript failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/confirm-chat",
    response_model=Dict[str, Any],
    summary="[PHASE 2] Send confirmed transcript to AI Brain for voice chat"
)
async def confirm_chat_voice(request: VoiceChatConfirmation, user: Dict[str, Any] = Depends(require_patient())):
    """
    PHASE 2 of the voice safety gate for AI companion voice chat.

    Called ONLY after the patient confirms the transcript on the Flutter card.
    Sends the verified transcript to the AI Brain and returns the response.
    """
    try:
        result = await astra_fill_service.confirm_and_chat_voice(request)
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:

        raise

    except Exception as e:
        logger.error(f"[VoiceGate] Phase 2 confirm-chat failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# LEGACY Endpoints (backward-compatible, not recommended for new clients)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/process-voice", response_model=Dict[str, Any], deprecated=True)
async def process_voice(
    audio: UploadFile = File(...),
    user_id: str = Form(...),
    language_code: str = Form("en-IN"),
    user: Dict[str, Any] = Depends(require_patient())
):
    """
    [DEPRECATED] Use /transcribe-voice + /confirm-transcript instead.
    Processes voice directly without human confirmation of the transcript.
    """
    try:
        audio_data = await audio.read()
        extraction, extraction_id = await astra_fill_service.process_voice_intake(
            audio_data, user_id, language_code=language_code
        )
        return {
            "status": "success",
            "extraction_id": extraction_id,
            "data": extraction.model_dump() if hasattr(extraction, "model_dump") else extraction.dict(),
            "warning": "This endpoint is deprecated. Use /transcribe-voice + /confirm-transcript for safe voice intake."
        }
    except HTTPException:

        raise

    except Exception as e:
        logger.error(f"Voice fill failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/process-text", response_model=Dict[str, Any])
async def process_text(
    text: str = Form(...),
    user_id: str = Form(...),
    user: Dict[str, Any] = Depends(require_patient())
):
    """
    Process text intake via Astra Fill Service.
    Returns structured health extraction.
    """
    try:
        extraction, extraction_id = await astra_fill_service.process_text_intake(text, user_id)
        return {
            "status": "success",
            "extraction_id": extraction_id,
            "data": extraction.model_dump() if hasattr(extraction, "model_dump") else extraction.dict()
        }
    except HTTPException:

        raise

    except Exception as e:
        logger.error(f"Text fill failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/voice-assistant", response_model=Dict[str, Any], deprecated=True)
async def voice_assistant(
    audio: UploadFile = File(...),
    user_id: str = Form(...),
    language_code: str = Form("en-IN"),
    user: Dict[str, Any] = Depends(require_patient())
):
    """
    [DEPRECATED] Use /transcribe-chat + /confirm-chat instead.
    Standard Voice Assistant relay without human transcript confirmation.
    """
    try:
        audio_data = await audio.read()
        result = await astra_fill_service.process_voice_chat(
            audio_data, user_id, language_code=language_code
        )
        return result
    except HTTPException:

        raise

    except Exception as e:
        logger.error(f"Voice assistant relay failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm")
async def confirm_extraction(request: AstraFillConfirmationRequest, user: Dict[str, Any] = Depends(require_patient())):
    """
    Confirm and apply the extracted health details.
    """
    success = await astra_fill_service.confirm_extraction(request)
    if not success:
        raise HTTPException(status_code=400, detail="Confirmation failed or extraction expired")
    return {"status": "success", "message": "Health details confirmed and saved"}


@router.get("/patient/{user_id}/latest")
async def get_latest_intake(user_id: str, user: Dict[str, Any] = Depends(require_patient_or_doctor())):
    """Get the most recent health intake for a patient."""
    data = await astra_fill_service.get_latest_fill(user_id)
    if not data:
        return {}
    return data


@router.get("/patient/{user_id}/history")
async def get_intake_history(user_id: str, user: Dict[str, Any] = Depends(require_patient_or_doctor())):
    """Get the full health intake history for a patient."""
    history = await astra_fill_service.get_fill_history(user_id)
    return history


@router.get("/patient/{user_id}/records")
async def get_intake_records(user_id: str, user: Dict[str, Any] = Depends(require_patient_or_doctor())):
    """Alias for history, used by some app versions."""
    history = await astra_fill_service.get_fill_history(user_id)
    return {"records": history}
