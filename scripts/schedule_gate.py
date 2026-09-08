"""Hourly scheduler: update once daily after 02:17 China time, retry failures."""
import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHINA = timezone(timedelta(hours=8))


def should_run(event, checkpoint, now):
    if event != "schedule":
        return True
    local = now.astimezone(CHINA)
    due = local.replace(hour=2, minute=17, second=0, microsecond=0)
    if local < due:
        return False
    value = checkpoint.get("last_published_success")
    if not value:
        return True
    try:
        published = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if published.tzinfo is None:
            return True
        # A future checkpoint is invalid; do not let it suppress all updates.
        return not (due <= published <= now)
    except (ValueError, TypeError):
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", default="workflow_dispatch")
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    checkpoint_path = ROOT / "data/deployment.json"
    if args.record:
        state = json.loads((ROOT / "data/papers.json").read_text())
        if state.get("error") or not state.get("last_success"):
            raise ValueError("Cannot mark an unsuccessful retrieval as published")
        checkpoint_path.write_text(json.dumps({"last_published_success": state["last_success"]}, indent=2) + "\n")
    else:
        try:
            checkpoint = json.loads(checkpoint_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            checkpoint = {}
        run = should_run(args.event, checkpoint, datetime.now(timezone.utc))
        print("run=" + str(run).lower())


if __name__ == "__main__":
    main()
