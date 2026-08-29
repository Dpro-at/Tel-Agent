"""What every migration has to be true of, checked without a database.

D-029 says one schema serves PostgreSQL and SQLite. The suite already proves that by
running against both, and that is the real check — but only on CI, where a PostgreSQL
container exists. On a developer's machine the PostgreSQL half is skipped, so a
migration that cannot run there passes locally, gets pushed, and fails ten minutes
later in a job that had to build a container first.

These are the cheap checks that catch the difference before the push. They read the
migration files as text on purpose: a migration is a frozen artifact with its own
literals, and the model it was generated from being correct says nothing about it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"

# `sa.Column("x", sa.Boolean(), server_default=sa.text("1"))` - the exact shape
# autogenerate produces from a model whose default was written as a SQLite literal.
BOOLEAN_INTEGER_DEFAULT = re.compile(
    r"""sa\.Boolean\(\)\s*,\s*server_default\s*=\s*sa\.text\(\s*["']\s*[01]\s*["']\s*\)""",
    re.VERBOSE,
)


def _migrations() -> list[Path]:
    files = sorted(path for path in VERSIONS.glob("*.py") if path.name != "__init__.py")
    assert files, "no migrations found - this test is checking nothing"
    return files


@pytest.mark.parametrize("path", _migrations(), ids=lambda path: path.stem)
def test_a_boolean_default_is_written_for_both_dialects(path: Path) -> None:
    """`1` is a boolean in SQLite and an integer in PostgreSQL.

    PostgreSQL refuses the whole CREATE TABLE with "column is of type boolean but
    default expression is of type integer", so the migration does not half-apply -
    it does not apply at all, and every test that needs the table errors at setup.

    `sa.true()` and `sa.false()` render per dialect, which is what makes one migration
    serve both. This cost a CI run once; it costs a regex now.
    """
    source = path.read_text(encoding="utf-8")
    found = BOOLEAN_INTEGER_DEFAULT.findall(source)
    assert not found, (
        f"{path.name} gives a boolean column an integer default: {found}. "
        "Use sa.true() or sa.false(), which render per dialect."
    )


@pytest.mark.parametrize("path", _migrations(), ids=lambda path: path.stem)
def test_a_migration_can_be_undone(path: Path) -> None:
    """A `downgrade` that does nothing is a migration nobody can back out of.

    Not run - read. Running it would need a database per migration, and the failure
    this catches is the one where autogenerate produced an empty body and it was
    committed with the `pass` still in it.
    """
    source = path.read_text(encoding="utf-8")
    body = source.split("def downgrade()", 1)
    assert len(body) == 2, f"{path.name} has no downgrade at all"

    statements = [
        line.strip()
        for line in body[1].splitlines()
        if line.strip() and not line.strip().startswith("#") and '"""' not in line
    ]
    assert statements, f"{path.name} has an empty downgrade"
    assert statements != ["pass"], (
        f"{path.name} has a downgrade that does nothing. If it genuinely cannot be "
        "undone, say so in a comment above the `pass` so the next reader knows it was "
        "a decision rather than a leftover."
    )
