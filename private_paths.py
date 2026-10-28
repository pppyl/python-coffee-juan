"""Resolve local configuration without placing secrets in the source folder."""
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def private_dir():
    override = os.environ.get("GOODGIFTS_PRIVATE_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "GoodGifts/private/starbucks-local"


def client_config_path():
    override = os.environ.get("STARBUCKS_CLIENT_CONFIG")
    if override:
        return Path(override).expanduser()
    legacy = ROOT / "protocol/client.private.json"
    return legacy if legacy.is_file() else private_dir() / "protocol/client.private.json"
