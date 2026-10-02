"""Unified FastAPI dependencies for Astra auth."""

import logging
from typing import Any, Dict, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .exceptions import ExpiredTokenError, InsufficientRoleError, InvalidTokenError, RevokedTokenError
from .firebase_service import verify_firebase_token
from .jwt_service import validate_role, verify_access_token

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)

def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ExpiredTokenError):
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    if isinstance(exc, RevokedTokenError):
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    if isinstance(exc, InsufficientRoleError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token")

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization header missing")
    try:
        return verify_access_token(credentials.credentials)
    except Exception as exc:
        raise _http_error(exc) from exc

def _role_dependency(roles: list[str]):
    async def dependency(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
        try:
            return validate_role(current_user, roles)
        except Exception as exc:
            raise _http_error(exc) from exc
    return dependency

def require_admin():
    return _role_dependency(["admin", "superadmin"])

def require_doctor():
    return _role_dependency(["doctor", "admin", "superadmin"])

def require_patient():
    return _role_dependency(["patient", "admin", "superadmin"])

def require_patient_or_doctor():
    # A separate dependency rather than widening require_patient() itself:
    # require_patient() is reused by routes that are genuinely patient-only
    # (treatment_centers, image_analysis, patient_management) and must stay
    # that way. Use this one specifically for routes where a doctor reading
    # their own patient's data is a legitimate case - e.g. astra-fill's
    # GET /patient/{user_id}/latest|history|records, which a doctor's own
    # JWT (role: "doctor") could never pass before this existed.
    return _role_dependency(["patient", "doctor", "admin", "superadmin"])

async def require_firebase_auth(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization header missing")
    return verify_firebase_token(credentials.credentials)

async def require_admin_auth(current_user: Dict[str, Any] = Depends(require_admin())) -> Dict[str, Any]:
    return current_user

async def require_doctor_auth(current_user: Dict[str, Any] = Depends(require_doctor())) -> Dict[str, Any]:
    return current_user

async def require_patient_auth(current_user: Dict[str, Any] = Depends(require_patient())) -> Dict[str, Any]:
    return current_user

async def optional_current_user(request: Request) -> Optional[Dict[str, Any]]:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    try:
        return verify_access_token(auth_header.split(" ", 1)[1])
    except Exception:
        return None
