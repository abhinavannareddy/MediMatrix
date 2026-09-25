# Auth service.
# Registers staff accounts, checks passwords, and hands out signed tokens
# that the other services can trust without ever seeing a password.
# Owns two collections in MongoDB: users, and verification_codes (used on
# behalf of the verification service, see /internal/codes below).

import os
import socket
import logging
import json
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from bson import ObjectId
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

# --------------------------------------------------------------------------
# Logging: one JSON object per line, same style as every other service.
# --------------------------------------------------------------------------
POD = os.environ.get("POD_NAME", socket.gethostname())
SERVICE = "auth"


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

# --------------------------------------------------------------------------
# Configuration - all from environment variables, never hard-coded.
# --------------------------------------------------------------------------
MONGO_USER = os.environ.get("MONGO_USER", "medimatrx")
MONGO_PASSWORD = os.environ.get("MONGO_PASSWORD", "devpassword")
MONGO_HOST = os.environ.get("MONGO_HOST", "localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "medimatrx")
MONGO_URI = f"mongodb://{MONGO_USER}:{MONGO_PASSWORD}@{MONGO_HOST}/?authSource=admin"

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_MINUTES = int(os.environ.get("JWT_EXPIRY_MINUTES", "60"))

# Shared secret that only auth, verification and the gateway know. It gates
# the /internal/* endpoints so that even a pod that can reach auth-service
# over the network (see the NetworkPolicy) still cannot forge a
# verification result without also knowing this value.
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "dev-internal-token-change-me")

VERIFICATION_URL = os.environ.get("VERIFICATION_URL", "http://verification-service:8080")
UPSTREAM_TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "6.0"))

VALID_ROLES = {"staff", "viewer"}

app = FastAPI(
    title="MediMatrx Auth Service",
    description="Registers staff accounts, checks credentials, issues JWTs.",
    version="1.0.0",
)

# pymongo's client is a connection pool, not a single connection - it
# reconnects on its own if MongoDB restarts. We intentionally use the
# synchronous driver here rather than motor: this service sees login and
# registration traffic, not the high-frequency reads the rest of the
# platform does, so the small amount of blocking per request is an
# accepted trade-off for one less dependency to version-pin.
_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
_db = _client[MONGO_DB]
_users = _db["users"]
_codes = _db["verification_codes"]


def ensure_indexes():
    _users.create_index([("username", ASCENDING)], unique=True)
    _users.create_index([("email", ASCENDING)], unique=True)
    _codes.create_index([("user_id", ASCENDING)])


# ==========================================================================
#  Password hashing - PBKDF2-HMAC-SHA256 with a random salt per user.
#  No extra dependency (bcrypt/argon2 need a compiled C extension); the
#  standard library's hashlib is enough for a coursework-scale user base.
# ==========================================================================
PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return hmac.compare_digest(candidate.hex(), digest_hex)


def check_internal_token(x_internal_token: str | None):
    if not x_internal_token or not hmac.compare_digest(x_internal_token, INTERNAL_TOKEN):
        raise HTTPException(status_code=403, detail="invalid internal token")


# ==========================================================================
#  API models
# ==========================================================================
class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_.-]+$")
    email: str = Field(min_length=5, max_length=120)
    password: str = Field(min_length=8, max_length=200)
    # "staff" can load demo data and act on the platform; "viewer" is
    # read-only. Letting the caller pick this at registration is a
    # deliberate demo simplification - a real deployment would have an
    # administrator approve and assign roles instead.
    role: str = Field(default="staff")


class LoginRequest(BaseModel):
    username: str
    password: str


class StoreCodeRequest(BaseModel):
    user_id: str
    code: str
    expires_at: datetime


class CheckCodeRequest(BaseModel):
    user_id: str
    code: str


# ==========================================================================
#  HEALTH ENDPOINTS
# ==========================================================================
@app.get("/healthz")
async def healthz():
    return {"status": "alive", "service": SERVICE, "pod": POD}


