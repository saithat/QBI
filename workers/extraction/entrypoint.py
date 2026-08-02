"""Canonical worker CLI path retained without migrating PRD-011 behavior early."""

from hiveblot.pipeline import main

__all__ = ["main"]


if __name__ == "__main__":
    main()
