"""
Start the GST Credit Finder website.

    python run_web.py

Then open http://localhost:8080
"""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "gst_credit_finder.web:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8080")),
        reload=os.getenv("RELOAD", "1") == "1",
    )
