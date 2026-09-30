"""Allow ``python -m kali_ops``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
