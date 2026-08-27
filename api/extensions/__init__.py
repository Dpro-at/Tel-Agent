"""The extension contract — D-031.

The core registers itself through it: `agent_core`, `database` and `web_chat` are
official applications, not privileged code paths. That is the decision's own wording,
and it is what keeps the contract honest — a contract only its authors are exempt from
is a contract that drifts from what extensions actually need.
"""
