"""
Diabetes Prediction Model Training Script

This module trains a Random Forest classifier on diabetes data and saves
the trained model for inference. The model predicts diabetes outcomes based
on patient health indicators.
"""

import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
import pickle


def load_and_prepare_data(filepath: str):
    """
    Load the diabetes dataset and prepare features and target variables.
    
    Args:
        filepath: Path to the CSV file containing diabetes data
        
    Returns:
        X: Feature matrix
        y: Target variable (Outcome)
    """
    # Read the CSV file
    df = pd.read_csv(filepath)
    print(f"Dataset shape: {df.shape}")
    print(df.head())
    
    # Extract features and target variable
    X = df.drop(columns=["Outcome"])
    y = df["Outcome"]
    
    return X, y


def preprocess_features(X: pd.DataFrame) -> pd.DataFrame:
    """
    Standardize features using z-score normalization.
    
    Args:
        X: Feature matrix
        
    Returns:
        Normalized feature matrix
    """
    # Apply z-score normalization (standardization)
    X_normalized = (X - X.mean()) / X.std()
    return X_normalized


def train_model(X_train, y_train):
    """
    Train a Random Forest classifier on the training data.
    
    Args:
        X_train: Training features
        y_train: Training target values
        
    Returns:
        Trained Random Forest model
    """
    # Initialize and train the Random Forest classifier
    model = RandomForestClassifier(n_estimators=100, random_state=42)
    model.fit(X_train, y_train)
    return model


def evaluate_model(model, X_test, y_test):
    """
    Evaluate model performance on test data.
    
    Args:
        model: Trained classifier
        X_test: Test features
        y_test: Test target values
        
    Returns:
        Accuracy score
    """
    # Generate predictions and calculate accuracy
    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    return accuracy


def save_model(model, filepath: str):
    """
    Save the trained model to a pickle file for later use.
    
    Args:
        model: Trained classifier to save
        filepath: Path where the model will be saved
    """
    # Serialize and save the model
    with open(filepath, "wb") as f:
        pickle.dump(model, f)
    print(f"Model successfully saved to '{filepath}'")


# Main execution
if __name__ == "__main__":
    # Load and prepare data
    X, y = load_and_prepare_data("diabetes.csv")
    
    # Normalize features
    X_normalized = preprocess_features(X)
    
    # Split data into training and test sets (80/20 split)
    X_train, X_test, y_train, y_test = train_test_split(
        X_normalized, y, test_size=0.2, random_state=42
    )
    
    # Train the model
    model = train_model(X_train, y_train)
    
    # Evaluate the model
    accuracy = evaluate_model(model, X_test, y_test)
    print(f"Model Accuracy: {accuracy:.2f}")
    
    # Save the trained model
    save_model(model, "diabetes_model.pkl")
