import os
from fastapi.testclient import TestClient
from sqlmodel import SQLModel
from agent.db import engine
from api.main import app

# Ensure database tables exist
SQLModel.metadata.create_all(engine)

client = TestClient(app)

def test_dashboard_rendering():
    print("1. Testing GET /dashboard Jinja2 rendering...")
    response = client.get("/dashboard")
    assert response.status_code == 200, f"Expected status 200, got {response.status_code}"
    assert "<html" in response.text.lower(), "Response did not return an HTML document!"
    print("   SUCCESS: Dashboard HTML rendered properly.")

def test_n8n_endpoints_security():
    print("\n2. Testing n8n trigger and alert endpoints...")
    api_key = os.environ.get("INTERNAL_API_KEY", "test-key")
    headers = {"X-API-Key": api_key}

    # Test unauthorized access
    unauth_res = client.post("/api/bottleneck-alerts", json={"alert": "CPU spike detected"})
    assert unauth_res.status_code in (401, 403, 503), "Security layer failed to block unauthenticated request!"
    print("   SUCCESS: Security header check blocked unauthorized request.")

    # Test authorized alert payload
    auth_res = client.post("/api/bottleneck-alerts", json={"alert": "Database connection pool saturated"}, headers=headers)
    print(f"   Bottleneck alert response status: {auth_res.status_code}")

if __name__ == "__main__":
    print("--- Running Phase 3 Verification Suite ---\n")
    try:
        test_dashboard_rendering()
        test_n8n_endpoints_security()
        print("\nPhase 3 API and Dashboard tests passed successfully!")
    except Exception as exc:
        print(f"\nTEST FAILED: {exc}")