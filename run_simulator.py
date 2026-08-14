#!/usr/bin/env python3
"""Start the local simulator backend (the frontend is served when built)."""
import uvicorn


if __name__ == "__main__":
    uvicorn.run("backend.run:app", host="127.0.0.1", port=8000, reload=False)
