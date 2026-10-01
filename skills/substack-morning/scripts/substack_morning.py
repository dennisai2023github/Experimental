#!/usr/bin/env python3
"""substack_morning.py: read-only helper for the substack-morning Claude skill.

Fetches, filters, scores and renders a daily brief of Substack articles and
Notes worth commenting on. Claude writes the reply drafts; this script only
reads. Security model:
  * http_get is the single network function. It only issues GET requests.
  * Only https URLs to substack.com, *.substack.com and the custom domains of
    the user's own subscriptions are allowed (redirects are re-checked).
  * The substack.sid cookie is sent only to Substack hosts and is never
    printed, logged, written to disk or included in error messages.

Standard library only, Python 3.10+, works on Windows (pathlib everywhere).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
USER_AGENT = "substack-morning/1.0 (personal read-only brief)"
THROTTLE_SECONDS = 0.6
TIMEOUT_SECONDS = 20
BODY_CAP = 6000
SEEN_RETENTION_DAYS = 60
TEMPLATE_TOKEN = "/*__BRIEF_DATA__*/null"
DASHES = ("—", "–", "--")
DASH_FIELDS = ("reply_a", "reply_b", "summary", "why")
REQUIRED_ITEM_KEYS = ("id", "kind", "title", "url", "writer", "why", "summary", "reply_a", "reply_b")
API = "https://substack.com/api/v1"
LOCK_MAX_AGE = timedelta(minutes=30)

# SessionStart hook messages (status --hook prints at most one of these).
HOOK_READY = ("Substack morning brief is ready: {url}. Before answering Dennis's message, tell "
              "him his Substack brief is ready and give him that link as a clickable markdown link.")
HOOK_FAILED = ("Substack morning brief failed today: {reason}. Tell Dennis in one line before "
               "answering his message.")
HOOK_PREPARING = ("Substack morning brief is being prepared in the background. If Dennis asks, "
                  "tell him it will open in his browser when ready.")
HOOK_STILL = "Substack morning brief is still being prepared."
HOOK_ASK = "Substack morning brief has not run today. Ask Dennis if he wants it."
HOOK_SPAWN_FAILED = ("Substack morning brief could not start automatically (claude command not "
                     "found). Run /substack-morning manually.")
CHILD_PROMPT = ("Run the substack-morning skill now in unattended mode for today's morning brief. "
                "Follow SKILL.md, do not ask questions, and finish by running the open command.")
CHILD_ALLOWED_TOOLS = ["Bash(python:*)", "Bash(py:*)", "Read", "Write", "Edit", "Skill"]

DEFAULT_CONFIG = {
    "handle": "dennisahking",
    "topics_core": ["AI", "AI governance", "AI risk", "cybersecurity", "compliance",
                    "enterprise risk", "GRC"],
    "topics_extended": ["privacy", "data protection", "third-party risk", "vendor risk",
                        "operational resilience", "DORA", "NIS2", "internal audit",
                        "responsible AI", "AI ethics", "CISO", "security leadership",
                        "board governance", "AI policy", "AI regulation", "EU AI Act",
                        "AI safety", "fraud", "financial crime", "regtech", "ISO 42001",
                        "NIST AI RMF"],
    "exclude_keywords": ["crypto trading", "politics", "giveaway", "discount code"],
    "feed_tabs": ["subscribed", "for-you"],
    "category_tabs": ["category-technology", "category-business"],
    "max_publications": 80,
    "posts_per_publication": 10,
    "candidate_pool": 40,
    "first_run_lookback_hours": 48,
    "brief_dir": None,
    "claude_command": "claude",
    "auto_run": True,
    "artifact_url": None,
}

ENV_TEMPLATE = """\
# substack-morning secrets. This file stays on this machine only.
# How to fill in SUBSTACK_SID:
#   1. Log in to https://substack.com in your browser.
#   2. Open DevTools (F12) > Application > Cookies > https://substack.com
#      (Firefox: Storage > Cookies).
#   3. Copy the value of the cookie named substack.sid and paste it after SUBSTACK_SID=
# Never paste this value into a chat, an email or a shared document.
# When it expires, `check` will tell you to copy a fresh one.
SUBSTACK_SID=
SUBSTACK_HANDLE=dennisahking
"""

# Secret values registered here are scrubbed from every message we print.
_SECRETS: list[str] = []


def redact(text) -> str:
    text = str(text)
    for secret in _SECRETS:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


# --------------------------------------------------------------------------- errors

class SubstackError(Exception):
    """Sanitized error. Messages never contain headers or the cookie."""


class HostNotAllowed(SubstackError):
    pass


class HttpError(SubstackError):
    def __init__(self, status, host, detail=""):
        self.status = status
        self.host = host
        msg = f"HTTP {status} from host {host}" if status else f"network error from host {host}"
        super().__init__(f"{msg} ({detail})" if detail else msg)


def safe_error(exc: BaseException) -> str:
    if isinstance(exc, SubstackError):
        return redact(exc)
    return f"{type(exc).__name__}: {redact(exc)[:200]}"


# --------------------------------------------------------------------------- network

def is_substack_host(host: str) -> bool:
    host = (host or "").lower()
    return host == "substack.com" or host.endswith(".substack.com")


def check_url(url: str, allowed_custom=frozenset()) -> str:
    """Return the lowercase host if the URL is allowed, else raise HostNotAllowed."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        raise HostNotAllowed(f"refusing non-https URL for host {host or '?'}")
    try:
        port = parts.port
    except ValueError:
        port = -1
    if parts.username or parts.password or port not in (None, 443):
        raise HostNotAllowed(f"refusing unusual URL for host {host}")
    if is_substack_host(host) or host in {h.lower() for h in allowed_custom}:
        return host
    raise HostNotAllowed(f"host not in allowlist: {host}")


