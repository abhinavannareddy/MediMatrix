"""
Authorization Microservice

Decodes the JWT issued by the authentication-service and checks whether
the caller's role is permitted to perform the requested action. Kept
separate from authentication so identity and permission checks can each
evolve (and scale) independently.
"""

import os

import jwt
from flask import Flask, jsonify, request

app = Flask(__name__)

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"

# role -> set of actions it may perform
ROLE_PERMISSIONS = {
    "user": {"predict"},
    "admin": {"predict", "manage_users"},
}


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "authorization"})


@app.route("/authorize", methods=["POST"])
def authorize():
    data = request.json or {}
    token = data.get("token") or ""
    action = data.get("action")

    if not token:
        return jsonify({"authorized": False, "error": "missing token"}), 401

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return jsonify({"authorized": False, "error": "token expired"}), 401
    except jwt.InvalidTokenError:
        return jsonify({"authorized": False, "error": "invalid token"}), 401

    role = payload.get("role", "user")
    allowed_actions = ROLE_PERMISSIONS.get(role, set())

    if action not in allowed_actions:
        return jsonify({
            "authorized": False,
            "error": f"role '{role}' may not perform '{action}'",
        }), 403

    return jsonify({
        "authorized": True,
        "username": payload.get("sub"),
        "user_id": payload.get("user_id"),
        "role": role,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5005)
