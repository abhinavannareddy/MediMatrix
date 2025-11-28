from flask import Flask, render_template, request
import requests

app = Flask(__name__)

BACKEND_URL = "http://backend:5001/predict"

@app.route("/", methods=["GET", "POST"])
def home():
    prediction = None
    if request.method == "POST":
        try:
            # Collect form data
            features = [
                float(request.form.get(feature))
                for feature in ["pregnancies", "glucose", "blood_pressure", "skin_thickness", "insulin", "bmi", "dpf", "age"]
            ]

            response = requests.post(BACKEND_URL, json={"features": features})
            response_data = response.json()

            if "prediction" in response_data:
                prediction = response_data["prediction"]
            else:
                prediction = response_data["error"]
        except Exception as e:
            prediction = f"Error: {str(e)}"

    # Render the HTML template and pass the prediction value
    return render_template("index.html", prediction=prediction)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
