"""Vercel serverless entrypoint.

Vercel's Python runtime looks for a module-level ASGI/WSGI callable named `app` in this file.
The actual FastAPI application lives in src/main.py; this file just re-exports it so Vercel's
builder (configured in vercel.json) has a single entry point to point at.
"""

from src.main import app  # noqa: F401
