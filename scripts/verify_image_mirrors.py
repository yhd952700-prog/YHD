#!/usr/bin/env python3
"""Mirror-liveness gate: is every pinned container image still pullable?

WHY THIS EXISTS
===============
On 2026-09-30 the CI job that protects the audit-stack surface referenced
``bitnami/etcd``. That manifest had been removed from the registry; the service
container failed to start, every ``needs:`` job behind it SKIPPED, and the whole
protection surface went to zero **without a single red check**. A dead image
reference is not a build error -- it is an INVISIBLE gate failure.

``verify_deployment_gate_vars.py`` catches one sibling shape (a control keyed to
a variable no artifact sets). This gate catches the other: an image tag no
registry can still resolve.

THREE STATES, AND WHY THERE ARE THREE
=====================================
A two-valued probe (up / down) is either flaky -- one rate-limited anonymous
request and the job goes red -- or muted. Neither is acceptable for a scheduled
reliability signal, so the verdict is three-valued:

  ALIVE               the registry answered 200 for the manifest. Green.
  CONFIRMED_GONE      the registry answered 404/410 for the manifest **and** the
                      response came from a real Docker-v2 registry endpoint.
                      RED -- a human must re-pin the image.
  PROBE_INCONCLUSIVE  everything else: 429 (rate limit), 5xx, timeout, network
                      error, an auth challenge we cannot answer, an unknown
                      registry host, a proxy's 502. NOT red, but explicitly
                      NOT_VERIFIED -- the gate says "I could not tell", never
                      "it is gone".

HARD RULES (the whole point of three states instead of two)
----------------------------------------------------------
R1  ``CONFIRMED_GONE`` requires 404/410 **and** ``Docker-Distribution-API-Version:
    registry/2.0`` (or a body carrying the registry's own ``MANIFEST_UNKNOWN`` /
    ``NAME_UNKNOWN`` code). A 404 from an HTML page, a CDN, or a corporate proxy
    is not evidence that a manifest is gone.
R2  A 429 / 5xx / timeout / connection error is ALWAYS ``PROBE_INCONCLUSIVE``.
    A rate-limited anonymous pull is the single most common Docker Hub outcome;
    reading it as "gone" would make this gate cry wolf every busy day.
R3  An unrecognised registry host is ``PROBE_INCONCLUSIVE``. The gate never
    guesses a probe URL for a host it does not know how to talk to.
R4  A token, when present (``DOCKERHUB_USERNAME`` / ``DOCKERHUB_TOKEN``), is used
    to lift Docker Hub's anonymous rate limit. It is never printed or logged.

LOCAL BUILDS ARE NOT PROBED
===========================
An image this repository builds itself (``liuhao-ai-os``) is ``LOCAL_BUILD``:
pulling it from a registry is not what the deployment does, so probing it would
invent a failure. See :data:`LOCAL_BUILD_REPOS`.

EXIT CODES
==========
  0  no image is ``CONFIRMED_GONE`` and at least one image is ``ALIVE``.
     ``PROBE_INCONCLUSIVE`` images are listed, marked NOT_VERIFIED, and do NOT
     fail the run (see R2).
  1  at least one image is ``CONFIRMED_GONE`` -- a real, actionable finding.
  2  NOT_VERIFIED -- nothing could be verified (no ALIVE and no GONE): offline,
     every request rate-limited, or no image found. Non-zero on purpose: a
     scheduled liveness job that silently goes green while blind is worse than
     one that says so.

WHEN TO RUN
===========
``schedule`` + ``workflow_dispatch`` ONLY -- see ``.github/workflows/
mirror-liveness.yml``. This must NOT gate a pull request: a registry hiccup or a
rate limit is not a reason to block a change, and the images change far less
often than the code does.

WHAT A PASS ACTUALLY MEANS (read before quoting it)
===================================================
PASS means: every image this repository pins was resolved by its registry as a
manifest that exists *at probe time*. It does NOT mean the image boots, is the
right architecture, or is free of CVEs. It means "this tag is not a dead
reference" -- the specific failure that made a protection surface vanish with no
red check.

Usage::

  python scripts/verify_image_mirrors.py
  python scripts/verify_image_mirrors.py --json
  python scripts/verify_image_mirrors.py --timeout 15 --max-images 5
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_NOT_VERIFIED = 2

#: Where image references live. Anything not in this list is not probed.
IMAGE_SOURCES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "docker-compose.*.yml",
    "docker-compose.*.yaml",
    "Dockerfile",
    "Dockerfile.*",
    "infra/*/docker-compose.yml",
    "infra/*/docker-compose.yaml",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
)
SKIP_PARTS = (".venv", "node_modules", ".git", "__pycache__")

#: Images this repository BUILDS. Probing them for a registry manifest would
#: invent a failure (they are not pulled -- they are built from the local
#: Dockerfile), so they are reported LOCAL_BUILD and never probed.
LOCAL_BUILD_REPOS = ("liuhao-ai-os",)

#: Verdict vocabulary. See the module docstring.
ALIVE = "ALIVE"
CONFIRMED_GONE = "CONFIRMED_GONE"
INCONCLUSIVE = "PROBE_INCONCLUSIVE"
LOCAL_BUILD = "LOCAL_BUILD"

#: Registries the gate knows how to talk to. A host not here (and not derived
#: from an explicit dotted first path segment) is INCONCLUSIVE, never GONE (R3).
KNOWN_REGISTRIES = {
    "docker.io": {"host": "registry-1.docker.io", "auth": "dockerhub"},
    "registry-1.docker.io": {"host": "registry-1.docker.io", "auth": "dockerhub"},
    "quay.io": {"host": "quay.io", "auth": "bearer-challenge"},
    "ghcr.io": {"host": "ghcr.io", "auth": "bearer-challenge"},
    "gcr.io": {"host": "gcr.io", "auth": "bearer-challenge"},
}

#: Docker's own "this manifest does not exist" error codes. Their presence in a
#: 404 body proves the answer came from a v2 registry rather than an HTML page.
_MANIFEST_MISSING_CODES = ("MANIFEST_UNKNOWN", "NAME_UNKNOWN", "MANIFEST_UNVERIFIED")

_ACCEPT = ", ".join((
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
))

_IMAGE_LINE = re.compile(r"(?m)^[ \t]*image:[ \t]*[\"']?([^\s\"'#]+)")
_FROM_LINE = re.compile(r"(?mi)^[ \t]*FROM[ \t]+([^\s]+)(?:[ \t]+AS[ \t]+[^\s]+)?[ \t]*$")
_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


class ImageRef(NamedTuple):
    """A parsed container image reference."""

    raw: str
    registry: str          # canonical registry key, e.g. "docker.io" / "quay.io"
    host: str              # the host to send the request to
    repository: str        # "library/redis" / "coreos/etcd"
    reference: str         # tag or "sha256:..."
    auth: str              # auth strategy key


class Verdict(NamedTuple):
    """The three-valued (plus local-build) outcome for one image."""

    state: str
    detail: str


class HttpResponse(NamedTuple):
    """What the transport returns. ``status`` is 0 for a transport-level error."""

    status: int
    headers: Dict[str, str]
    body: str
    error: str = ""


# --------------------------------------------------------------------------- #
# Pure functions -- no I/O, so the classification can be unit-tested offline.
# --------------------------------------------------------------------------- #
def parse_image_ref(raw: str) -> Optional[ImageRef]:
    """Parse ``[registry/]repository[:tag|@digest]`` into its parts.

    Returns ``None`` for a reference the gate must not probe (a digest-pinned
    reference is fine; an empty or obviously non-image token is not).
    """
    ref = raw.strip()
    if not ref or ref.startswith("-") or "/" not in ref and ":" not in ref and ref == "":
        if not ref:
            return None
    if not ref:
        return None

    registry = "docker.io"
    remainder = ref
    first, sep, rest = ref.partition("/")
    # A registry host is the first segment AND looks like a host (has a dot or a
    # port). "library/redis" has no dot -> it is a repository on docker.io.
    if sep and ("." in first or ":" in first):
        registry = first
        remainder = rest

    if _DIGEST.search(remainder):
        repository, _, digest = remainder.partition("@")
        reference = digest
    else:
        repository, sep2, tag = remainder.rpartition(":")
        if not sep2:
            repository, reference = remainder, "latest"
        else:
            reference = tag

    repository = repository.strip("/")
    if not repository:
        return None

    if registry in ("docker.io", "registry-1.docker.io"):
        registry = "docker.io"
        if "/" not in repository:
            repository = "library/" + repository

    info = KNOWN_REGISTRIES.get(registry)
    if info is None:
        # R3: unknown host -> we will NOT guess a probe URL. Report it as an
        # unresolvable reference and let the caller mark it INCONCLUSIVE.
        return ImageRef(raw, registry, registry, repository, reference, "unknown")
    return ImageRef(raw, registry, info["host"], repository, reference, info["auth"])


def classify(status: int, headers: Dict[str, str], body: str, error: str = "") -> Verdict:
    """Turn a probe response into a verdict. This is the whole safety surface.

    Every branch that is not "the registry said this manifest exists" or "a real
    v2 registry said it does not" returns ``PROBE_INCONCLUSIVE`` (R1/R2/R3).
    """
    if error:
        return Verdict(INCONCLUSIVE, "transport error: %s" % error[:120])
    if status == 0:
        return Verdict(INCONCLUSIVE, "no response")

    if 200 <= status < 300:
        return Verdict(ALIVE, "HTTP %d" % status)

    if status in (404, 410):
        v2 = headers.get("docker-distribution-api-version", "")
        body_has_code = any(code in body for code in _MANIFEST_MISSING_CODES)
        if "registry/2.0" in v2:
            return Verdict(CONFIRMED_GONE, "HTTP %d from a v2 registry" % status)
        if body_has_code:
            return Verdict(CONFIRMED_GONE,
                           "HTTP %d carrying a registry MANIFEST_UNKNOWN code" % status)
        # R1: a 404 that is not provably a registry answer is not evidence.
        return Verdict(INCONCLUSIVE,
                       "HTTP %d but not from a v2 registry (no "
                       "Docker-Distribution-API-Version) -- cannot confirm" % status)

    if status == 429:
        return Verdict(INCONCLUSIVE, "HTTP 429 -- rate limited (never read as gone, R2)")
    if status in (401, 403):
        return Verdict(INCONCLUSIVE, "HTTP %d -- auth could not be completed" % status)
    if status >= 500:
        return Verdict(INCONCLUSIVE, "HTTP %d -- registry/proxy error" % status)

    return Verdict(INCONCLUSIVE, "HTTP %d -- unexpected status" % status)


def extract_images(texts: Sequence[str]) -> List[str]:
    """Every distinct image reference in the given file bodies, in order.

    Commented-out lines are ignored: a ``# image: ...`` is a suggestion, not a
    pin. ``FROM`` lines that name a previous build stage (a bare identifier with
    no registry/tag) are skipped -- Docker treats them as stage names, not
    images.
    """
    out: List[str] = []
    seen = set()

    def add(ref: str) -> None:
        ref = ref.strip().strip('"\'')
        if ref and ref not in seen:
            seen.add(ref)
            out.append(ref)

    for text in texts:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            m = _IMAGE_LINE.match(line)
            if m:
                add(m.group(1))
                continue
            m = _FROM_LINE.match(line)
            if m:
                token = m.group(1)
                # ``FROM builder`` (a stage alias) has no registry/dot/tag --
                # skip it; ``FROM redis:7`` or ``FROM quay.io/x/y:1`` is an image.
                if "." not in token and ":" not in token and "/" not in token:
                    continue
                if token.lower() == "scratch":
                    continue
                add(token)
    return out


def is_local_build(ref: str) -> bool:
    parsed = parse_image_ref(ref)
    if parsed is None:
        return False
    repo = parsed.repository.split("/")[-1]
    return repo in LOCAL_BUILD_REPOS


def summarise(verdicts: Sequence[Tuple[ImageRef, Verdict]]) -> Dict[str, int]:
    counts = {ALIVE: 0, CONFIRMED_GONE: 0, INCONCLUSIVE: 0, LOCAL_BUILD: 0}
    for _ref, v in verdicts:
        counts[v.state] = counts.get(v.state, 0) + 1
    return counts


def exit_code_for(verdicts: Sequence[Tuple[ImageRef, Verdict]]) -> int:
    """0 / 1 / 2 -- see the module docstring."""
    counts = summarise(verdicts)
    if counts.get(CONFIRMED_GONE):
        return EXIT_FAIL
    if counts.get(ALIVE):
        return EXIT_OK
    return EXIT_NOT_VERIFIED


# --------------------------------------------------------------------------- #
# Transport + probe (the only I/O).
# --------------------------------------------------------------------------- #
class Probe:
    """Registry prober with an injectable HTTP transport.

    ``transport(url, headers) -> HttpResponse`` is the seam the tests use to
    exercise :func:`classify` without a network. The default uses urllib.
    """

    def __init__(self, timeout: float = 20.0,
                 transport: Optional[Callable[[str, Dict[str, str]], HttpResponse]] = None,
                 token: Optional[Tuple[str, str]] = None) -> None:
        self.timeout = timeout
        self._transport = transport or self._urllib_transport
        self._token = token
        self._bearer_cache: Dict[str, str] = {}

    # -- default transport ------------------------------------------------- #
    def _urllib_transport(self, url: str, headers: Dict[str, str]) -> HttpResponse:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read(4096).decode("utf-8", "replace")
                return HttpResponse(resp.status, _lower_headers(resp.headers), body)
        except urllib.error.HTTPError as exc:  # a status, not a transport failure
            try:
                body = exc.read(4096).decode("utf-8", "replace")
            except Exception:
                body = ""
            return HttpResponse(exc.code, _lower_headers(exc.headers or {}), body)
        except Exception as exc:  # noqa: BLE001 - any socket/proxy error is INCONCLUSIVE
            return HttpResponse(0, {}, "", "%s: %s" % (type(exc).__name__, exc))

    # -- auth -------------------------------------------------------------- #
    def _dockerhub_token(self, repository: str) -> Optional[str]:
        """Anonymous (or credentialed) pull token for Docker Hub.

        R4: when a token is configured we use it to lift the anonymous rate
        limit; the value is never logged.
        """
        import base64
        url = ("https://auth.docker.io/token?service=registry.docker.io"
               "&scope=repository:%s:pull" % repository)
        headers = {"Accept": "application/json", "User-Agent": "liuhao-mirror-liveness"}
        if self._token:
            user, secret = self._token
            raw = ("%s:%s" % (user, secret)).encode()
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        resp = self._transport(url, headers)
        if resp.status != 200:
            return None
        try:
            return json.loads(resp.body).get("token")
        except Exception:
            return None

    def _bearer_from_challenge(self, challenge: str, repository: str) -> Optional[str]:
        """Resolve a ``WWW-Authenticate: Bearer realm=...`` challenge (generic)."""
        if challenge not in self._bearer_cache:
            self._bearer_cache[challenge] = ""  # memoise failures too
            import urllib.parse
            params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
            realm = params.get("realm")
            if realm:
                query = {"service": params.get("service", ""),
                         "scope": params.get("scope", "repository:%s:pull" % repository)}
                url = realm + "?" + urllib.parse.urlencode(
                    {k: v for k, v in query.items() if v})
                resp = self._transport(url, {"Accept": "application/json",
                                             "User-Agent": "liuhao-mirror-liveness"})
                if resp.status == 200:
                    try:
                        self._bearer_cache[challenge] = json.loads(resp.body).get("token", "")
                    except Exception:
                        pass
        return self._bearer_cache.get(challenge) or None

    # -- probe ------------------------------------------------------------- #
    def probe(self, ref: ImageRef) -> Verdict:
        if ref.auth == "unknown":
            return Verdict(INCONCLUSIVE,
                           "unknown registry host %r -- not probed (R3)" % ref.registry)

        url = "https://%s/v2/%s/manifests/%s" % (ref.host, ref.repository, ref.reference)
        headers = {"Accept": _ACCEPT, "User-Agent": "liuhao-mirror-liveness"}

        if ref.auth == "dockerhub":
            token = self._dockerhub_token(ref.repository)
            if token:
                headers["Authorization"] = "Bearer " + token
            # A missing token is NOT fatal: anonymous Docker Hub still answers
            # 200 -- it just rate-limits sooner (which classify() maps to R2).

        resp = self._transport(url, headers)
        if resp.status == 401:
            challenge = resp.headers.get("www-authenticate", "")
            if challenge.lower().startswith("bearer"):
                token = self._bearer_from_challenge(challenge, ref.repository)
                if token:
                    headers["Authorization"] = "Bearer " + token
                    resp = self._transport(url, headers)
        return classify(resp.status, resp.headers, resp.body, resp.error)


def _lower_headers(headers: object) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for key in headers:  # type: ignore[union-attr]
            out[key.lower()] = headers[key]  # type: ignore[index]
    except Exception:  # noqa: BLE001
        pass
    return out


# --------------------------------------------------------------------------- #
# Discovery + main
# --------------------------------------------------------------------------- #
def collect_source_files(repo_root: Path = REPO_ROOT) -> List[Path]:
    found: List[Path] = []
    for pattern in IMAGE_SOURCES:
        for path in repo_root.glob(pattern):
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            if path.is_file():
                found.append(path)
    unique: List[Path] = []
    seen = set()
    for path in sorted(found, key=lambda p: str(p).lower()):
        key = str(path.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _label(path: Path, repo_root: Path = REPO_ROOT) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def render(verdicts: Sequence[Tuple[ImageRef, Verdict]], source_count: int) -> str:
    counts = summarise(verdicts)
    lines = ["=" * 78, "Mirror-liveness gate (three-state)", "=" * 78]
    lines.append("  image source files     : %d" % source_count)
    lines.append("  images parsed          : %d" % len(verdicts))
    lines.append("  ALIVE / GONE / INCONC. : %d / %d / %d  (local build: %d)"
                 % (counts[ALIVE], counts[CONFIRMED_GONE], counts[INCONCLUSIVE],
                    counts[LOCAL_BUILD]))
    lines.append("")
    for ref, v in verdicts:
        marker = {ALIVE: "  ok  ", CONFIRMED_GONE: " GONE ",
                  INCONCLUSIVE: "  ??  ", LOCAL_BUILD: " local"}[v.state]
        lines.append("[%s] %-42s %s" % (marker, ref.raw, v.detail))
    lines.append("")
    code = exit_code_for(verdicts)
    if code == EXIT_FAIL:
        lines.append("RESULT: FAIL -- %d image(s) CONFIRMED_GONE. Re-pin them; a "
                     "dead image reference silently zeroes every job behind it."
                     % counts[CONFIRMED_GONE])
    elif code == EXIT_OK:
        note = ""
        if counts[INCONCLUSIVE]:
            note = ("  %d inconclusive (NOT_VERIFIED -- not a claim they are gone)."
                    % counts[INCONCLUSIVE])
        lines.append("RESULT: PASS -- no image is confirmed gone.%s" % note)
    else:
        lines.append("RESULT: NOT_VERIFIED -- nothing could be verified (offline, "
                     "rate-limited, or no image found). This is NOT a pass and NOT "
                     "a claim that any image is gone.")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe every pinned image for a live registry manifest.")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable results on stdout")
    parser.add_argument("--timeout", type=float, default=20.0,
                        help="per-request timeout in seconds (default 20)")
    parser.add_argument("--max-images", type=int, default=0,
                        help="probe at most N images (0 = all); for smoke runs")
    args = parser.parse_args(list(argv) if argv is not None else None)

    sources = collect_source_files()
    texts = []
    for path in sources:
        try:
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    images = extract_images(texts)
    if args.max_images:
        images = images[:args.max_images]

    token = None
    user = os.environ.get("DOCKERHUB_USERNAME", "").strip()
    secret = os.environ.get("DOCKERHUB_TOKEN", "").strip()
    if user and secret:
        token = (user, secret)

    probe = Probe(timeout=args.timeout, token=token)
    verdicts: List[Tuple[ImageRef, Verdict]] = []
    for raw in images:
        if is_local_build(raw):
            parsed = parse_image_ref(raw)
            assert parsed is not None
            verdicts.append((parsed, Verdict(LOCAL_BUILD, "built by this repo, not pulled")))
            continue
        parsed = parse_image_ref(raw)
        if parsed is None:
            continue
        verdicts.append((parsed, probe.probe(parsed)))

    code = exit_code_for(verdicts)
    if args.json:
        print(json.dumps({
            "exit": code,
            "counts": summarise(verdicts),
            "token_used": bool(token),
            "images": [{"raw": r.raw, "registry": r.registry, "repository": r.repository,
                        "reference": r.reference, "state": v.state, "detail": v.detail}
                       for r, v in verdicts],
        }, indent=2))
    else:
        print(render(verdicts, len(sources)))
    return code


if __name__ == "__main__":
    sys.exit(main())
