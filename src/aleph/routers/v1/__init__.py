"""Versioned resource routers (``/api/v1``).

HTTP endpoints for the learner-facing resources (TDD §6). Session-cookie
protected via ``dependencies.get_current_user``; every route addresses by UUID
and enforces ownership (404 for another learner's resource).
"""
