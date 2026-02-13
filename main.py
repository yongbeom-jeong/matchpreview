# main.py — MatchPreview Root Launcher (최종 구조)
from __future__ import annotations
import os, sys, time, subprocess, logging
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=True)

LOG_LEVEL = (os.getenv("LOG_LEVEL", "INFO") or "INFO").upper()
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "3600"))
ACTIVE_USERS = [u.strip() for u in os.getenv("ACTIVE_USERS", "").split(",") if u.strip()]

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def run_user_main(user: str):
    """각 사용자별 main.py 실행 (1회 실행 후 종료)"""
    env = os.environ.copy()
    env["USER_ID"] = user
    user_dir = ROOT / user
    try:
        logging.info(f"[run] launching user={user}")
        subprocess.run(
            [sys.executable, "-m", f"{user}.main"],
            cwd=user_dir,
            env=env,
            check=False
        )
    except subprocess.CalledProcessError as e:
        logging.error(f"[run] user={user} exited code={e.returncode}")
    except Exception as e:
        logging.error(f"[run] user={user} error: {e}")

def main():
    logging.info("[main] MatchPreview root launcher start")
    if not ACTIVE_USERS:
        logging.warning("No ACTIVE_USERS defined in .env")
        return

    logging.info(f"ACTIVE_USERS={ACTIVE_USERS}")
    while True:
        for user in ACTIVE_USERS:
            run_user_main(user)

        logging.info(f"[main] all users done, sleep {POLL_SECONDS}s")
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
