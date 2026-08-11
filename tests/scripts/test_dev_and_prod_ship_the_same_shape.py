"""Dev and prod must differ in their values, never in which keys exist.

The two profiles in ``config/project-settings.yaml`` and the two app templates
(``app.yaml``, ``app.dev.yaml``) are the same deployment described twice. Dev
names dev things — a ``-dev`` app, a ``_dev`` schema, ``shift_1_dev`` — and that
is the arrangement working. A key that exists on one side and not the other is
the arrangement broken, and it fails in a way nobody notices for weeks: the
capability works in dev, ships without its variable in prod, and the prod app
comes up with the feature quietly switched off.

Both halves of this have already happened here. ``DOCS_KA_ENDPOINT`` reached
``app.dev.yaml`` and not ``app.yaml``, because the file you edit is the file you
are testing against. And the Genie ids ship as ``SET_ME`` in prod, where a
comment explains that ``SET_ME`` is what deploy.sh looks for to (re)capture an
id, but as empty strings in dev — the same behaviour by luck, since the deploy
gate tests for both, while the shipped file contradicts its own documentation.

So this compares shape, not values: the same keys, and the same *kind* of
placeholder for each key. Anything genuinely one-sided has to be named below,
which is the point — a deliberate asymmetry is a one-line diff, and an
accidental one is a failure.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

# Dev exists to be the same deployment with a suffix, so it alone carries the
# suffix that makes its resource names distinct.
DEV_ONLY_SETTINGS = frozenset({"resource_name_suffix"})
PROD_ONLY_SETTINGS: frozenset[str] = frozenset()

# No app env key is legitimately one-sided today: the app is the same program in
# both environments, and every difference belongs in the *value*.
DEV_ONLY_ENV: frozenset[str] = frozenset()
PROD_ONLY_ENV: frozenset[str] = frozenset()


def _profiles() -> tuple[dict, dict]:
    doc = yaml.safe_load((ROOT / "config/project-settings.yaml").read_text())
    profiles = doc["profiles"]
    return profiles["line_manager_dev"], profiles["line_manager_prod"]


def _env_names(app_yaml: str) -> list[str]:
    doc = yaml.safe_load((ROOT / app_yaml).read_text()) or {}
    return [e["name"] for e in doc.get("env", []) if "name" in e]


def _placeholder_kind(value: object) -> str:
    """Which flavour of "not a real value yet" this is, or ``value``.

    The flavours are not interchangeable. Empty means "unset, and the code reads
    that as a decision" — no AI Search endpoint means keyword retrieval.
    ``SET_ME`` means "the deploy fills this in", and the deploy's SET_ME guard
    and teardown's reset both spell it exactly that way. A ``<sample>`` means
    "replace this with yours". Shipping one where the other is documented is how
    a reader learns the wrong convention.
    """
    text = str(value if value is not None else "").strip()
    if not text:
        return "empty"
    if text == "SET_ME":
        return "SET_ME"
    if re.search(r"<[^<>]+>", text):
        return "sample"
    if "${" in text:
        return "expanded"
    return "value"


def test_both_profiles_declare_the_same_keys():
    dev, prod = _profiles()
    only_dev = set(dev) - set(prod) - DEV_ONLY_SETTINGS
    only_prod = set(prod) - set(dev) - PROD_ONLY_SETTINGS
    assert not only_dev and not only_prod, (
        "a key on one side only is a capability that works in one environment:\n"
        f"  dev only:  {sorted(only_dev)}\n"
        f"  prod only: {sorted(only_prod)}\n"
        "Add it to the other profile, or name it in DEV_ONLY_SETTINGS / "
        "PROD_ONLY_SETTINGS with the reason."
    )


def test_a_key_is_the_same_kind_of_placeholder_in_both():
    dev, prod = _profiles()
    drift = []
    for key in sorted(set(dev) & set(prod)):
        d, p = _placeholder_kind(dev[key]), _placeholder_kind(prod[key])
        if d != p:
            drift.append(f"{key}: dev {d} ({dev[key]!r}) vs prod {p} ({prod[key]!r})")
    assert not drift, (
        "the two profiles disagree about how an unfilled value is written, so one "
        "of them teaches a convention the deploy does not document:\n  "
        + "\n  ".join(drift)
    )


def test_the_side_marked_dev_is_the_dev_side():
    """A dev marker in prod's value is the two profiles written backwards.

    Shipped, the assistant names were swapped: prod named ``Plant documents —
    dev`` and dev named ``Plant documents``. Nothing fails on it — both
    environments still get an assistant each, under one wrong label — until a
    reader trusts the label, or a dev teardown deletes the assistant whose name
    says dev while prod is querying it.
    """
    dev, prod = _profiles()
    backwards = []
    for key in sorted(set(dev) & set(prod)):
        d, p = str(dev[key] or "").strip(), str(prod[key] or "").strip()
        if d == p:
            continue
        marked = re.compile(r"(?:^|[\s_\-—])dev(?:$|[\s_\-—])", re.IGNORECASE)
        if marked.search(p) and not marked.search(d):
            backwards.append(f"{key}: prod {p!r} carries the dev marker, dev is {d!r}")
    assert not backwards, "the dev and prod values are the wrong way round:\n  " + "\n  ".join(
        backwards
    )


def test_both_app_templates_declare_the_same_env():
    dev, prod = _env_names("app.dev.yaml"), _env_names("app.yaml")
    only_dev = set(dev) - set(prod) - DEV_ONLY_ENV
    only_prod = set(prod) - set(dev) - PROD_ONLY_ENV
    assert not only_dev and not only_prod, (
        "the app is one program; an env key present in only one template is a "
        "feature that silently does not run in the other:\n"
        f"  app.dev.yaml only: {sorted(only_dev)}\n"
        f"  app.yaml only:     {sorted(only_prod)}"
    )


def test_neither_app_template_declares_a_key_twice():
    """A duplicate is a merge that went wrong, and the last one silently wins."""
    for app_yaml in ("app.yaml", "app.dev.yaml"):
        names = _env_names(app_yaml)
        dupes = sorted({n for n in names if names.count(n) > 1})
        assert not dupes, f"{app_yaml} declares {dupes} more than once"
