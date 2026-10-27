"""Shortcut so you can run `py detect_ats.py <careers_url>` from the worker folder."""

import sys

from internradar.detect_ats import main

if __name__ == "__main__":
    sys.exit(main())
