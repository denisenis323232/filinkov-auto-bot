from __future__ import annotations

import subprocess
import sys
import time


def main() -> int:
    # New architecture: ChatGPT prepares/edits photos. Railway bot only moderates,
    # queues and publishes. No automatic image processor runs here.
    bot = subprocess.Popen([sys.executable, "run_moderation.py"])
    try:
        while True:
            if bot.poll() is not None:
                return bot.returncode or 1
            time.sleep(1)
    finally:
        if bot.poll() is None:
            bot.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
