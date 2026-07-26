"""Registry of the two hardcoded users this app supports, their per-user paths,
and the language pair each of them is practising."""

import os
from dataclasses import dataclass
from pathlib import Path

import languages

_ROOT = os.path.dirname(os.path.abspath(__file__))
_RECORDINGS_ROOT = Path(_ROOT) / "recordings"
PROFILE_FILENAME = "student_profile.json"

@dataclass(frozen=True)
class User:
    id: str
    display_name: str
    native_lang: str  # the language they already have — they translate *into* it
    target_lang: str  # the language they are learning

    @property
    def recordings_dir(self) -> Path:
        return _RECORDINGS_ROOT / self.id

    @property
    def profile_path(self) -> Path:
        return self.recordings_dir / PROFILE_FILENAME

    @property
    def native(self) -> languages.Language:
        return languages.get(self.native_lang)

    @property
    def target(self) -> languages.Language:
        return languages.get(self.target_lang)

    @property
    def pair_label(self) -> str:
        """"Español → Français" — what the landing page shows under a name."""
        return f"{self.native.endonym} → {self.target.endonym}"

# One pair per user: the whole pipeline reads the pair off whoever is
# practising, and `recordings/<user>/student_profile.json` accumulates
# weaknesses for that pair alone (curriculum.load_profile archives the old file
# if a user's target language ever changes).
USERS = {
    "niclas": User("niclas", "Niclas", native_lang="en", target_lang="es"),
    "alejandra": User("alejandra", "Alejandra", native_lang="es", target_lang="fr"),
}

def get_user(user_id: str):
    return USERS.get(user_id)

def list_users():
    return list(USERS.values())