class _AllowlistRedirect(urllib.request.HTTPRedirectHandler):
    """Re-check every redirect hop and drop the cookie when leaving Substack."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed = getattr(req, "allowed_custom", frozenset())
        host = check_url(newurl, allowed)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.allowed_custom = allowed
            if not is_substack_host(host):
                new.remove_header("Cookie")
        return new


_OPENER = urllib.request.build_opener(_AllowlistRedirect())
_last_request_at: list = [None]


def http_get(url: str, sid: str | None = None, allowed_custom=frozenset(),
             timeout: float = TIMEOUT_SECONDS):
    """The ONLY network function: allowlisted, GET-only, throttled, sanitized. Returns JSON."""
    host = check_url(url, allowed_custom)
    if _last_request_at[0] is not None:
        wait = THROTTLE_SECONDS - (time.monotonic() - _last_request_at[0])
        if wait > 0:
            time.sleep(wait)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if sid and is_substack_host(host):
        headers["Cookie"] = f"substack.sid={sid}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    req.allowed_custom = frozenset(h.lower() for h in allowed_custom)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read()
    except SubstackError:
        raise
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, host) from None
    except urllib.error.URLError as exc:
        raise HttpError(None, host, type(exc.reason).__name__) from None
    except (OSError, ValueError) as exc:
        raise HttpError(None, host, type(exc).__name__) from None
    finally:
        _last_request_at[0] = time.monotonic()
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise HttpError(None, host, "response was not JSON") from None


def mock_fixture_name(url: str) -> str:
    """Map an API URL to an offline fixture file name (used by --mock)."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path
    query = urllib.parse.parse_qs(parts.query)
    if path == "/api/v1/subscriptions":
        name = "subscriptions.json"
    elif path.endswith("/public_profile"):
        name = "profile.json"
    elif path == "/api/v1/reader/feed":
        name = f"feed_{query.get('tab', [''])[0]}.json"
    elif path == "/api/v1/post/search":
        name = "search.json"
    elif path == "/api/v1/archive":
        label = host[: -len(".substack.com")] if host.endswith(".substack.com") else host
        name = f"archive_{label}.json"
    elif m := re.fullmatch(r"/api/v1/posts/(.+)", path):
        name = f"post_{urllib.parse.unquote(m.group(1))}.json"
    elif m := re.fullmatch(r"/api/v1/post/(\d+)/comments", path):
        name = f"comments_{m.group(1)}.json"
    elif m := re.fullmatch(r"/api/v1/reader/comment/(\d+)/replies", path):
        name = f"note_replies_{m.group(1)}.json"
    else:
        name = "unknown.json"
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


class Fetcher:
    """Wraps http_get, or serves fixture files when mock_dir is set."""

    def __init__(self, sid: str | None, mock_dir=None):
        self.sid = sid
        self.mock_dir = Path(mock_dir) if mock_dir else None
        self.allowed_custom: set[str] = set()

    def get(self, url: str):
        if self.mock_dir is None:
            return http_get(url, self.sid, frozenset(self.allowed_custom))
        host = check_url(url, self.allowed_custom)  # allowlist applies offline too
        path = self.mock_dir / mock_fixture_name(url)
        if not path.is_file():
            raise HttpError(404, host, "mock fixture missing")
        return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- config & state

def home_dir() -> Path:
    override = os.environ.get("SUBSTACK_MORNING_HOME")
    return Path(override).expanduser() if override else Path.home() / ".substack-morning"


