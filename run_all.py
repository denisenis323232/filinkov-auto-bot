from __future__ import annotations

import subprocess
import sys
import time


def main() -> int:
    # Keep the existing selected-set recovery available.
    stage = subprocess.run([sys.executable, "stage_selected_14.py"], check=False)
    if stage.returncode != 0:
        print(f"WARNING: selected staging returned {stage.returncode}; continuing", flush=True)

    # Current user-selected car for this run.
    q2_car = subprocess.run([sys.executable, "stage_q2_car.py"], check=False)
    if q2_car.returncode != 0:
        print(f"WARNING: Q2 car staging returned {q2_car.returncode}; continuing", flush=True)

    # Ten separate approved-style Q2 images for the moderation post.
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
