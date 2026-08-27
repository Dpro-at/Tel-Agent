"""The dashboard's backend, and the project's public API - they are the same thing.

Never touches audio, and never calls ``agent``. Authorisation is enforced here:
anything the browser can bypass is not a rule.
"""