def parse_env_file(path: Path) -> dict:
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_secrets(home: Path, cfg: dict) -> tuple[str, str]:
    """Return (sid, handle). Environment variables win over the .env file."""
    values = parse_env_file(home / ".env")
    for key in ("SUBSTACK_SID", "SUBSTACK_HANDLE"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    sid = (values.get("SUBSTACK_SID") or "").strip()
    if sid:
        _SECRETS.append(sid)
    handle = (values.get("SUBSTACK_HANDLE") or cfg.get("handle") or "").strip().lstrip("@")
    return sid, handle


def load_config(home: Path) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    path = home / "config.json"
    if path.is_file():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg


def brief_dir(home: Path, cfg: dict) -> Path:
    return Path(cfg["brief_dir"]).expanduser() if cfg.get("brief_dir") else home / "briefs"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def prune_seen(state: dict, today: date) -> None:
    cutoff = today - timedelta(days=SEEN_RETENTION_DAYS)
    kept = {}
    for key, stamp in (state.get("seen_ids") or {}).items():
        try:
            if date.fromisoformat(str(stamp)[:10]) >= cutoff:
                kept[key] = stamp
        except ValueError:
            continue
    state["seen_ids"] = kept


def load_state(home: Path) -> dict:
    path = home / "state.json"
    state = {"last_brief_at": None, "seen_ids": {}}
    if path.is_file():
        state.update(json.loads(path.read_text(encoding="utf-8")))
    prune_seen(state, date.today())
    return state


def save_state(home: Path, state: dict) -> Path:
    prune_seen(state, date.today())
    path = home / "state.json"
    write_atomic(path, json.dumps(state, indent=2, ensure_ascii=False))
    return path


# --------------------------------------------------------------------------- parsing helpers

def parse_dt(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        secs = value / 1000 if value > 1e12 else value
        return datetime.fromtimestamp(secs, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_int(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"([\d][\d,]*(?:\.\d+)?)\s*([KkMm])?", str(value))
    if not m:
        return None
    number = float(m.group(1).replace(",", ""))
    number *= {"k": 1_000, "m": 1_000_000}.get((m.group(2) or "").lower(), 1)
    return int(number)


def first_int(*values) -> int | None:
    for value in values:
        n = to_int(value)
        if n is not None:
            return n
    return None


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.parts.append(" ")

    def handle_data(self, text):
        if not self.skip:
            self.parts.append(text)


def html_to_text(markup: str, cap: int = BODY_CAP) -> str:
    parser = _TextExtractor()
    parser.feed(markup or "")
    parser.close()
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()[:cap]


def keyword_regex(term: str) -> re.Pattern:
    # Short all-caps terms (AI, GRC, DORA, NIS2) match case-sensitively to avoid noise.
    flags = 0 if (term.isupper() and len(term) <= 5) else re.IGNORECASE
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", flags)


def find_hits(text: str, terms) -> list[str]:
    return [t for t in terms if keyword_regex(t).search(text or "")]


# --------------------------------------------------------------------------- normalization

def pub_host(pub: dict) -> str:
    if pub.get("subdomain"):
        return f"{str(pub['subdomain']).lower()}.substack.com"
    return str(pub.get("custom_domain") or "").lower()


def _sum_reactions(obj: dict) -> int:
    n = to_int(obj.get("reaction_count"))
    if n is None and isinstance(obj.get("reactions"), dict):
        n = sum(to_int(v) or 0 for v in obj["reactions"].values())
    return n or 0


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def normalize_post(post, pub, source: str, ctx: dict) -> dict | None:
    if not isinstance(post, dict) or post.get("id") is None:
        return None
    pub = pub if isinstance(pub, dict) and pub else (post.get("publication") or {})
    bylines = [b for b in (post.get("publishedBylines") or []) if isinstance(b, dict)]
    first = bylines[0] if bylines else {}
    pub_id = str(post.get("publication_id") or pub.get("id") or "")
    canonical = post.get("canonical_url") or ""
    host = pub_host(pub) or (urllib.parse.urlsplit(canonical).hostname or "").lower()
    slug = post.get("slug") or ""
    paywalled = str(post.get("audience") or "everyone").lower() != "everyone"
    return {
        "id": f"post:{post['id']}",
        "kind": "article",
        "title": post.get("title") or "",
        "subtitle": post.get("subtitle") or "",
        "url": canonical or (f"https://{host}/p/{slug}" if host and slug else ""),
        "writer": first.get("name") or pub.get("author_name") or pub.get("name") or "",
        "writer_handle": first.get("handle") or "",
        "publication": pub.get("name") or "",
        "publication_host": host,
        "published_at": _iso(parse_dt(post.get("post_date"))),
        "comment_count": to_int(post.get("comment_count")) or 0,
        "reaction_count": _sum_reactions(post),
        "audience_size": first_int(pub.get("subscriber_count"), pub.get("subscriberCount"),
                                   pub.get("freeSubscriberCount"),
                                   pub.get("free_subscriber_count")),
        "paywalled": paywalled,
        "readable": (not paywalled) or pub_id in ctx["paid_pub_ids"],
        "followed": pub_id in ctx["followed_pub_ids"] or source == "feed:subscribed",
        "sources": [source],
        "keyword_hits": [],
        "excluded": False,
        "text": (post.get("truncated_body_text") or post.get("description") or "")[:BODY_CAP],
        "_author_ids": {str(b["id"]) for b in bylines if b.get("id") is not None},
        "_raw_id": post["id"],
        "_slug": slug,
    }


def normalize_note(item: dict, source: str, ctx: dict) -> dict | None:
    c = item.get("comment") if isinstance(item.get("comment"), dict) else {}
    if c.get("id") is None:
        return None
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    pub = item.get("publication") if isinstance(item.get("publication"), dict) else {}
    handle = c.get("handle") or user.get("handle") or ""
    body = str(c.get("body") or "")[:BODY_CAP]
    first_line = body.strip().splitlines()[0] if body.strip() else ""
    author = c.get("user_id") if c.get("user_id") is not None else user.get("id")
    return {
        "id": f"note:{c['id']}",
        "kind": "note",
        "title": first_line[:90],
        "subtitle": "",
        "url": f"https://substack.com/@{handle}/note/c-{c['id']}" if handle
               else f"https://substack.com/note/c-{c['id']}",
        "writer": c.get("name") or user.get("name") or handle,
        "writer_handle": handle,
        "publication": pub.get("name") or "",
        "publication_host": "substack.com",
        "published_at": _iso(parse_dt(c.get("date"))),
        "comment_count": first_int(c.get("reply_count"), c.get("children_count")) or 0,
        "reaction_count": _sum_reactions(c),
        "audience_size": first_int(user.get("follower_count"), user.get("followerCount"),
                                   c.get("follower_count")),
        "paywalled": False,
        "readable": True,
        "followed": source == "feed:subscribed" or str(pub.get("id") or "") in ctx["followed_pub_ids"],
        "sources": [source],
        "keyword_hits": [],
        "excluded": False,
        "text": body,
        "_author_ids": {str(author)} if author is not None else set(),
        "_raw_id": c["id"],
        "_slug": "",
    }


def normalize_feed_item(item, source: str, ctx: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("type")
    if kind == "comment" or (kind is None and "comment" in item):
        return normalize_note(item, source, ctx)
    if kind == "post" or (kind is None and "post" in item):
        return normalize_post(item.get("post"), item.get("publication"), source, ctx)
    return None


def merge_items(a: dict, b: dict) -> None:
    a["sources"] += [s for s in b["sources"] if s not in a["sources"]]
    a["followed"] = a["followed"] or b["followed"]
    a["readable"] = a["readable"] or b["readable"]
    for key in ("subtitle", "writer", "writer_handle", "publication", "publication_host",
                "audience_size", "url", "published_at", "_slug"):
        if not a.get(key) and b.get(key):
            a[key] = b[key]
    if len(b.get("text") or "") > len(a.get("text") or ""):
        a["text"] = b["text"]
    for key in ("comment_count", "reaction_count"):
        a[key] = max(a[key] or 0, b[key] or 0)
    a["_author_ids"] |= b["_author_ids"]


# --------------------------------------------------------------------------- scoring

def score_item(item: dict, cfg: dict, now: datetime) -> None:
    core = set(cfg["topics_core"])
    hits = item["keyword_hits"]
    parts = {"followed": 30.0 if item["followed"] else 0.0}
    if any(h in core for h in hits):
        parts["keyword"] = 25.0
    elif hits:
        parts["keyword"] = 15.0
    else:
        parts["keyword"] = 0.0
    parts["low_comments"] = 15 * (1 - min(item["comment_count"] or 0, 30) / 30)
    base = item["audience_size"] or (item["reaction_count"] or 0) * 20 or 1
    parts["audience"] = 15 * min(math.log10(max(base, 1)) / 5, 1)
    published = parse_dt(item["published_at"])
    age_hours = (now - published).total_seconds() / 3600 if published else 72
    parts["recency"] = 15 * max(0.0, 1 - max(age_hours, 0) / 72)
    item["score_parts"] = {k: round(v, 2) for k, v in parts.items()}
    item["score"] = round(sum(parts.values()), 1)


def select_pool(ranked: list, pool_size: int) -> list:
    """Top pool_size/2 of each kind, fill the rest from whichever kind has more."""
    half = pool_size // 2
    articles = [i for i in ranked if i["kind"] == "article"]
    notes = [i for i in ranked if i["kind"] == "note"]
    chosen = articles[:half] + notes[:half]
    rest = sorted(articles[half:] + notes[half:], key=lambda i: (-i["score"], i["id"]))
    chosen += rest[: max(0, pool_size - len(chosen))]
    return sorted(chosen, key=lambda i: (-i["score"], i["id"]))


# --------------------------------------------------------------------------- collect

def run_source(health: list, name: str, fn) -> list:
    """Run one source. fn returns (items, errors, attempts). Never raises."""
    try:
        items, errors, attempts = fn()
    except Exception as exc:  # unknown response shapes must not crash the brief
        health.append({"source": name, "status": "failed", "count": 0, "error": safe_error(exc)})
        return []
    items = [i for i in items if i]
    status = "ok" if not errors else ("failed" if len(errors) >= attempts else "partial")
    health.append({"source": name, "status": status, "count": len(items),
                   "error": redact("; ".join(errors[:3])) if errors else None})
    return items


def is_me(comment: dict, ctx: dict) -> bool:
    uid = comment.get("user_id")
    if uid is None and isinstance(comment.get("user"), dict):
        uid = comment["user"].get("id")
    if uid is not None and ctx["user_id"] and str(uid) == ctx["user_id"]:
        return True
    handle = comment.get("handle") or (comment.get("user") or {}).get("handle") or ""
    return bool(handle) and handle.lower() == ctx["handle"]


def tree_has_me(nodes, ctx: dict) -> bool:
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        c = node["comment"] if isinstance(node.get("comment"), dict) else node
        if is_me(c, ctx):
            return True
        for key in ("children", "descendantComments"):
            if tree_has_me(c.get(key) or node.get(key), ctx):
                return True
    return False


def build_pool(candidates: list, pool_size: int, fetcher: Fetcher, ctx: dict,
               health: list) -> list:
    """Select the balanced pool, enrich it, and backfill slots freed by already-commented items."""
    stats = {"body_errors": [], "body_attempts": 0, "check_errors": [], "check_attempts": 0}
    checked, dropped_ids = set(), set()
    while True:
        pool = select_pool([c for c in candidates if c["id"] not in dropped_ids], pool_size)
        todo = [p for p in pool if p["id"] not in checked]
        if not todo:
            break
        kept_ids = {i["id"] for i in enrich_items(todo, fetcher, ctx, stats)}
        checked |= {p["id"] for p in todo}
        dropped_ids |= {p["id"] for p in todo} - kept_ids
    run_source(health, "post_bodies", lambda: (
        ["x"] * (stats["body_attempts"] - len(stats["body_errors"])),
        stats["body_errors"], stats["body_attempts"]))
    run_source(health, "already_commented_check", lambda: (
        ["x"] * (stats["check_attempts"] - len(stats["check_errors"])),
        stats["check_errors"], stats["check_attempts"]))
    return pool


def enrich_items(pool: list, fetcher: Fetcher, ctx: dict, stats: dict) -> list:
    """Fetch article bodies and drop items the user already commented on."""
    kept, body_errors, check_errors = [], stats["body_errors"], stats["check_errors"]
    for item in pool:
        raw_id, host = item["_raw_id"], item["publication_host"]
        if item["kind"] == "article":
            if item["_slug"] and host:
                stats["body_attempts"] += 1
                try:
                    data = fetcher.get(f"https://{host}/api/v1/posts/"
                                       f"{urllib.parse.quote(item['_slug'], safe='')}")
                    text = html_to_text(data.get("body_html") or "")
                    if text:
                        item["text"] = text
                except Exception as exc:
                    body_errors.append(f"{item['id']}: {safe_error(exc)}")
            check_url_ = (f"https://{host}/api/v1/post/{raw_id}/comments"
                          "?all_comments=true&sort=best_first") if host else None
        else:
            check_url_ = f"{API}/reader/comment/{raw_id}/replies"
        item["commented_check"] = "unknown"
        if check_url_:
            stats["check_attempts"] += 1
            try:
                data = fetcher.get(check_url_)
                nodes = data if isinstance(data, list) else (
                    data.get("comments") or data.get("commentBranches") or [])
                if tree_has_me(nodes, ctx):
                    item["commented_check"] = "commented"
                    continue  # already commented: drop
                item["commented_check"] = "not_commented"
            except Exception as exc:
                check_errors.append(f"{item['id']}: {safe_error(exc)}")
        kept.append(item)
    return kept


def collect(cfg: dict, state: dict, fetcher: Fetcher, handle: str, now: datetime) -> dict:
    since = parse_dt(state.get("last_brief_at")) or now - timedelta(
        hours=float(cfg["first_run_lookback_hours"]))
    health: list = []
    ctx = {"handle": handle.lower(), "user_id": None,
           "followed_pub_ids": set(), "paid_pub_ids": set()}
    pubs: list = []

    def profile():
        data = fetcher.get(f"{API}/user/{urllib.parse.quote(handle, safe='')}/public_profile")
        if data.get("id") is not None:
            ctx["user_id"] = str(data["id"])
        return [data], [], 1

    def subscriptions():
        data = fetcher.get(f"{API}/subscriptions?tvOnly=false")
        pubs.extend(p for p in data.get("publications") or [] if isinstance(p, dict))
        for sub in data.get("subscriptions") or []:
            pid = str(sub.get("publication_id") or "")
            ctx["followed_pub_ids"].add(pid)
            membership = str(sub.get("membership_state") or "").lower()
            if "paid" in membership or "founding" in membership:
                ctx["paid_pub_ids"].add(pid)
        for pub in pubs:
            ctx["followed_pub_ids"].add(str(pub.get("id") or ""))
            if pub.get("custom_domain"):
                fetcher.allowed_custom.add(str(pub["custom_domain"]).lower())
        ctx["followed_pub_ids"].discard("")
        return pubs, [], 1

    def archives():
        out, errors = [], []
        limit = int(cfg["posts_per_publication"])
        targets = [p for p in pubs[: int(cfg["max_publications"])] if pub_host(p)]
        for pub in targets:
            try:
                data = fetcher.get(f"https://{pub_host(pub)}/api/v1/archive"
                                   f"?sort=new&offset=0&limit={limit}")
            except SubstackError as exc:
                errors.append(safe_error(exc))
                continue
            posts = data if isinstance(data, list) else (data.get("posts") or [])
            out += [normalize_post(p, pub, "subscriptions", ctx) for p in posts[:limit]]
        return out, errors, len(targets)

    def feed(tab):
        data = fetcher.get(f"{API}/reader/feed?tab={urllib.parse.quote(tab, safe='')}")
        items = (data.get("items") or []) if isinstance(data, dict) else []
        return [normalize_feed_item(i, f"feed:{tab}", ctx) for i in items], [], 1

    def search():
        out, errors = [], []
        for term in cfg["topics_core"]:
            try:
                data = fetcher.get(f"{API}/post/search?query={urllib.parse.quote(term)}&page=0")
            except SubstackError as exc:
                errors.append(f"{term}: {safe_error(exc)}")
                continue
            results = data if isinstance(data, list) else (
                data.get("results") or data.get("posts") or [])
            for r in results:
                if not isinstance(r, dict):
                    continue
                post = r["post"] if isinstance(r.get("post"), dict) else r
                out.append(normalize_post(post, r.get("publication") or post.get("publication"),
                                          f"search:{term}", ctx))
        return out, errors, len(cfg["topics_core"])

    run_source(health, "profile", profile)
    run_source(health, "subscriptions", subscriptions)
    raw = run_source(health, "archives", archives)
    for tab in list(cfg["feed_tabs"]) + list(cfg["category_tabs"]):
        raw += run_source(health, f"feed:{tab}", lambda tab=tab: feed(tab))
    raw += run_source(health, "search", search)

    merged: dict = {}
    for item in raw:
        if item["id"] in merged:
            merge_items(merged[item["id"]], item)
        else:
            merged[item["id"]] = item

    seen = state.get("seen_ids") or {}
    all_terms = list(cfg["topics_core"]) + list(cfg["topics_extended"])
    dropped = {"seen": 0, "own": 0, "old_or_undated": 0, "excluded": 0, "paywalled": 0,
               "already_commented": 0}
    candidates = []
    for item in merged.values():
        blob = " ".join([item["title"], item["subtitle"], item["text"]])
        item["keyword_hits"] = find_hits(blob, all_terms)
        item["excluded"] = bool(find_hits(blob, cfg["exclude_keywords"]))
        published = parse_dt(item["published_at"])
        if item["id"] in seen:
            dropped["seen"] += 1
        elif item["writer_handle"].lower() == ctx["handle"] or (
                ctx["user_id"] and ctx["user_id"] in item["_author_ids"]):
            dropped["own"] += 1
        elif published is None or published < since:
            dropped["old_or_undated"] += 1
        elif item["excluded"]:
            dropped["excluded"] += 1
        elif not item["readable"]:
            dropped["paywalled"] += 1
        else:
            score_item(item, cfg, now)
            candidates.append(item)

    candidates.sort(key=lambda i: (-i["score"], i["id"]))
    final = build_pool(candidates, int(cfg["candidate_pool"]), fetcher, ctx, health)
    dropped["already_commented"] = sum(
        1 for c in candidates if c.get("commented_check") == "commented")
    for item in final:
        for key in [k for k in item if k.startswith("_")]:
            del item[key]
    return {
        "generated_at": now.isoformat(),
        "since": since.isoformat(),
        "user": {"handle": handle, "id": ctx["user_id"]},
        "source_health": health,
        "dropped": dropped,
        "candidates": final,
    }


# --------------------------------------------------------------------------- render helpers

def dash_violations(drafts: dict) -> list[str]:
    found = []
    for idx, item in enumerate(drafts.get("items") or []):
        if not isinstance(item, dict):
            continue
        for field in DASH_FIELDS:
            value = str(item.get(field) or "")
            if any(d in value for d in DASHES):
                found.append(f"{item.get('id', f'#{idx}')}: {field}")
    return found


def validate_drafts(drafts: dict) -> list[str]:
    if not isinstance(drafts, dict) or not isinstance(drafts.get("items"), list):
        return ["drafts must be an object with an 'items' list"]
    errors = []
    if drafts.get("date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(drafts["date"])):
        errors.append("date must be YYYY-MM-DD")
    for idx, item in enumerate(drafts["items"]):
        if not isinstance(item, dict):
            errors.append(f"item #{idx} is not an object")
            continue
        ident = item.get("id", f"#{idx}")
        missing = [k for k in REQUIRED_ITEM_KEYS if k not in item]
        if missing:
            errors.append(f"{ident}: missing keys {', '.join(missing)}")
        if not re.search(r"\[[^\[\]]+\]", str(item.get("reply_b") or "")):
            errors.append(f"{ident}: reply_b needs at least one [slot] for Dennis to fill in")
    return errors


def _quote_block(text: str) -> str:
    return "\n".join("> " + line for line in str(text or "").splitlines() or [""])


def drafts_to_markdown(drafts: dict, day: str) -> str:
    out = [f"# Substack morning brief, {day}", ""]
    out.append(f"Generated {drafts.get('generated_at') or 'unknown'}. "
               f"{len(drafts['items'])} items.")
    for n, item in enumerate(drafts["items"], 1):
        pub = f" ({item['publication']})" if item.get("publication") else ""
        out += ["", f"## {n}. {item.get('title') or '(untitled)'}", "",
                f"- Kind: {item.get('kind')}",
                f"- Writer: {item.get('writer')}{pub}",
                f"- Link: {item.get('url')}",
                f"- Comments: {item.get('comment_count', 'n/a')}"]
        if item.get("flags"):
            out.append(f"- Flags: {', '.join(map(str, item['flags']))}")
        out += ["", f"**Why:** {item.get('why')}", "", f"**Summary:** {item.get('summary')}",
                "", "**Reply A**", "", _quote_block(item.get("reply_a")),
                "", "**Reply B**", "", _quote_block(item.get("reply_b"))]
    if drafts.get("source_health"):
        out += ["", "## Source health", ""]
        for h in drafts["source_health"]:
            err = f": {h.get('error')}" if h.get("error") else ""
            out.append(f"- {h.get('source')}: {h.get('status')} ({h.get('count')}){err}")
    return redact("\n".join(out) + "\n")


# --------------------------------------------------------------------------- commands

def cmd_setup(args) -> int:
    home = home_dir()
    home.mkdir(parents=True, exist_ok=True)
    cfg_path, env_path = home / "config.json", home / ".env"
    created = []
    if not cfg_path.exists():
        write_atomic(cfg_path, json.dumps(DEFAULT_CONFIG, indent=2))
        created.append("config.json")
    if not env_path.exists():  # never overwrite an existing .env
        env_path.write_text(ENV_TEMPLATE, encoding="utf-8")
        created.append(".env")
        try:
            os.chmod(env_path, 0o600)  # best effort; mostly a no-op on Windows
        except OSError:
            pass
    briefs = brief_dir(home, load_config(home))
    briefs.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"home": str(home), "config": str(cfg_path), "env": str(env_path),
                      "briefs": str(briefs), "created": created}, indent=2))
    if ".env" in created:
        print(f"Next: open {env_path} and paste your substack.sid value after SUBSTACK_SID=",
              file=sys.stderr)
    return 0


def cmd_check(args) -> int:
    home = home_dir()
    cfg = load_config(home)
    sid, handle = load_secrets(home, cfg)
    print("cookie: present" if sid else "cookie: missing")
    if not sid:
        print(f"Add SUBSTACK_SID to {home / '.env'} (run setup first if it does not exist).")
        return 1
    fetcher = Fetcher(sid, args.mock)
    try:
        prof = fetcher.get(f"{API}/user/{urllib.parse.quote(handle, safe='')}/public_profile")
        print(f"handle: {handle}  user id: {prof.get('id')}")
        subs = fetcher.get(f"{API}/subscriptions?tvOnly=false")
        pubs = subs.get("publications") or []
        print(f"publications: {len(pubs)}  (response keys: {sorted(subs)})")
        states = sorted({str(s.get('membership_state')) for s in subs.get("subscriptions") or []})
        print(f"membership_state values: {states}")
        if pubs:
            print(f"publication fields: {sorted(pubs[0])[:25]}")
        tab = (list(cfg["feed_tabs"]) or ["for-you"])[0]
        feed = fetcher.get(f"{API}/reader/feed?tab={urllib.parse.quote(tab, safe='')}")
        items = feed.get("items") or []
        kinds: dict = {}
        for it in items:
            kinds[str(it.get("type"))] = kinds.get(str(it.get("type")), 0) + 1
        print(f"feed '{tab}': {len(items)} items, types {kinds}")
    except HttpError as exc:
        if exc.status in (401, 403):
            print("cookie expired, copy a fresh substack.sid from your browser into "
                  f"{home / '.env'}", file=sys.stderr)
        else:
            print(f"check failed: {safe_error(exc)}", file=sys.stderr)
        return 1
    try:
        res = fetcher.get(f"{API}/post/search?query=AI&page=0")
        keys = sorted(res) if isinstance(res, dict) else ["<list>"]
        print(f"search endpoint: ok (keys {keys})")
    except SubstackError as exc:
        print(f"search endpoint: failed ({safe_error(exc)}); brief still works without it")
    print("check: ok")
    return 0


def status_info() -> dict:
    home = home_dir()
    cfg = load_config(home)
    today = date.today().isoformat()
    html_path = brief_dir(home, cfg) / f"{today}.html"
    state_path = home / "state.json"
    last = None
    if state_path.is_file():
        last = json.loads(state_path.read_text(encoding="utf-8")).get("last_brief_at")
    exists = html_path.is_file()
    return {"today": today, "brief_exists": exists,
            "brief_html": str(html_path) if exists else None, "last_brief_at": last}


def cmd_status(args) -> int:
    if args.hook:
        try:  # hook mode must never block or break session start
            if not status_info()["brief_exists"]:
                print(HOOK_LINE)
        except Exception:
            pass
        return 0
    print(json.dumps(status_info(), indent=2))
    return 0


def cmd_collect(args) -> int:
    home = home_dir()
    cfg = load_config(home)
    sid, handle = load_secrets(home, cfg)
    if not sid and not args.mock:
        print("warning: cookie missing, subscription sources will fail", file=sys.stderr)
    now = parse_dt(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise SubstackError("--now must be an ISO datetime")
    result = collect(cfg, load_state(home), Fetcher(sid, args.mock), handle, now)
    out = Path(args.out)
    write_atomic(out, redact(json.dumps(result, indent=2, ensure_ascii=False)))
    cands = result["candidates"]
    n_notes = sum(1 for c in cands if c["kind"] == "note")
    failed = [h["source"] for h in result["source_health"] if h["status"] != "ok"]
    print(f"collected {len(cands)} candidates ({len(cands) - n_notes} articles, {n_notes} notes) "
          f"since {result['since']}; dropped {result['dropped']}", file=sys.stderr)
    if failed:
        print(f"sources not fully ok: {', '.join(failed)}", file=sys.stderr)
    print(json.dumps({"out": str(out), "candidates": len(cands)}))
    return 0


def cmd_render(args) -> int:
    drafts = json.loads(Path(args.drafts).read_text(encoding="utf-8"))
    bad = dash_violations(drafts) if isinstance(drafts, dict) else []
    if bad:
        print("BLOCKED: dashes (em dash, en dash or '--') found in:", file=sys.stderr)
        for entry in bad:
            print(f"  {entry}", file=sys.stderr)
        print(json.dumps({"blocked": "dashes", "fields": bad}))
        return 2
    errors = validate_drafts(drafts)
    if errors:
        print("BLOCKED: drafts failed validation:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        print(json.dumps({"blocked": "validation", "errors": errors}))
        return 2
    template_path = Path(args.template) if args.template else (
        SCRIPT_DIR.parent / "assets" / "brief_template.html")
    if not template_path.is_file():
        print(f"error: template not found at {template_path}", file=sys.stderr)
        return 1
    template = template_path.read_text(encoding="utf-8")
    if TEMPLATE_TOKEN not in template:
        print(f"error: template is missing the token {TEMPLATE_TOKEN}", file=sys.stderr)
        return 1
    home = home_dir()
    briefs = brief_dir(home, load_config(home))
    day = drafts.get("date") or date.today().isoformat()
    payload = redact(json.dumps(drafts, ensure_ascii=False)).replace("</", "<\\/")
    html_out = Path(args.out) if args.out else briefs / f"{day}.html"
    write_atomic(html_out, template.replace(TEMPLATE_TOKEN, payload, 1))
    md_out, json_out = briefs / f"{day}.md", briefs / f"{day}.json"
    write_atomic(md_out, drafts_to_markdown(drafts, day))
    write_atomic(json_out, redact(json.dumps(drafts, indent=2, ensure_ascii=False)))
    print(json.dumps({"html": str(html_out), "md": str(md_out), "json": str(json_out)}, indent=2))
    return 0


def cmd_commit(args) -> int:
    drafts = json.loads(Path(args.drafts).read_text(encoding="utf-8"))
    items = drafts.get("items") or []
    home = home_dir()
    state = load_state(home)
    today = date.today().isoformat()
    ids = [str(i["id"]) for i in items if isinstance(i, dict) and i.get("id")]
    for item_id in ids:
        state["seen_ids"][item_id] = today
    state["last_brief_at"] = drafts.get("generated_at") or datetime.now(timezone.utc).isoformat()
    path = save_state(home, state)
    print(json.dumps({"committed": len(ids), "last_brief_at": state["last_brief_at"],
                      "state": str(path)}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="substack_morning.py",
                                description="Read-only Substack morning brief helper.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("setup", help="create home dir, config.json and .env template")
    s.set_defaults(func=cmd_setup)
    s = sub.add_parser("check", help="verify cookie and endpoints")
    s.add_argument("--mock", metavar="FIXTURE_DIR", help="serve responses from fixture files")
    s.set_defaults(func=cmd_check)
    s = sub.add_parser("status", help="report whether today's brief exists")
    s.add_argument("--hook", action="store_true", help="SessionStart hook mode")
    s.set_defaults(func=cmd_status)
    s = sub.add_parser("collect", help="gather and score candidates")
    s.add_argument("--out", required=True)
    s.add_argument("--mock", metavar="FIXTURE_DIR", help="serve responses from fixture files")
    s.add_argument("--now", help="override current time (ISO, for testing)")
    s.set_defaults(func=cmd_collect)
    s = sub.add_parser("render", help="render drafts to HTML and Markdown")
    s.add_argument("--drafts", required=True)
    s.add_argument("--out")
    s.add_argument("--template", help="template path (default ../assets/brief_template.html)")
    s.set_defaults(func=cmd_render)
    s = sub.add_parser("commit", help="mark drafted items as seen")
    s.add_argument("--drafts", required=True)
    s.set_defaults(func=cmd_commit)
    return p


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # Windows consoles may not be UTF-8
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except SubstackError as exc:
        print(f"error: {safe_error(exc)}", file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {safe_error(exc)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
