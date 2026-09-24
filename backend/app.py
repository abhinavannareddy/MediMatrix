"""
Diabetes Prediction API Backend

This Flask application provides a REST API endpoint for diabetes prediction.
It loads a trained machine learning model and accepts patient health data,
then returns predictions and stores results in a PostgreSQL database.

Every /predict call is gated by two other microservices:
  - authorization-service: checks the caller's JWT and role.
  - validation-service: checks the input feature vector is well-formed.
"""

import os

from flask import Flask, jsonify, request
import joblib
import numpy as np
import psycopg2
import requests

# Initialize Flask application
app = Flask(__name__)

# Load the pre-trained diabetes prediction model from disk
model = joblib.load("diabetes_model.pkl")

AUTHORIZATION_SERVICE_URL = os.environ.get(
    "AUTHORIZATION_SERVICE_URL", "http://authorization-service:5005"
)
VALIDATION_SERVICE_URL = os.environ.get(
    "VALIDATION_SERVICE_URL", "http://validation-service:5004"
)


def get_db_connection():
    """
    Establish a connection to the PostgreSQL database.

    Returns:
        psycopg2 connection object to the microservices database
    """
    conn = psycopg2.connect(
        host=os.environ.get("DB_HOST", "db"),
        database=os.environ.get("DB_NAME", "microservices_db"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "password"),
    )
    return conn


def authorize_request(token):
    """Ask the authorization-service whether this token may run a prediction."""
    try:
        resp = requests.post(
            f"{AUTHORIZATION_SERVICE_URL}/authorize",
            json={"token": token, "action": "predict"},
            timeout=5,
        )
    except requests.RequestException as exc:
        return None, {"error": f"authorization-service unreachable: {exc}"}, 503

    data = resp.json()
    if not data.get("authorized"):
        return None, data, resp.status_code
    return data, None, None


def validate_features(features):
    """Ask the validation-service whether the feature vector is acceptable."""
    try:
        resp = requests.post(
            f"{VALIDATION_SERVICE_URL}/validate",
            json={"features": features},
            timeout=5,
        )
    except requests.RequestException as exc:
        return {"error": f"validation-service unreachable: {exc}"}, 503

    data = resp.json()
    if not data.get("valid"):
        return data, resp.status_code
    return None, None


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "backend"})


@app.route("/predict", methods=["POST"])
def predict():
    """
    Handle POST requests for diabetes prediction.

    Requires an "Authorization: Bearer <token>" header, checked against the
    authorization-service, and a JSON payload of the form:
        {
            "features": [pregnancies, glucose, blood_pressure, skin_thickness, insulin, bmi, dpf, age]
        }

    Returns:
        JSON response with prediction result (Diabetic/Non-Diabetic) or error message
    """
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.split(" ", 1)[1] if auth_header.startswith("Bearer ") else auth_header

    auth_data, auth_error, auth_status = authorize_request(token)
    if auth_error:
        return jsonify(auth_error), auth_status or 401

    data = request.json or {}
    features = data.get("features", [])

    validation_error, validation_status = validate_features(features)
    if validation_error:
        return jsonify(validation_error), validation_status or 400

    try:
        # Convert input features list to NumPy array and reshape for model input
        # Append two placeholder values (0.0) for missing features like Gender and Smoking Status
        # Reshape to (1, -1) for single sample prediction
        input_features = np.array(features + [0.0, 0.0]).reshape(1, -1)

        # Generate prediction using the trained model
        # Output is 1 (Diabetic) or 0 (Non-Diabetic)
        prediction = model.predict(input_features)[0]
        result = "Diabetic" if prediction == 1 else "Non-Diabetic"

        # Store prediction result in the database for record-keeping and analytics
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO predictions (username, pregnancies, glucose, blood_pressure, skin_thickness, insulin, bmi, dpf, age, prediction_result)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (auth_data.get("username"), *features, result),
        )
        # Commit the transaction to save changes to the database
        conn.commit()
        # Clean up database resources
        cursor.close()
        conn.close()

        # Return prediction result as JSON
        return jsonify({"prediction": result})
    except Exception as e:
        # Handle any errors and return error message
        return jsonify({"error": str(e)})


# Entry point: Run Flask development server
if __name__ == "__main__":
    # Start the Flask server on all network interfaces at port 5001
    app.run(host="0.0.0.0", port=5001)
