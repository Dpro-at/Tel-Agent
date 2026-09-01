"""Official extensions that ship with the core."""

# Loaded in this order at startup. Order matters only where one listener wants to run
# before another on the same event; today none do, and the list is explicit so that
# when one does, the answer is a line here rather than an import-time accident.
BUILTIN = (
    "api.extensions.builtin.agent_core",
    "api.extensions.builtin.database",
    "api.extensions.builtin.web_chat",
    "api.extensions.builtin.telegram",
)
