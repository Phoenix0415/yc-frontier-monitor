"""Traction extraction from the YC long_description (SPEC 002 Phase 1).

An LLM pass that reads text already on disk and fills a structured `traction`
object per company. The whole risk of this phase is the model inventing
numbers, so the prompt (specs/phase1-traction-prompt.md) is built to forbid it:
extract only what the text literally states, empty otherwise.

Constraints (SPEC 002 §4 / §4a):
- stdlib only — the Anthropic call goes through urllib.request, no SDK.
- key-optional — no ANTHROPIC_API_KEY means the whole step is a clean no-op.
- hash-gated — an LLM call fires only when the description text is new or its
  content_hash changed; unchanged companies are skipped (cost control + change
  detector).
- never destroy state — an API error or unparseable output skips the company
  and leaves any prior `traction` untouched; partial/guessed records are never
  written.
"""

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5"  # small/Haiku-class per SPEC 002 §4a
TIMEOUT = 60
MAX_TOKENS = 512

# published per-1M-token prices (input, output), for the dry-run cost estimate
PRICING = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
}

TRACTION_KEYS = ("revenue", "arr", "growth", "customers_count", "funding",
                 "named_customers")

# --- the extraction contract (specs/phase1-traction-prompt.md) --------------

SYSTEM = (
    "You extract only facts a company has explicitly stated in the text provided.\n"
    "You never infer, estimate, compute, or add anything not literally present.\n"
    "Return a single JSON object and nothing else — no prose, no markdown fences.\n\n"
    "Fields (all optional; use \"\" or [] when the text does not state it):\n"
    "- revenue: stated current revenue, copied as written (e.g. \"$428K live revenue\"). "
    "Money the company EARNS. Never money it raised.\n"
    "- arr: stated ARR or run-rate (e.g. \"$764K contracted ARR\").\n"
    "- growth: a stated growth figure or velocity (e.g. \"10x in the first week\", "
    "\"30% MoM\"). Vague adjectives like \"fast-growing\" are NOT growth — leave empty.\n"
    "- customers_count: a stated count of customers/users/pilots or usage volume "
    "(e.g. \"50+ customers\", \"automated 50,000+ calls\", \"100% of pilots converted\").\n"
    "- funding: capital THIS company has raised, if stated (e.g. \"$2M seed\", "
    "or named investors like \"backed by Lightspeed\"). Keep separate from "
    "revenue/arr. Do NOT record \"backed by Y Combinator\" / \"YC\" / a batch "
    "tag like \"(W26)\" — every company here is YC-backed, so it carries no "
    "signal; if YC is the only backer named, leave funding empty. When YC is "
    "named alongside a real investor, keep only the real investor.\n"
    "- named_customers: array of explicitly named customer or partner companies "
    "(e.g. [\"Mayo\", \"Experian Health\"]). Brand names only. [] if none are named.\n\n"
    "Rules:\n"
    "- Copy figures exactly as written. Do not normalize, convert, or do arithmetic.\n"
    "- Attribute only to THIS company. Numbers, customers, or funding the text ties "
    "to a founder's PREVIOUS company or employer (e.g. \"previously built X\", "
    "\"at <PriorCo> we scaled to...\", \"before founding\") belong to that other "
    "company — leave them out entirely.\n"
    "- If a figure's meaning is ambiguous (could be revenue or funding), place it in "
    "the single best-fitting field and leave the other empty. Never put the same "
    "figure in two fields.\n"
    "- No field may contain anything not grounded in the input text.\n"
    "- If the text states no traction facts at all, return every field empty.\n"
    "- Output JSON only."
)

_FEWSHOT_POS_DESC = (
    "Since our launch in July we are now at $764K in contracted ARR, of which "
    "$428K is live revenue. Our customers see a 10x increase in claims followed "
    "up per biller in the first week and we have converted 100% of pilots to "
    "paying customers. We have automated 50,000+ calls and signed partnerships "
    "with UC health systems, Mayo, Experian Health."
)
_FEWSHOT_NEG_DESC = (
    "We're building the future of autonomous logistics. Our platform helps "
    "fleets run smarter."
)


