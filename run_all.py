from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def main() -> int:
    processor = subprocess.Popen([sys.executable, "run_photo_processor.py"], env=os.environ.copy())
    bot = subprocess.Popen([sys.executable, "run_persistent_state.py"], env=os.environ.copy())

    def stop(_sig=None, _frame=None):
        for p in (processor, bot):
            if p.poll() is None:
                p.terminate()
        deadline = time.time() + 10
        while time.time() < deadline and any(p.poll() is None for p in (processor, bot)):
            time.sleep(0.2)
        for p in (processor, bot):
            if p.poll() is None:
                p.kill()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        while True:
            if processor.poll() is not None:
                code = processor.returncode or 1
                stop()
                return code
            if bot.poll() is not None:
                code = bot.returncode or 1
                stop()
                return code
            time.sleep(1)
    finally:
        stop()


if __name__ == "__main__":
    raise SystemExit(main())
