"""Entry point for PyInstaller and for running from source: python run.py [--background]"""

import sys

from jct.gui import main

if __name__ == "__main__":
    main(background="--background" in sys.argv)
