# Authorization service.
# Decodes the token auth-service issued and checks if the caller is allowed
# to do what they're asking. Kept separate from auth on purpose - who you
# are and what you're allowed to do are different questions. Stateless,
# no data of its own besides the signing secret.

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
