"""Registry of the two hardcoded users this app supports and their per-user paths."""

import os
from dataclasses import dataclass
from pathlib import Path

_ROOT = os.path.dirname(os.path.abspath(__file__))
_RECORDINGS_ROOT = Path(_ROOT) / "recordings"
PROFILE_FILENAME = "student_profile.json"

@dataclass(frozen=True)
class User:
    id: str
    display_name: str

    @property
    def recordings_dir(self) -> Path:
        return _RECORDINGS_ROOT / self.id

    @property
    def profile_path(self) -> Path:
        return self.recordings_dir / PROFILE_FILENAME

USERS = {"niclas": User("niclas", "Niclas"), "alejandra": User("alejandra", "Alejandra")}

def get_user(user_id: str):
    return USERS.get(user_id)

def list_users():
    return list(USERS.values())
