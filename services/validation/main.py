"""
===========================================================================
 MediMatrx - VALIDATION SERVICE
---------------------------------------------------------------------------
 Job in one sentence:
    "I check that what a client is trying to send actually makes sense,
     before it reaches a service that would have to trust it."

 This is the assignment report's own stated mitigation for injection
 attacks: "validate and sanitize all incoming data on the backend, and
 apply strict schema validation." The gateway calls this service for the
 two kinds of input a human or a device can submit - a meter reading and
 a new account - so a malformed or out-of-range request is rejected here,
 by name, instead of failing confusingly three services downstream.

 ingest and auth-service still do their own basic checks too. That is
 deliberate defence in depth, not duplication to be removed: this service
 can be skipped or fail closed without silently disabling the checks that
 protect the data those two services actually own.
===========================================================================
"""

import os
import re
import socket
import logging
import json
import time
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

POD = os.environ.get("POD_NAME", socket.gethostname())
SERVICE = "validation"


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

INGEST_URL = os.environ.get("INGEST_URL", "http://ingest-service:8080")
UPSTREAM_TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "6.0"))
ZONES_CACHE_TTL_SECONDS = int(os.environ.get("ZONES_CACHE_TTL_SECONDS", "300"))

# The same nine zone IDs ingest ships with (services/ingest/zones.js). Used
# only if ingest cannot be reached when the cache is cold - graceful
# degradation, the same pattern price-service uses for its price curve.
FALLBACK_ZONE_IDS = {
    "icu", "theatres", "imaging", "wards", "hvac",
    "sterilisation", "laundry", "catering", "admin",
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = FastAPI(
    title="MediMatrx Validation Service",
    description="Validates meter readings and registration input before they reach their owning service.",
    version="1.0.0",
)

_zone_cache: dict = {"fetched_at": 0.0, "ids": None}
_stats = {"reading_checks": 0, "reading_rejections": 0, "registration_checks": 0, "registration_rejections": 0}


async def known_zone_ids() -> set[str]:
    now = time.time()
    if _zone_cache["ids"] is not None and (now - _zone_cache["fetched_at"]) < ZONES_CACHE_TTL_SECONDS:
        return _zone_cache["ids"]

    try:
        async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
            resp = await client.get(f"{INGEST_URL}/api/zones")
            resp.raise_for_status()
            ids = {z["zoneId"] for z in resp.json().get("zones", [])}
        if ids:
            _zone_cache["ids"] = ids
            _zone_cache["fetched_at"] = now
            return ids
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning(f"could not fetch zones from ingest, using fallback list: {exc}")

    return _zone_cache["ids"] or FALLBACK_ZONE_IDS


class ReadingCheck(BaseModel):
    zoneId: str | None = None
    kwh: float | None = None
    ts: str | None = None


class RegistrationCheck(BaseModel):
    username: str | None = None
    email: str | None = None
    password: str | None = None


@app.get("/healthz")
async def healthz():
    return {"status": "alive", "service": SERVICE, "pod": POD}


@app.get("/readyz")
async def readyz():
    return {"status": "ready", "service": SERVICE, "pod": POD, "stats": _stats}


@app.post("/api/validate/reading")
async def validate_reading(body: ReadingCheck):
    _stats["reading_checks"] += 1
    errors = []

    zones = await known_zone_ids()
    if not body.zoneId or body.zoneId not in zones:
        errors.append(f"zoneId must be one of {sorted(zones)}")

    if body.kwh is None:
        errors.append("kwh is required")
    elif not (0 <= body.kwh <= 100000):
        errors.append("kwh must be between 0 and 100000")

    if body.ts is not None:
        try:
            datetime.fromisoformat(body.ts.replace("Z", "+00:00"))
        except ValueError:
            errors.append("ts must be a valid ISO-8601 timestamp")

    if errors:
        _stats["reading_rejections"] += 1
        return JSONResponse(status_code=400, content={"valid": False, "errors": errors})
    return {"valid": True}


@app.post("/api/validate/registration")
async def validate_registration(body: RegistrationCheck):
    _stats["registration_checks"] += 1
    errors = []

    if not body.username or not re.match(r"^[a-zA-Z0-9_.-]{3,32}$", body.username):
        errors.append("username must be 3-32 characters: letters, numbers, dot, dash or underscore")

    if not body.email or not EMAIL_RE.match(body.email):
        errors.append("email must be a valid address")

    if not body.password or len(body.password) < 8:
        errors.append("password must be at least 8 characters")

    if errors:
        _stats["registration_rejections"] += 1
        return JSONResponse(status_code=400, content={"valid": False, "errors": errors})
    return {"valid": True}


@app.exception_handler(Exception)
async def unhandled(request, exc):
    log.error(f"unhandled error on {request.url.path}: {exc}")
    return JSONResponse(status_code=500, content={"error": "internal error", "pod": POD})


@app.on_event("startup")
async def startup():
    log.info(f"Validation service started. ingest={INGEST_URL}")
