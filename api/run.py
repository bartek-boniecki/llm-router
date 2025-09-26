# api/run.py
# Safe launcher for the FastAPI app.
# - Prints a big banner on start
# - Imports api.main:app and starts Uvicorn
# - If import/start fails, prints full traceback and DOES NOT exit immediately,
#   so `docker compose logs -f api` always shows the reason.

import os
import sys
import time
import traceback

def main():
    print("============================================================")
    print("🚀 Starting LLM Router (api.run) — safe launcher")
    print("PYTHONPATH:", os.getenv("PYTHONPATH"))
    print("CWD       :", os.getcwd())
    print("============================================================", flush=True)

    try:
        # Import here so we can catch any import-time errors
        from api.main import app  # noqa: F401
        print("✅ api.run: import api.main:app OK", flush=True)

        # Start uvicorn programmatically
        import uvicorn
        host = os.getenv("HOST", "0.0.0.0")
        port = int(os.getenv("PORT", "8000"))
        print(f"🔈 Uvicorn serving on http://{host}:{port}", flush=True)
        uvicorn.run("api.main:app", host=host, port=port, log_level="info")
    except Exception as e:
        print("❌ api.run: FAILED to start the server", flush=True)
        traceback.print_exc()
        print("------------------------------------------------------------", flush=True)
        print("The process will stay alive so you can read this traceback in docker logs.", flush=True)
        print("Fix the error above, then rebuild & restart.", flush=True)
        # Keep container alive for inspection
        while True:
            time.sleep(60)

if __name__ == "__main__":
    main()
