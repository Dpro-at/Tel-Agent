"""The call. Long-running, event-driven, and under a sub-second budget per turn.

Never imports from ``api``: the database is the boundary between them. See
docs/ARCHITECTURE.md, enforced by the contract in .importlinter.
"""
