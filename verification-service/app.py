"""
Verification Microservice

Issues and checks one-time account verification codes. There is no real
mail server in this demo setup, so a freshly generated code is handed
back in the /generate response instead of being emailed out.
"""

import os
import random
import datetime

import psycopg2
from flask import Flask, jsonify, request

app = Flask(__name__)

CODE_EXPIRY_MINUTES = int(os.environ.get("CODE_EXPIRY_MINUTES", "15"))


def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "db"),
        database=os.environ.get("DB_NAME", "microservices_db"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "password"),
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "verification"})


@app.route("/generate", methods=["POST"])
def generate():
    data = request.json or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400

    code = f"{random.randint(0, 999999):06d}"
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=CODE_EXPIRY_MINUTES)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO verification_codes (user_id, code, expires_at, verified)
        VALUES (%s, %s, %s, FALSE);
        """,
        (user_id, code, expires_at),
    )
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"user_id": user_id, "code": code, "expires_at": expires_at.isoformat()}), 201


@app.route("/confirm", methods=["POST"])
def confirm():
    data = request.json or {}
    user_id = data.get("user_id")
    code = data.get("code")
    if not user_id or not code:
        return jsonify({"error": "user_id and code are required"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, expires_at, verified FROM verification_codes
        WHERE user_id = %s AND code = %s
        ORDER BY created_at DESC LIMIT 1;
        """,
        (user_id, code),
    )
    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        return jsonify({"verified": False, "error": "invalid code"}), 400

    code_id, expires_at, already_verified = row
    if already_verified:
        cursor.close()
        conn.close()
        return jsonify({"verified": True, "message": "already verified"})

    if datetime.datetime.utcnow() > expires_at:
        cursor.close()
        conn.close()
        return jsonify({"verified": False, "error": "code expired"}), 400

    cursor.execute("UPDATE verification_codes SET verified = TRUE WHERE id = %s;", (code_id,))
    cursor.execute("UPDATE users SET is_verified = TRUE WHERE id = %s;", (user_id,))
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"verified": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5003)