def _user_msg(name, one_liner, description):
    return "Company: %s\nOne-liner: %s\nDescription: %s" % (
        name or "", one_liner or "", description or "")


FEWSHOT = [
    {"role": "user", "content": _user_msg("LunaBill", "", _FEWSHOT_POS_DESC)},
    {"role": "assistant", "content": json.dumps({
        "revenue": "$428K live revenue",
        "arr": "$764K contracted ARR",
        "growth": "10x increase in claims followed up per biller in the first week",
        "customers_count": "100% of pilots converted to paying customers; 50,000+ calls automated",
        "funding": "",
        "named_customers": ["UC health systems", "Mayo", "Experian Health"],
    }, ensure_ascii=False)},
    {"role": "user", "content": _user_msg("(vision-only example)", "", _FEWSHOT_NEG_DESC)},
    {"role": "assistant", "content": json.dumps({
        "revenue": "", "arr": "", "growth": "", "customers_count": "",
        "funding": "", "named_customers": [],
    }, ensure_ascii=False)},
    # YC tautology + a real investor: drop YC, keep the named investor.
    {"role": "user", "content": _user_msg("(YC + investor example)", "",
        "We help cities navigate permitting reviews. We're backed by Y Combinator "
        "and Lightspeed Venture Partners.")},
    {"role": "assistant", "content": json.dumps({
        "revenue": "", "arr": "", "growth": "", "customers_count": "",
        "funding": "backed by Lightspeed Venture Partners", "named_customers": [],
    }, ensure_ascii=False)},
    # Prior company: the founders' previous company's numbers are NOT this company's.
    {"role": "user", "content": _user_msg("(prior-company example)", "",
        "Acme is the monitoring layer for long-running agents. The founders "
        "previously built Emergent (YC S24), where they scaled production agents "
        "to 5M+ users and grew from $0 to $100M ARR in 8 months.")},
    {"role": "assistant", "content": json.dumps({
        "revenue": "", "arr": "", "growth": "", "customers_count": "",
        "funding": "", "named_customers": [],
    }, ensure_ascii=False)},
]


# --- key + hashing ----------------------------------------------------------

def load_api_key():
    """ANTHROPIC_API_KEY from the environment, or a .env-style export at the
    repo root. None when absent — the caller must treat that as a no-op."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key and key.strip():
        return key.strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == "ANTHROPIC_API_KEY":
                v = v.strip().strip('"').strip("'")
                return v or None
    return None


def content_hash(text):
    """Whitespace-normalized sha256 — matches the delta engine's notion of a
    'real' text change, so trivial reformatting doesn't force a re-extraction."""
    return hashlib.sha256(" ".join((text or "").split()).encode("utf-8")).hexdigest()


def needs_extraction(company):
    """A company needs an LLM call when it has a description and either has no
    traction yet or its description text changed since the last extraction."""
    desc = (company.get("long_description") or "").strip()
    if not desc:
        return False
    tr = company.get("traction")
    if not tr:
        return True
    return tr.get("content_hash") != content_hash(desc)


# --- the call ---------------------------------------------------------------

