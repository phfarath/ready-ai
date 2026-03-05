import requests
import time
import subprocess
import os
import signal

def test_api():
    print("Starting API server...")
    proc = subprocess.Popen(["python3", "main.py", "api", "--port", "8001"])
    time.sleep(3) # wait for server to start
    
    try:
        print("Testing POST /runs...")
        res = requests.post("http://localhost:8001/runs", json={
            "goal": "Test API",
            "url": "http://example.com",
            "model": "gpt-4o-mini"
        })
        print(f"POST status: {res.status_code}")
        assert res.status_code == 200
        data = res.json()
        print(f"POST response: {data}")
        run_id = data.get("run_id")
        assert run_id is not None
        
        print(f"Testing GET /runs/{run_id}...")
        res_get = requests.get(f"http://localhost:8001/runs/{run_id}")
        assert res_get.status_code == 200
        data_get = res_get.json()
        print(f"GET response: {data_get}")
        assert data_get.get("status") == "PLANNING"
        
        print("All basic API tests passed!")
    finally:
        print("Shutting down API server...")
        try:
             os.kill(proc.pid, signal.SIGTERM)
        except Exception as e:
             pass

if __name__ == "__main__":
    test_api()
