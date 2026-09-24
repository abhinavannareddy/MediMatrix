"""
Validation Microservice

Checks that a diabetes-prediction feature vector is well-formed and
clinically plausible before the backend spends a model inference on it.
"""

from flask import Flask, jsonify, request

app = Flask(__name__)

# (field name, min, max) in the order the frontend/backend send them.
FEATURE_RULES = [
    ("pregnancies", 0, 20),
    ("glucose", 0, 300),
    ("blood_pressure", 0, 200),
    ("skin_thickness", 0, 100),
    ("insulin", 0, 900),
    ("bmi", 0, 70),
    ("dpf", 0, 3),
    ("age", 0, 120),
]


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "validation"})


@app.route("/validate", methods=["POST"])
def validate():
    data = request.json or {}
    features = data.get("features")
    errors = []

    if not isinstance(features, list):
        return jsonify({"valid": False, "errors": ["features must be a list"]}), 400

    if len(features) != len(FEATURE_RULES):
        errors.append(f"expected {len(FEATURE_RULES)} features, got {len(features)}")
    else:
        for value, (name, low, high) in zip(features, FEATURE_RULES):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"{name} must be numeric")
                continue
            if value < low or value > high:
                errors.append(f"{name} must be between {low} and {high} (got {value})")

    if errors:
        return jsonify({"valid": False, "errors": errors}), 400

    return jsonify({"valid": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5004)
