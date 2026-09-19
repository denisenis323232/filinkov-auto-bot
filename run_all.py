from __future__ import annotations

import subprocess
import sys
import time


def main() -> int:
    # One-shot staging of currently selected cars that were missing in Dropbox.
    # Safe to rerun: files are overwritten with the same originals.
    stage = subprocess.run([sys.executable, "stage_missing_selected.py"], check=False)
    if stage.returncode != 0:
        print(f"WARNING: staging returned {stage.returncode}; starting moderation bot anyway", flush=True)

    # ChatGPT prepares/edits photos. Railway bot only moderates, queues and publishes.
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
