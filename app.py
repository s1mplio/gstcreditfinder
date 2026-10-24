"""Vercel entry point. Vercel's FastAPI support sends every request here."""

from gst_credit_finder.web import app

__all__ = ["app"]
