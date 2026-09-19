from __future__ import annotations

import subprocess
import sys
import time


def main() -> int:
    # Stage the current 14 selected cars into the bot database and Dropbox inbox.
    # Originals are always separate numbered files (01..10). No image processing here.
    stage = subprocess.run([sys.executable, "stage_selected_14.py"], check=False)
    if stage.returncode != 0:
        print(f"WARNING: selected staging returned {stage.returncode}; starting moderation bot anyway", flush=True)

    # Railway only stages originals, imports completed individual READY files,
    # moderates, queues and publishes after explicit approval.
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
