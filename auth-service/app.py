"""
Authentication Microservice

Owns user identity: registration, credential checks and JWT issuance.
Newly registered accounts start unverified; the verification-service
flips them to verified once the user confirms their one-time code.
"""

import os
import datetime

import jwt
import psycopg2
import requests
from flask import Flask, jsonify, request
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_MINUTES = int(os.environ.get("JWT_EXPIRY_MINUTES", "60"))

VERIFICATION_SERVICE_URL = os.environ.get(
    "VERIFICATION_SERVICE_URL", "http://verification-service:5003"
)


def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "db"),
        database=os.environ.get("DB_NAME", "microservices_db"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "password"),
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "authentication"})


@app.route("/register", methods=["POST"])
def register():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""

    if not username or not email or not password:
        return jsonify({"error": "username, email and password are required"}), 400

    password_hash = generate_password_hash(password)

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (username, email, password_hash, role, is_verified)
            VALUES (%s, %s, %s, 'user', FALSE)
            RETURNING id;
            """,
            (username, email, password_hash),
        )
        user_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"error": "username or email already registered"}), 409
    finally:
        conn.close()

    # Kick off account verification via the verification-service.
    code = None
    try:
        resp = requests.post(
            f"{VERIFICATION_SERVICE_URL}/generate",
            json={"user_id": user_id},
            timeout=5,
        )
        if resp.ok:
            code = resp.json().get("code")
    except requests.RequestException as exc:
        return jsonify({
            "message": "user registered but verification-service is unreachable",
            "user_id": user_id,
            "error": str(exc),
        }), 202

    return jsonify({
        "message": "registration successful, verification required",
        "user_id": user_id,
        # Returned only because there is no real mail server in this demo setup.
        "verification_code": code,
    }), 201


@app.route("/login", methods=["POST"])
def login():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, password_hash, role, is_verified FROM users WHERE username = %s;",
        (username,),
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row or not check_password_hash(row[1], password):
        return jsonify({"error": "invalid username or password"}), 401

    user_id, _password_hash, role, is_verified = row
    if not is_verified:
        return jsonify({"error": "account not verified"}), 403

    payload = {
        "sub": username,
        "user_id": user_id,
        "role": role,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=JWT_EXPIRY_MINUTES),
        "iat": datetime.datetime.utcnow(),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    return jsonify({"token": token, "username": username, "role": role})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002)
