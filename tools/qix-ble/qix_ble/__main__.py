"""Enable `python -m qix_ble ...` to run the CLI."""
import sys

from qix_ble.cli import main

if __name__ == "__main__":
    sys.exit(main())
