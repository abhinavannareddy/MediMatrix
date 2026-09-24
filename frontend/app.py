import os

from flask import Flask, render_template, request, redirect, url_for, session
import requests

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:5001")
AUTH_SERVICE_URL = os.environ.get("AUTH_SERVICE_URL", "http://auth-service:5002")
VERIFICATION_SERVICE_URL = os.environ.get(
    "VERIFICATION_SERVICE_URL", "http://verification-service:5003"
)


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    info = None
    if request.method == "POST":
        payload = {
            "username": request.form.get("username", "").strip(),
            "email": request.form.get("email", "").strip(),
            "password": request.form.get("password", ""),
        }
        try:
            resp = requests.post(f"{AUTH_SERVICE_URL}/register", json=payload, timeout=5)
            data = resp.json()
        except requests.RequestException as exc:
            error = f"auth-service unreachable: {exc}"
        else:
            if resp.status_code == 201:
                return redirect(url_for("verify", user_id=data.get("user_id"), code=data.get("verification_code")))
            error = data.get("error", "registration failed")

    return render_template("register.html", error=error, info=info)


@app.route("/verify", methods=["GET", "POST"])
def verify():
    error = None
    info = None
    user_id = request.values.get("user_id", "")
    prefill_code = request.values.get("code", "")

    if request.method == "POST":
        payload = {
            "user_id": request.form.get("user_id"),
            "code": request.form.get("code", "").strip(),
        }
        try:
            resp = requests.post(f"{VERIFICATION_SERVICE_URL}/confirm", json=payload, timeout=5)
            data = resp.json()
        except requests.RequestException as exc:
            error = f"verification-service unreachable: {exc}"
        else:
            if data.get("verified"):
                return redirect(url_for("login", verified="1"))
            error = data.get("error", "verification failed")
        user_id = payload["user_id"]

    return render_template("verify.html", error=error, info=info, user_id=user_id, prefill_code=prefill_code)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    info = "Account verified, you can log in now." if request.args.get("verified") else None

    if request.method == "POST":
        payload = {
            "username": request.form.get("username", "").strip(),
            "password": request.form.get("password", ""),
        }
        try:
            resp = requests.post(f"{AUTH_SERVICE_URL}/login", json=payload, timeout=5)
            data = resp.json()
        except requests.RequestException as exc:
            error = f"auth-service unreachable: {exc}"
        else:
            if resp.status_code == 200:
                session["token"] = data["token"]
                session["username"] = data["username"]
                return redirect(url_for("home"))
            error = data.get("error", "login failed")

    return render_template("login.html", error=error, info=info)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
def home():
    if "token" not in session:
        return redirect(url_for("login"))

    prediction = None
    if request.method == "POST":
        try:
            # Collect form data
            features = [
                float(request.form.get(feature))
                for feature in ["pregnancies", "glucose", "blood_pressure", "skin_thickness", "insulin", "bmi", "dpf", "age"]
            ]

            response = requests.post(
                f"{BACKEND_URL}/predict",
                json={"features": features},
                headers={"Authorization": f"Bearer {session['token']}"},
                timeout=10,
            )
            response_data = response.json()

            if response.status_code == 401:
                # Token expired/invalid - send the user back to log in.
                session.clear()
                return redirect(url_for("login"))

            if "prediction" in response_data:
                prediction = response_data["prediction"]
            else:
                message = response_data.get("error") or response_data.get("errors") or "prediction failed"
                prediction = ", ".join(message) if isinstance(message, list) else message
        except Exception as e:
            prediction = f"Error: {str(e)}"

    # Render the HTML template and pass the prediction value
    return render_template("index.html", prediction=prediction, username=session.get("username"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
