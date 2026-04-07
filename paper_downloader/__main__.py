"""python -m paper_downloader 入口"""

import sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

from .cli import cli

if __name__ == "__main__":
    cli()
