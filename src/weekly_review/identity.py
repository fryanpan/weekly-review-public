"""Who counts as the data subject.

One function, `SubjectIdentity.is_subject()`, answers this for every workspace
source — comments, read sessions and activity touches alike. It is deliberately
one function and not three filters: the defect this module exists to fix was two
filters that had drifted apart, one of them (`HUMAN_NAME_FRAGMENT = "<first name>"`)
never matching the subject's own primary handle while the other had
no allowlist at all and counted 22.6h of other people's reading as his.

The rule is per-user config (`~/.config/weekly-review/identity.yaml`), never
source. This is a public repo; another user's handles are not ours to hardcode.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .config import load_yaml

# actorId prefixes and author kinds the stores use for non-human writers.
_MACHINE_KINDS = frozenset({"agent", "live", "chat", "test", "bot"})


@dataclass(frozen=True)
class AnonEra:
    """A dated window in which unnamed, unowned activity counts as the subject.

    An era is an exception with a stated reason and two hard edges, not a
    general rule. the user's, ruled 2026-09-12: the workspace did not record his
    name on browser handles it was calling anonymous until 2026-08-10, so the
    reading in that window is his own signed-out browser. After that date an
    anonymous reader is somebody else, and the era must not be widened to
    recover hours -- that is what makes it defensible.
    """

    start: date
    end: date  # inclusive
    reason: str = ""

    def contains(self, when: date) -> bool:
        return self.start <= when <= self.end


@dataclass(frozen=True)
class SubjectIdentity:
    """The data subject's handles, and the eras their anonymous activity counts.

    `name_fragments` are lowercased substrings, because one person has several
    handles and a store renders them inconsistently. The user's store holds four
    families for one person: `known-*` renders a first name, `anon-*` renders the
    full name, `user-*` renders a handle, and a slice of `anon-*` renders a
    generated `Anon-xxxxxx` handle with no name behind it at all.

    Empty means NO name filter -- see `is_subject`.
    """

    name_fragments: tuple[str, ...] = ()
    anon_eras: tuple[AnonEra, ...] = ()
    # A generated handle is a name-shaped placeholder, not a name. The store
    # mints `Anon-tnd4hs` for a browser it cannot identify, so matching it as a
    # "named reader" would drop every era session the ruling means to keep.
    anon_name_pattern: str = r"^anon[-_]"

    @property
    def configured(self) -> bool:
        return bool(self.name_fragments)

    def _is_generated(self, low: str) -> bool:
        return bool(low) and re.search(self.anon_name_pattern, low) is not None

    def is_subject(
        self,
        *,
        name: str = "",
        kind: str = "",
        is_owner: bool = False,
        ts: str | datetime | None = None,
    ) -> bool:
        """True when this actor is the data subject, and their time is his.

        Order matters, and each step is a different kind of evidence:

        1. An agent is never the subject. Agents sign as their PROJECT -- "Client A
           board", "Notes Repo" -- so a denylist on the literal
           name "Agent" let 53k of 61k comment words through as human time. The
           activity stream also files agents under `actor: "person"` ("Claude",
           "Live Feedback", "Chat Audit"), so this cannot be left to the caller.
        2. A named handle on the allowlist is the subject. Positive
           identification, so a third-party reviewer's real review time is
           correctly somebody's and correctly not *his*.
        3. `is_owner` is the store's own structural answer and needs no name
           list: it agrees with `actorName` on 5,687 of 5,699 read sessions
           (99.8%) and covers the handles a name list cannot see.
        4. A GENERATED handle ("Anon-tnd4hs") counts only inside a configured
           era. A blank name is not a generated handle: it is malformed input,
           and it counts never.
        5. Otherwise no -- a reader the store can really name, who is not on the
           allowlist, is somebody else. That is what drops a named collaborator
           inside the era without this repo ever holding their name.

        With no `name_fragments` configured, step 2 cannot fire and the rule
        would report zero hours for a new user with a working install. So an
        unconfigured identity means "no name filter": any non-agent person
        counts, matching `config.github_usernames()`, and the run warns.
        """
        low = str(name or "").strip().lower()
        if str(kind or "").strip().lower() in _MACHINE_KINDS:
            return False
        if not self.configured:
            return True
        if any(f in low for f in self.name_fragments):
            return True
        if is_owner:
            return True
        if not self._is_generated(low):
            # Named-and-not-his, or no name at all. A blank name is malformed
            # input, not evidence of anonymity -- every person record in the
            # store carries one -- and crediting it would make a parse failure
            # read as his time.
            return False
        when = _as_date(ts)
        if when is None:
            return False
        return any(era.contains(when) for era in self.anon_eras)


def _as_date(ts: str | datetime | None) -> date | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.date()
    raw = str(ts).strip()
    if not raw:
        return None
    if raw.isdigit():  # epoch milliseconds, as comments carry
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc).date()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _as_era_date(raw) -> date | None:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        return None


def load_subject_identity() -> SubjectIdentity:
    """Read the subject's handles and eras from identity.yaml.

    ```yaml
    identity:
      workspace_names: [first name, full name, handle]
      anon_eras:
        - start: 2026-06-29
          end: 2026-08-10
          reason: the workspace did not record his name on browser handles yet
    ```

    `WEEKLY_REVIEW_WORKSPACE_NAMES` (comma-separated) overrides the name list,
    matching how `WEEKLY_REVIEW_GITHUB_USER` overrides the GitHub one.
    """
    ident = load_yaml("identity").get("identity", {}) or {}

    env = os.environ.get("WEEKLY_REVIEW_WORKSPACE_NAMES", "").strip()
    if env:
        names = [n.strip() for n in env.split(",")]
    else:
        raw = ident.get("workspace_names") or ident.get("workspace_name") or []
        if isinstance(raw, str):
            raw = [raw]
        names = [str(n).strip() for n in raw]
    fragments = tuple(sorted({n.lower() for n in names if n}))

    eras = []
    for row in ident.get("anon_eras") or []:
        if not isinstance(row, dict):
            continue
        start, end = _as_era_date(row.get("start")), _as_era_date(row.get("end"))
        # A half-specified era would silently widen to everything or narrow to
        # nothing; neither is a thing a reader of the config asked for.
        if start is None or end is None or end < start:
            continue
        eras.append(AnonEra(start=start, end=end, reason=str(row.get("reason") or "")))

    pattern = str(ident.get("anon_name_pattern") or "").strip()
    if pattern:
        try:
            re.compile(pattern)
        except re.error:
            pattern = ""  # a broken pattern must not silently match nothing
    kwargs = {"anon_name_pattern": pattern} if pattern else {}
    return SubjectIdentity(name_fragments=fragments, anon_eras=tuple(eras), **kwargs)
