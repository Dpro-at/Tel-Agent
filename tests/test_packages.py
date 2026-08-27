"""The two root packages import cleanly, and importing the agent does not pull in the API.

This is deliberately the smallest possible test, and it is not a placeholder. The
import-linter contract in `.importlinter` reads the source graph; this checks the other
half — that the packages are importable at all, and that nothing in `agent`'s import
chain reaches `api` at runtime.

Both halves are needed. A `__init__.py` that raises still satisfies a static contract,
and a static contract that cannot resolve the package silently passes nothing at all —
which is exactly how this repository's boundary check first appeared to be green.
"""

import subprocess
import sys


def test_agent_imports() -> None:
    import agent

    assert agent.__doc__


def test_api_imports() -> None:
    import api

    assert api.__doc__


def test_importing_agent_does_not_load_api() -> None:
    """The database is the boundary. The agent must run with the dashboard absent.

    Run in a fresh interpreter: this test file may itself have imported `api` already,
    and `sys.modules` in this process would hide the very thing being checked.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import agent, sys; print('api' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"
