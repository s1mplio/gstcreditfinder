"""Vercel entry point: every request is routed here (see vercel.json)."""

from gst_credit_finder.web import app

__all__ = ["app"]
