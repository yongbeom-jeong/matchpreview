# user/main.py — 단일 실행 버전
import os, sys, logging, subprocess
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=True)

LOG_LEVEL = (os.getenv("LOG_LEVEL", "INFO") or "INFO").upper()
SPORTS = [s.strip() for s in os.getenv("SPORTS", "").split(",") if s.strip()]

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def run_sport_main(sport: str):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT.parent)  # automatic 기준
    user_id = ROOT.name
    sport_dir = ROOT / sport
    try:
        logging.info(f"[{user_id}] launching {sport}.main")
        subprocess.run(
            [sys.executable, "-m", f"{user_id}.{sport}.main"],
            cwd=sport_dir,
            env=env,
            check=False
        )
    except Exception as e:
        logging.error(f"[{user_id}] error in {sport}.main: {e}")

def main():
    if not SPORTS:
        logging.warning(f"[{ROOT.name}] no SPORTS defined")
        return
    for sport in SPORTS:
        run_sport_main(sport)
    logging.info(f"[{ROOT.name}] all SPORTS done")

if __name__ == "__main__":
    main()
