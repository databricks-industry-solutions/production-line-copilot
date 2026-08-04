"""``uv.lock`` must only reference indexes the public can reach.

This accelerator is published, but it is developed on a corporate network
where ``~/.config/uv/uv.toml`` makes an internal mirror the default index. Any
``uv run`` there rewrites every URL in the lockfile to that mirror — a 3,000-
line diff that looks like noise in review and, if merged, leaves anyone
outside the network unable to install the project at all. Pinning the public
index in ``pyproject.toml`` would fix the cause but breaks installs behind the
proxy, so we catch the effect instead.
"""

from __future__ import annotations

import re
from pathlib import Path

LOCKFILE = Path(__file__).resolve().parents[1] / "uv.lock"

PUBLIC_HOSTS = {"pypi.org", "files.pythonhosted.org"}

_URL = re.compile(r"https://([A-Za-z0-9.\-]+)/")


def test_the_lockfile_points_only_at_public_indexes():
    if not LOCKFILE.exists():  # pragma: no cover - lockfile is committed
        return

    hosts = {m.group(1) for m in _URL.finditer(LOCKFILE.read_text())}
    private = sorted(h for h in hosts if h not in PUBLIC_HOSTS)

    assert not private, (
        "uv.lock references non-public index host(s): "
        f"{private}. A `uv run` with a private mirror configured as the default "
        "index rewrote the lockfile. Restore it with `git checkout uv.lock`, and "
        "use `uv run --frozen` (or `UV_INDEX=https://pypi.org/simple`) to keep it "
        "from happening again."
    )
