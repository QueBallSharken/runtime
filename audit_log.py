import json
import sys
from datetime import datetime, timezone

from schema import validate


def emit(event: dict) -> None:
    stamped = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
    validate(stamped)
    print(json.dumps(stamped), file=sys.stderr)
