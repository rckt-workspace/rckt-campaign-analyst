"""Regenerates the STATEMENTS block of backend/migrations/versions/20261025_0004_analyses.py from the SQL migration.

The SQL file is the source of truth. This script splits it into single statements (dollar-quoted function bodies and
quoted strings are respected), drops BEGIN/COMMIT and comments, and rewrites the block between the generated markers.
Run it from any directory: python scripts/generate_alembic_0004.py
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SQL_PATH = REPO / "supabase" / "migrations" / "20261025000300_analyses.sql"
ALEMBIC_PATH = REPO / "backend" / "migrations" / "versions" / "20261025_0004_analyses.py"
BEGIN_MARK = "# --- BEGIN GENERATED STATEMENTS ---"
END_MARK = "# --- END GENERATED STATEMENTS ---"


def strip_comments(sql: str) -> str:
    lines = []
    for line in sql.splitlines():
        in_quote = False
        cut = len(line)
        for index, char in enumerate(line):
            if char == "'":
                in_quote = not in_quote
            if not in_quote and line[index : index + 2] == "--":
                cut = index
                break
        lines.append(line[:cut])
    return "\n".join(lines)


def split_statements(sql: str) -> list[str]:
    statements, current = [], []
    in_dollar = in_quote = False
    index = 0
    while index < len(sql):
        if sql.startswith("$$", index) and not in_quote:
            in_dollar = not in_dollar
            current.append("$$")
            index += 2
            continue
        char = sql[index]
        if char == "'" and not in_dollar:
            in_quote = not in_quote
        if char == ";" and not in_dollar and not in_quote:
            statements.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    rest = "".join(current).strip()
    if rest:
        statements.append(rest)
    return [s for s in statements if s and s not in ("BEGIN", "COMMIT")]


def python_literal(statement: str) -> str:
    body = "\n".join("    " + line if line else "" for line in statement.splitlines())
    return f'    """\n{body}\n    """,'


def main() -> None:
    statements = split_statements(strip_comments(SQL_PATH.read_text(encoding="utf-8")))
    block = [BEGIN_MARK, "STATEMENTS = ("]
    block += [python_literal(s) for s in statements]
    block += [")", END_MARK]
    generated = "\n".join(block)

    source = ALEMBIC_PATH.read_text(encoding="utf-8")
    start = source.index(BEGIN_MARK)
    end = source.index(END_MARK) + len(END_MARK)
    ALEMBIC_PATH.write_text(source[:start] + generated + source[end:], encoding="utf-8")
    print(f"{len(statements)} statements written to {ALEMBIC_PATH.relative_to(REPO)}")


if __name__ == "__main__":
    main()
