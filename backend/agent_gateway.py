"""The only place to wire your own async handle(command, services, emit)."""
from importlib import import_module


def load_handler(mode):
    if mode == 'demo':
        from .demo import handle
        return handle
    try:
        # The user creates this module. This application never generates it.
        return getattr(import_module('career_agent.agent'), 'handle')
    except (ImportError, AttributeError):
        return None
