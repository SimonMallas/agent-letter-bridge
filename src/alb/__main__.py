"""Allow `python -m alb` as well as the console script.

Two ways in, one implementation. The untested path is how a second entry point
drifts, so both land on cli.main.
"""
import sys

from alb.cli import main

if __name__ == "__main__":
    sys.exit(main())