@app.get("/readyz")
async def readyz():
    try:
        _client.admin.command("ping")
    except PyMongoError as exc:
        return JSONResponse(status_code=503, content={"status": "not-ready", "reason": str(exc), "pod": POD})
    return {"status": "ready", "service": SERVICE, "pod": POD}


# ==========================================================================
#  REST API
# ==========================================================================
@app.post("/api/register", status_code=201)
async def register(req: RegisterRequest):
    if req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {sorted(VALID_ROLES)}")

    doc = {
        "username": req.username,
        "email": req.email,
        "password_hash": hash_password(req.password),
        "role": req.role,
        "verified": False,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        result = _users.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="username or email already registered")

    user_id = str(result.inserted_id)

    # Ask the verification-service to mint a one-time code. It will call
    # us back on /internal/codes to store it - auth-service stays the only
    # writer of its own collections.
    verification_code = None
    try:
        async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
            resp = await client.post(f"{VERIFICATION_URL}/api/generate", json={"user_id": user_id})
            resp.raise_for_status()
            verification_code = resp.json().get("code")
    except httpx.HTTPError as exc:
        log.warning(f"verification-service unreachable while registering {req.username}: {exc}")
        return JSONResponse(status_code=202, content={
            "user_id": user_id,
            "message": "registered, but verification-service is unreachable - ask the user to retry verification",
        })

    log.info(f"registered user {req.username} (role={req.role})")
    return {
        "user_id": user_id,
        "message": "registration successful, verification required",
        # Returned only because there is no mail server in this demo setup.
        "verification_code": verification_code,
    }


@app.post("/api/login")
async def login(req: LoginRequest):
    user = _users.find_one({"username": req.username})
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid username or password")
    if not user.get("verified"):
        raise HTTPException(status_code=403, detail="account not verified")

    payload = {
        "sub": user["username"],
        "user_id": str(user["_id"]),
        "role": user["role"],
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRY_MINUTES),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    log.info(f"login ok for {req.username}")
    return {"token": token, "username": user["username"], "role": user["role"]}


# ==========================================================================
#  INTERNAL API - only verification-service is meant to call these.
#  Reachability is also restricted at the network layer (see
#  08-network-policy.yaml); the shared token is a second, independent
#  check, so a network-policy misconfiguration alone is not enough to
#  forge a verified account.
# ==========================================================================
@app.post("/internal/codes")
async def store_code(req: StoreCodeRequest, x_internal_token: str | None = Header(default=None)):
    check_internal_token(x_internal_token)
    _codes.insert_one({
        "user_id": req.user_id,
        "code": req.code,
        "expires_at": req.expires_at,
        "used": False,
        "created_at": datetime.now(timezone.utc),
    })
    return {"stored": True}


@app.post("/internal/codes/check")
async def check_code(req: CheckCodeRequest, x_internal_token: str | None = Header(default=None)):
    check_internal_token(x_internal_token)

    record = _codes.find_one(
        {"user_id": req.user_id, "code": req.code, "used": False},
        sort=[("created_at", -1)],
    )
    if not record:
        return {"valid": False, "reason": "no matching unused code"}

    expires_at = record["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        return {"valid": False, "reason": "code expired"}

    _codes.update_one({"_id": record["_id"]}, {"$set": {"used": True}})
    _users.update_one({"_id": ObjectId(req.user_id)}, {"$set": {"verified": True}})

    log.info(f"user {req.user_id} verified")
    return {"valid": True}


@app.exception_handler(Exception)
async def unhandled(request, exc):
    log.error(f"unhandled error on {request.url.path}: {exc}")
    return JSONResponse(status_code=500, content={"error": "internal error", "pod": POD})


@app.on_event("startup")
async def startup():
    try:
        ensure_indexes()
        log.info("Auth service started, indexes ensured.")
    except PyMongoError as exc:
        log.warning(f"could not ensure indexes at startup (will retry lazily): {exc}")
