"""
===========================================================================
 MediMatrx - AUTHORIZATION SERVICE
---------------------------------------------------------------------------
 Job in one sentence:
    "I decode the token auth-service issued and answer one question:
     is this caller allowed to do that?"

 Authentication (auth-service) and authorization (this service) are kept
 apart on purpose. Proving who somebody is and deciding what they may do
 are different concerns with different failure modes - a permissions
 change should never require touching the code that verifies passwords,
 and vice versa. The gateway calls this service, on every request to a
 protected route, before it proxies anywhere else.

 This service is completely stateless: it holds the signing secret (via
 the Secret, never in source) but no data of its own, so it scales
 horizontally with zero coordination between replicas.
===========================================================================
"""

import os
import socket
import logging
import json
from datetime import datetime, timezone

import jwt
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

POD = os.environ.get("POD_NAME", socket.gethostname())
SERVICE = "authorization"


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "service": SERVICE,
            "pod": POD,
            "message": record.getMessage(),
        })


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
log = logging.getLogger(SERVICE)
log.setLevel(logging.INFO)
log.handlers = [handler]
log.propagate = False

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"

# role -> the actions it may perform. "read" covers every GET the
# dashboard makes; "write" covers the two actions that change state
# (loading demo data, storing a meter reading). Everyone who registers
# gets to choose "staff" or "viewer" at sign-up - see auth-service.
ROLE_PERMISSIONS = {
    "staff": {"read", "write"},
    "viewer": {"read"},
}

app = FastAPI(
    title="MediMatrx Authorization Service",
    description="Checks a JWT and a role against the action being attempted.",
    version="1.0.0",
)

_stats = {"allowed": 0, "denied": 0}


class AuthorizeRequest(BaseModel):
    token: str
    action: str


@app.get("/healthz")
async def healthz():
    return {"status": "alive", "service": SERVICE, "pod": POD}


@app.get("/readyz")
async def readyz():
    return {"status": "ready", "service": SERVICE, "pod": POD, "stats": _stats}


@app.post("/api/authorize")
async def authorize(req: AuthorizeRequest):
    try:
        payload = jwt.decode(req.token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        _stats["denied"] += 1
        return JSONResponse(status_code=401, content={"authorized": False, "error": "token expired"})
    except jwt.InvalidTokenError:
        _stats["denied"] += 1
        return JSONResponse(status_code=401, content={"authorized": False, "error": "invalid token"})

    role = payload.get("role", "viewer")
    allowed_actions = ROLE_PERMISSIONS.get(role, set())

    if req.action not in allowed_actions:
        _stats["denied"] += 1
        return JSONResponse(status_code=403, content={
            "authorized": False,
            "error": f"role '{role}' may not perform '{req.action}'",
        })

    _stats["allowed"] += 1
    return {
        "authorized": True,
        "username": payload.get("sub"),
        "user_id": payload.get("user_id"),
        "role": role,
    }


@app.exception_handler(Exception)
async def unhandled(request, exc):
    log.error(f"unhandled error on {request.url.path}: {exc}")
    return JSONResponse(status_code=500, content={"error": "internal error", "pod": POD})


@app.on_event("startup")
async def startup():
    log.info("Authorization service started.")
