from __future__ import annotations

NAME_LIMIT = 100
_FORBIDDEN = set('<>:"/\\|?*')
_RESERVED = {"con", "prn", "aux", "nul",
             *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class InvalidProjectName(ValueError):
    pass


def project_stem(name: str) -> str:
    stem = name[:-4] if name.lower().endswith(".3mf") else name
    if not stem or len(stem) > NAME_LIMIT:
        raise InvalidProjectName(name)
    if stem[0] in " ." or stem[-1] in " .":
        raise InvalidProjectName(name)
    if any(ord(c) < 0x20 or ord(c) == 0x7F or c in _FORBIDDEN for c in stem):
        raise InvalidProjectName(name)
    if stem.split(".", 1)[0].lower() in _RESERVED:
        raise InvalidProjectName(name)
    return stem


def project_path(folder: str, stem: str) -> str:
    separator = "/" if "/" in folder and "\\" not in folder else "\\"
    return folder.rstrip("\\/") + separator + stem + ".3mf"


def is_listed(listing: dict, stem: str) -> bool:
    return any(p.get("name", "").lower() == stem.lower() for p in listing.get("projects", []))
