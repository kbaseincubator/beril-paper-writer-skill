"""CRAFT telemetry egress (C1 Workstream D) — a projecting CONSUMER of the
corrected run-record that writes whitelisted, batched usage/perf records to a
configurable shared lakehouse location.

A near-verbatim port of narrative-connector's `telemetry_egress.py` (same
function names + shape so the two stay legible as one pattern), specialised to
CRAFT's run-record. The model:

- **No service, no new emission.** At a natural flush point
  (`record_finalize`, AFTER the A2 completeness guard passes) the projector
  reads the canonical `run_record.json`, projects each `stages[]` entry +
  the run-level totals through a STRICT FIELD-WHITELIST, and best-effort
  batch-writes ONE JSONL file.
- **The guard is the WHITELIST — client-side, drop-by-default.** Only
  :data:`CRAFT_KEEP` fields may leave the machine; the projection drops
  everything else. A field added to the run-record in future cannot silently
  egress — it's absent from the allowlist, so the projection drops it.
  *You can't leak what you don't emit.*
- **The CRAFT identifier traps (the nc `src`/`dst` lesson).** `draft_dir`,
  the project/draft NAMES, `beril.yaml` content, title/presenter/author
  strings, slide/prompt text, artifact filenames — anything that *looks*
  safe but could carry an identifier — are DROPPED. Only an opaque
  `draft_hash` of the names egresses.
- **Best-effort / never-perturb.** A telemetry fault (unwritable
  `/global_share`, S3 down, disabled) NEVER fails or slows finalize — every
  fault is logged to stderr and swallowed. Same "never perturb the run" rule
  C1 enforces everywhere.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

#: The config var selecting the shared egress root. UNSET →
#: :data:`DEFAULT_TELEMETRY_ROOT` (default-on). ``off`` / ``local`` / ``none``
#: / ``disabled`` / empty → egress DISABLED (the per-user opt-out). Any other
#: value OVERRIDES the default: an ``s3a://bucket/prefix`` or a local/mounted
#: path. Mirrors nc's ``NARRATIVE_CONNECTOR_TELEMETRY_ROOT``.
TELEMETRY_ROOT_ENV = "CRAFT_TELEMETRY_ROOT"

#: The DEFAULT egress root (default-on) — a shared-access filesystem mount in
#: every Hub user's space. The env var overrides; ``off`` disables. Egress is
#: best-effort: an unwritable ``/global_share`` omits the write, never fails
#: the run. Note ``…/telemetry/craft/…`` deliberately parallels nc's
#: ``…/telemetry/nc/…`` — the shared-operational-memory convergence.
DEFAULT_TELEMETRY_ROOT = "/global_share"

#: Values of the root var that DISABLE egress (case-insensitive).
_DISABLED_VALUES = frozenset({"", "off", "local", "none", "disabled"})

#: The STRICT FIELD-WHITELIST — the load-bearing safety property. ONLY these
#: keys may leave the machine; the projection drops everything else by default.
#: Verified against the run-record stage schema (`run_record._REQUIRED_STAGE_KEYS`
#: = id, status, model, started_at, finished_at, elapsed_seconds,
#: input/output/cache_*_tokens, cost_usd, subrecord) + the top-level
#: (skill, skill_version, run_id, draft_dir, mode, status, started_at,
#: finished_at, totals{…}).
#:
#: Two derived/reserved fields handled explicitly:
#:   - ``ts`` is COMPUTED, not copied — the run-record stores ISO
#:     ``finished_at``/``started_at`` (not epoch); at projection
#:     ``ts = epoch(finished_at or started_at)``.
#:   - ``error_class`` is NOT in the run-record today — ``status``
#:     (completed/failed/skipped) is the only outcome signal. ``error_class``
#:     stays in the allowlist as RESERVED / KEEP-if-present (nc does this for
#:     ``bytes_moved``): absent from every record until a later enhancement
#:     records a per-stage failure-taxonomy token. NEVER fabricated.
#:
#: DROPPED (never egress): draft_dir + any filesystem path; the project/draft
#: NAMES (only their opaque ``draft_hash`` egresses); beril.yaml content;
#: title/presenter/author/contributor strings; slide text; prompt text;
#: artifact filenames; any free text; ``subrecord`` (a path); anything not
#: listed here. A field that LOOKS safe but could carry an identifier stays OUT.
CRAFT_KEEP: frozenset[str] = frozenset({
    "ts",             # epoch seconds (float) — COMPUTED from finished/started_at
    "kind",           # record-kind token: per-stage absent; run-level "run_finalize"
    "op",             # the stage id (= stages[].id) — a pipeline-stage token
    "skill",          # app id (= record.skill)
    "skill_version",  # app@version (= record.skill_version)
    "mode",           # talk-30 / report / … / null — a closed-ish format token
    "status",         # outcome token: completed / failed / skipped / running
    "model",          # opus / sonnet / haiku alias (non-identifying)
    "error_class",    # RESERVED / KEEP-if-present — taxonomy token, never a message
    "cost_usd",       # perf — the CRAFT cost axis
    "input_tokens",   # perf
    "output_tokens",  # perf
    "cache_read_tokens",      # perf
    "cache_creation_tokens",  # perf
    "duration_ms",    # perf — COMPUTED from elapsed_seconds × 1000
    "run_id",         # correlation — opaque run-N; groups a run + its resume chain
    "draft_hash",     # correlation — sha256(project/draft)[:16]; the draft shape
})

#: Fields ADDED at egress (not from the record): the real Hub username + the
#: craft platform version. Documented in the consent notice. The ONLY additions
#: — everything else is a strict projection of the run-record.
_INJECTED_KEYS = frozenset({"user", "craft_version"})

#: The consent flag file name (written once on first egress, under the draft's
#: audit dir — CRAFT has no central state tier; the audit dir is per-draft, so
#: the notice may re-fire per fresh draft, which is acceptable + honest).
_CONSENT_FLAG = "telemetry_consent"

#: Resolved at import only as a fallback; the live value is read per-call.
def _resolve_craft_version() -> str:
    """Best-effort craft platform version. Vendored copies may not have
    ``craft`` importable (standalone-on-hub) → a plain fallback, never an
    error."""
    try:
        import craft
        v = getattr(craft, "__version__", None)
        return v if isinstance(v, str) and v else "0.0.0"
    except Exception:  # noqa: BLE001 — vendored copies may lack craft
        return "0.0.0"


_CRAFT_VERSION = _resolve_craft_version()


def egress_root() -> str | None:
    """The configured shared egress ``<root>``, or ``None`` when egress is
    DISABLED. The single decision point — every entry short-circuits on
    ``None`` (a cheap env read, no IO), so a disabled deployment never touches
    the sink or prompts.

    Resolution (default-on):
      - env var UNSET → :data:`DEFAULT_TELEMETRY_ROOT` (``/global_share``);
      - env ``off`` / ``local`` / ``none`` / ``disabled`` / empty → ``None``
        (the per-user opt-out);
      - any other env value → that value (overrides the default)."""
    import os
    raw = os.environ.get(TELEMETRY_ROOT_ENV)
    if raw is None:
        return DEFAULT_TELEMETRY_ROOT
    if raw.strip().lower() in _DISABLED_VALUES:
        return None
    return raw.rstrip("/")


def _epoch(iso_ts: Any) -> float | None:
    """Parse an ISO-8601 UTC timestamp (``YYYY-MM-DDTHH:MM:SS[.fff]Z``) to epoch
    seconds. None on a missing/unparseable value (``ts`` is then simply
    absent from the record — never fabricated)."""
    if not isinstance(iso_ts, str) or not iso_ts:
        return None
    from datetime import datetime, timezone
    s = iso_ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _draft_names(draft_dir: Any) -> tuple[str | None, str | None]:
    """Pull (project_name, draft_name) from an absolute ``draft_dir`` WITHOUT
    egressing either — they're hashed by :func:`_draft_hash`, then dropped.

    presmaker: ``…/projects/<project>/talks/draft_N``;
    paper-writer: ``…/<…>/papers/draft_N`` (or ``…/projects/<project>/…``).
    We take the LAST path segment as the draft name, and the segment two-up as
    the project name when the parent is a known container (``talks``/``papers``/
    ``drafts``), else the immediate parent. Names are correlation substrate only
    — they never leave; only the hash does."""
    if not draft_dir:
        return None, None
    from pathlib import PurePosixPath
    parts = PurePosixPath(str(draft_dir)).parts
    if not parts:
        return None, None
    draft_name = parts[-1] or None
    project_name = None
    if len(parts) >= 3 and parts[-2] in ("talks", "papers", "drafts"):
        project_name = parts[-3]
    elif len(parts) >= 2:
        project_name = parts[-2]
    return project_name, draft_name


def _draft_hash(project: str | None, draft: str | None) -> str | None:
    """An OPAQUE, stable group key for a draft (port of nc's ``_plan_hash``):
    a truncated ``sha256(f"{project}/{draft}")`` hex digest. Reveals NOTHING
    about the (possibly semantic) project/draft names — one-way; the names
    never egress. Returns None when neither is present. ``user`` is NOT folded
    in (kept orthogonal: ``draft_hash``×``user`` = per-user-draft; ``draft_hash``
    alone = the draft shape cross-user)."""
    if not project and not draft:
        return None
    import hashlib
    raw = f"{project or ''}/{draft or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _filter_keep(src: dict) -> dict:
    """Drop-by-default allowlist copy: ``{k: src[k] for k in CRAFT_KEEP if k in
    src}``. The ONLY path by which a field reaches the output — a dropped field
    (draft_dir, names, subrecord, any free text, a future unknown key) cannot
    survive."""
    return {k: src[k] for k in CRAFT_KEEP if k in src}


def project_stage(
    stage: dict, *, shared: dict, user: str, craft_version: str,
) -> dict:
    """Project ONE ``stages[]`` entry to the whitelist (the redaction guard).

    Returns a NEW dict with ONLY :data:`CRAFT_KEEP` fields present on the stage
    (drop-by-default), the COMPUTED ``ts`` (epoch of finished/started_at) and
    ``duration_ms`` (elapsed_seconds×1000), the shared record-level keys
    (skill/skill_version/mode/run_id/draft_hash), and the injected
    ``user``/``craft_version``. ``op`` = the stage id."""
    out = _filter_keep(stage)
    if "id" in stage:
        out["op"] = stage["id"]
        out.pop("id", None)  # 'id' is not in CRAFT_KEEP, but be explicit
    ts = _epoch(stage.get("finished_at") or stage.get("started_at"))
    if ts is not None:
        out["ts"] = ts
    es = stage.get("elapsed_seconds")
    if isinstance(es, (int, float)):
        out["duration_ms"] = float(es) * 1000.0
    out.update(shared)
    out["user"] = user
    out["craft_version"] = craft_version
    return out


def project_run(
    record: dict, *, shared: dict, user: str, craft_version: str,
) -> dict:
    """Project the RUN-LEVEL record (one per finalize): ``kind="run_finalize"``,
    run status, mode, the totals' cost + token sums, total ``duration_ms``, and
    the computed ``ts`` (the record's ``finished_at``). Same shared keys +
    injected attribution as the per-stage projection."""
    totals = record.get("totals") or {}
    out = _filter_keep(totals)            # cost_usd + token sums (all in KEEP)
    out["kind"] = "run_finalize"
    if "status" in record:
        out["status"] = record["status"]
    es = totals.get("elapsed_seconds")
    if isinstance(es, (int, float)):
        out["duration_ms"] = float(es) * 1000.0
    ts = _epoch(record.get("finished_at") or record.get("started_at"))
    if ts is not None:
        out["ts"] = ts
    out.update(shared)
    out["user"] = user
    out["craft_version"] = craft_version
    return out


def project_record(
    record: dict, *, user: str, craft_version: str,
) -> list[dict]:
    """Project a whole run-record into the egress payload: one per-stage record
    per ``stages[]`` entry + one run-level record. Every record is a strict
    :data:`CRAFT_KEEP` projection + the computed ``ts``/``duration_ms``/
    ``draft_hash`` + injected attribution. The raw ``draft_dir`` / project /
    draft NAMES are DROPPED — only ``draft_hash`` egresses.

    Pure (no IO). This is the function the redaction audit hammers."""
    project, draft = _draft_names(record.get("draft_dir"))
    shared: dict[str, Any] = {}
    for k in ("skill", "skill_version", "mode", "run_id"):
        if record.get(k) is not None:
            shared[k] = record[k]
    dh = _draft_hash(project, draft)
    if dh is not None:
        shared["draft_hash"] = dh

    out: list[dict] = []
    for stage in (record.get("stages") or []):
        if isinstance(stage, dict):
            out.append(project_stage(
                stage, shared=shared, user=user, craft_version=craft_version))
    out.append(project_run(
        record, shared=shared, user=user, craft_version=craft_version))
    return out


def _consent_flag_path(audit_dir):
    from pathlib import Path
    return Path(audit_dir) / _CONSENT_FLAG


def _ensure_consent(*, root: str, user: str, audit_dir) -> None:
    """Print the one-time consent notice + record the flag. NON-BLOCKING by
    design (load-bearing for default-on): a printed stderr notice + a recorded
    flag — NEVER an ``input()``/interactive prompt. CRAFT runs non-interactively
    (the orchestrator / ``continue`` flow), so a blocking prompt would hang a
    run. Idempotent: once the flag exists, a cheap stat returns. The config-var
    opt-out (``egress_root()`` is ``None``) already suppressed the call."""
    flag = _consent_flag_path(audit_dir)
    try:
        if flag.exists():
            return
    except OSError:
        return  # can't stat → don't spam; treat as already-noticed
    print(
        "[craft] telemetry is ON by default: usage + performance records "
        "(stage, skill@version, status, model, cost, token counts, durations, "
        "and OPAQUE correlation keys — run id + a draft-name HASH — NEVER "
        "draft paths, project/draft names, beril.yaml content, presenter/"
        "author strings, slide or prompt text, or tokens) are written under "
        f"{root}/telemetry/craft/{user}/ attributed to your real Hub "
        f"username. Opt out any time by setting {TELEMETRY_ROOT_ENV}=off. "
        "This notice is shown once.",
        file=sys.stderr,
    )
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(f"consented {time.time()}\n", encoding="utf-8")
    except OSError as exc:  # best-effort — a failed flag write just re-notices
        print(f"[craft] telemetry: could not record consent flag ({exc}); "
              f"the notice may repeat.", file=sys.stderr)


def _batch_key(*, root: str, user: str, batch_id: str, now: float) -> str:
    """Compose the per-user, month-partitioned, append-only sink key:
    ``<root>/telemetry/craft/<safe_user>/<yyyy-mm>/<batch_id>.jsonl``. Each
    writer owns its keys (batch_id = the run-N), so no collision."""
    yyyy_mm = time.strftime("%Y-%m", time.gmtime(now))
    safe_user = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in user)
    return f"{root}/telemetry/craft/{safe_user}/{yyyy_mm}/{batch_id}.jsonl"


def _default_upload(*, target_uri: str, payload_lines: list[str]) -> int:
    """Write the JSONL batch to ``target_uri``. An ``s3a://`` root goes through
    the lakehouse writer; a local/mounted root is a plain file write. Returns
    bytes written."""
    body = ("\n".join(payload_lines) + "\n").encode("utf-8")
    if target_uri.startswith("s3a://"):
        # CRAFT has no bundled lakehouse client; an s3a root is a deployment
        # that provides one. Defer the import so the local path needs nothing.
        from craft.telemetry import _s3  # pragma: no cover — deployment-provided
        return _s3.upload_blob(target_uri=target_uri, payload=body)
    from pathlib import Path
    p = Path(target_uri)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    return len(body)


def _resolve_user() -> str:
    """The real Hub username: ``JUPYTERHUB_USER`` (the spawner sets it on the
    Hub) → OS user → ``unknown``. Cheap + never raises."""
    import os
    u = os.environ.get("JUPYTERHUB_USER") or os.environ.get("KBASE_USER")
    if u and u.strip():
        return u.strip()
    try:
        import getpass
        return getpass.getuser()
    except Exception:  # noqa: BLE001 — last resort
        return "unknown"


def egress_run_record(
    record: dict,
    *,
    audit_dir,
    user: str | None = None,
    craft_version: str | None = None,
    batch_id: str | None = None,
    upload=None,
    now: float | None = None,
) -> int | None:
    """Project a run-record to the whitelist and batch-write ONE file to the
    shared egress root (the C1-D flush). Call from ``record_finalize`` on the
    terminal write, AFTER the A2 completeness guard passes (don't egress an
    incomplete record).

    BEST-EFFORT: returns the bytes written, ``0`` when there's nothing to write,
    or ``None`` when egress is disabled / a fault was swallowed — and NEVER
    raises (a telemetry failure must not perturb the run that triggered it).

    ``upload``/``now`` are injectable for hermetic tests; production uses the
    s3a/local writer. ``batch_id`` defaults to the record's ``run_id`` (each
    writer owns its keys → append-only, no overwrite)."""
    root = egress_root()
    if root is None:
        return None  # disabled — cheap no-op, no prompt, no IO

    try:
        if upload is None:
            upload = _default_upload
        if now is None:
            now = time.time()
        if user is None:
            user = _resolve_user()
        if craft_version is None:
            craft_version = _CRAFT_VERSION
        if batch_id is None:
            # CRAFT's run_id (run-N) is PER-DRAFT, not globally unique — two
            # different drafts both at run-1 would collide on
            # <user>/<month>/run-1.jsonl and the second would clobber the
            # first. Key the batch by the OPAQUE draft_hash + run_id so each
            # (draft, run) owns a distinct, append-only file. draft_hash is
            # hex (filename-safe) and non-identifying.
            project, draft = _draft_names(record.get("draft_dir"))
            dh = _draft_hash(project, draft)
            run_id = str(record.get("run_id") or f"run-{int(now)}")
            batch_id = f"{dh}-{run_id}" if dh else run_id

        projected = project_record(
            record, user=user, craft_version=craft_version)
        if not projected:
            return 0  # nothing to egress

        _ensure_consent(root=root, user=user, audit_dir=audit_dir)

        lines = [json.dumps(rec, sort_keys=True) for rec in projected]
        target = _batch_key(root=root, user=user, batch_id=batch_id, now=now)
        return upload(target_uri=target, payload_lines=lines)
    except Exception as exc:  # noqa: BLE001 — telemetry NEVER perturbs a run
        print(f"[craft] telemetry egress skipped "
              f"({type(exc).__name__}: {exc}).", file=sys.stderr)
        return None
