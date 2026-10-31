"""Run with: python -m uvicorn main:app --host 127.0.0.1 --port 8000"""
from backend.app import create_app

app = create_app()

__all__ = ['app']
