"""
Diabetes Prediction API Backend

This Flask application provides a REST API endpoint for diabetes prediction.
It loads a trained machine learning model and accepts patient health data,
then returns predictions and stores results in a PostgreSQL database.
"""

from flask import Flask, jsonify, request
import joblib
import numpy as np
import psycopg2

# Initialize Flask application
app = Flask(__name__)

# Load the pre-trained diabetes prediction model from disk
model = joblib.load("diabetes_model.pkl")


def get_db_connection():
    """
    Establish a connection to the PostgreSQL database.
    
    Returns:
        psycopg2 connection object to the microservices database
    """
    conn = psycopg2.connect(
        host="db",  # Database container hostname
        database="microservices_db",  # Database name
        user="postgres",  # Database user
        password="password"  # Database password
    )
    return conn


@app.route("/predict", methods=["POST"])
def predict():
    """
    Handle POST requests for diabetes prediction.
    
    Expected JSON payload:
        {
            "features": [pregnancies, glucose, blood_pressure, skin_thickness, insulin, bmi, dpf, age]
        }
    
    Returns:
        JSON response with prediction result (Diabetic/Non-Diabetic) or error message
    """
    data = request.json
    try:
        # Convert input features list to NumPy array and reshape for model input
        # Append two placeholder values (0.0) for missing features like Gender and Smoking Status
        # Reshape to (1, -1) for single sample prediction
        input_features = np.array(data["features"] + [0.0, 0.0]).reshape(1, -1)

        # Generate prediction using the trained model
        # Output is 1 (Diabetic) or 0 (Non-Diabetic)
        prediction = model.predict(input_features)[0]
        result = "Diabetic" if prediction == 1 else "Non-Diabetic"

        # Store prediction result in the database for record-keeping and analytics
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO predictions (pregnancies, glucose, blood_pressure, skin_thickness, insulin, bmi, dpf, age, prediction_result)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (*data["features"], result)
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
