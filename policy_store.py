import hashlib
import json
import os
from pathlib import Path


_DEFAULT_POLICY_PATH = Path(__file__).parent / "policies.json"


class PolicyStore:
    def __init__(self, path: str | Path | None = None):
        self._path = Path(path or os.environ.get("POLICY_PATH", _DEFAULT_POLICY_PATH))
        self._data: dict = {}
        self.version: str = "unknown"
        self.hash: str = "unknown"
        self._load()

    def _load(self):
        raw = self._path.read_text()
        self._data = json.loads(raw)
        self.version = self._data.get("_version", "unknown")
        self.hash = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get(self, subject_id: str, key: str):
        subject = self._data.get(subject_id) or self._data.get("default")
        if subject is None:
            raise KeyError(f"No policy for subject '{subject_id}' and no default")
        if key not in subject:
            raise KeyError(f"Policy key '{key}' not found for '{subject_id}'")
        return subject[key]
