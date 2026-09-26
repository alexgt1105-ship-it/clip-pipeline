#!/bin/bash
# YouTube changes its extraction regularly and a stale yt-dlp fails with things like
# "The page needs to be reloaded" or a bare HTTP 403. This pulls the current build.
# Run it if downloads start failing.
set -e
cd "$(dirname "$0")/.."
./venv/bin/python -m pip install -q --upgrade \
    "yt-dlp @ https://github.com/yt-dlp/yt-dlp/archive/refs/heads/master.tar.gz"
./venv/bin/python -c "import yt_dlp; print('yt-dlp now', yt_dlp.version.__version__)"
