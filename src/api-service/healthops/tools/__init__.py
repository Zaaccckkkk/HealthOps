"""Deterministic, user-scoped history tools.

Layering: routes in ``healthops.main`` handle authentication and parameters,
``queries`` enforces user isolation and bounded data access, and the remaining
modules compute results from explicit inputs without touching the database.
"""
