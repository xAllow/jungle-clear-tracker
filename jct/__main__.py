"""python -m jct [--background]   (--background: start tracking and minimize; used by "Start with Windows")."""

import sys

from jct.gui import main

if __name__ == "__main__":
    main(background="--background" in sys.argv)
