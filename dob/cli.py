"""
dob.cli
~~~~~~~
Command-line entry point.

Usage:
    dob [path/to/database.db | mysql://user:pass@host/dbname]
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    db_path = argv[0] if argv else None

    from dob.app import DobApp
    DobApp(db_path=db_path).run()


if __name__ == "__main__":
    main()
