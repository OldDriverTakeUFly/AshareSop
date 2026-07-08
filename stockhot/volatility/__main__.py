"""Module entry shim — enables `python -m stockhot.volatility`.

对齐 ``stockhot/advisor/__main__.py`` 模式。
"""

from stockhot.volatility.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
