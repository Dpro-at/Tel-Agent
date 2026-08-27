"""Operational reporting — what the health screen reads.

Separate from `api/routes/`: gathering the state of the machine is not routing, and
the same functions are wanted by the scheduled probe, which has no request.
"""