def _parse_json(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    # tolerate a stray markdown fence / lead-in by taking the first {...} block
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            return None
    return None


def _clean(obj):
    """Validate shape and keep only the six contract keys with correct types;
    drop anything the model may have added. None if the object is unusable."""
    if not isinstance(obj, dict):
        return None
    out = {}
    for k in TRACTION_KEYS:
        v = obj.get(k)
        if k == "named_customers":
            out[k] = ([str(x).strip() for x in v if str(x).strip()]
                      if isinstance(v, list) else [])
        elif isinstance(v, str):
            out[k] = v.strip()
        elif v is None:
            out[k] = ""
        else:  # a number/bool the model emitted unquoted — keep it verbatim-ish
            out[k] = str(v)
    return out


def _post_messages(api_key, body, max_retries=4):
    """POST to /v1/messages with backoff on transient errors (429 / 5xx /
    network). Non-retryable HTTP errors (400/401/403) re-raise immediately so
    the caller surfaces them. Raises after exhausting retries."""
    data = json.dumps(body).encode("utf-8")
    headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION,
               "content-type": "application/json"}
    last = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(ANTHROPIC_URL, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 529):
                raise
            last = "HTTP %s" % e.code
            ra = e.headers.get("retry-after")
            try:
                delay = float(ra) if ra else 2 ** attempt
            except ValueError:
                delay = 2 ** attempt
            time.sleep(min(delay, 30))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = str(e)
            time.sleep(2 ** attempt)
    raise RuntimeError("exhausted retries (%s)" % last)


def extract_one(api_key, model, company):
    """Return (traction_dict | None, usage_dict, error_str | None).

    traction_dict has the six contract keys only (no source/hash/timestamp —
    the caller stamps those). None means skip-and-keep-prior (error or
    unparseable); the six-empty dict is a valid 'no traction stated' result."""
    messages = FEWSHOT + [{"role": "user", "content": _user_msg(
        company.get("name"), company.get("one_liner"), company.get("long_description"))}]
    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "system": SYSTEM,
        "messages": messages,
    }
    try:
        payload = _post_messages(api_key, body)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        return None, {}, "HTTP %s: %s" % (e.code, detail)
    except Exception as e:
        return None, {}, str(e)
    usage = payload.get("usage", {}) or {}
    text = "".join(b.get("text", "") for b in payload.get("content", [])
                   if b.get("type") == "text")
    traction = _clean(_parse_json(text))
    if traction is None:
        return None, usage, "unparseable model output: %r" % text[:160]
    return traction, usage, None


def estimate_cost(model, in_tokens, out_tokens):
    pin, pout = PRICING.get(model, PRICING[DEFAULT_MODEL])
    return in_tokens / 1e6 * pin + out_tokens / 1e6 * pout


def stamp(traction, desc):
    """Attach provenance + the hash gate to a cleaned traction dict."""
    return {
        "source": "yc_description",
        **traction,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "content_hash": content_hash(desc),
    }


def enrich_traction(companies, api_key, model=DEFAULT_MODEL, max_companies=None,
                    workers=6, log=print):
    """Fill `company['traction']` in place for new/changed companies, capped at
    max_companies. Hash-gated, concurrent, and failure-safe (a company that
    errors is skipped with its prior value intact). Returns a stats dict."""
    todo = [c for c in companies if needs_extraction(c)]
    if max_companies:
        todo = todo[:max_companies]
    stats = {"eligible": len(todo), "extracted": 0, "with_traction": 0,
             "errors": 0, "in_tokens": 0, "out_tokens": 0}
    if not todo:
        log("Traction extraction: nothing new to do (hash gate).")
        return stats
    log("Traction extraction: %d companies via %s (%d workers)..."
        % (len(todo), model, workers))
    lock = threading.Lock()

    def work(c):
        traction, usage, err = extract_one(api_key, model, c)  # network, no lock
        with lock:
            stats["in_tokens"] += usage.get("input_tokens", 0)
            stats["out_tokens"] += usage.get("output_tokens", 0)
            if err:
                stats["errors"] += 1
                log("  ! %s: %s (kept prior)" % (c.get("slug"), err))
                return
            c["traction"] = stamp(traction, c.get("long_description"))
            stats["extracted"] += 1
            if any(traction[k] for k in TRACTION_KEYS):
                stats["with_traction"] += 1

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(work, c) for c in todo]
        for done, _ in enumerate(as_completed(futures), 1):
            if done % 50 == 0 or done == len(todo):
                log("  %d/%d" % (done, len(todo)))
    stats["cost"] = estimate_cost(model, stats["in_tokens"], stats["out_tokens"])
    return stats
