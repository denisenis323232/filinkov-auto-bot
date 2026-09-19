from __future__ import annotations

import subprocess
import sys
import time


def main() -> int:
    # Stage current selected cars/originals into the bot database.
    stage = subprocess.run([sys.executable, "stage_selected_14.py"], check=False)
    if stage.returncode != 0:
        print(f"WARNING: selected staging returned {stage.returncode}; starting moderation bot anyway", flush=True)

    # One-off: stage the approved Q2 style as ten separate images into local READY
    # so the moderation bot can send them as a real Telegram album.
    q2 = subprocess.run([sys.executable, "stage_q2_final_urls.py"], check=False)
    if q2.returncode != 0:
        print(f"WARNING: Q2 final staging returned {q2.returncode}; moderation bot will still start", flush=True)

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
