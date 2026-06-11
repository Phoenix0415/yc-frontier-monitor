"""When updates are due, and the launchd agent that checks daily.

The cadence policy (the smart part of `yc.py auto`):
- monthly baseline: pull whenever the data is older than 31 days
- batch-kickoff boost: one extra pull about a week after each new batch
  starts. YC's four batches kick off in the first week of January, April,
  July and October; the kickoff dates here are nominal (not scraped — YC
  publishes no machine-readable calendar), which is fine because the monthly
  baseline catches anything the boost mistimes.

The launchd agent is a dumb daily tick that runs `yc.py auto`; every decision
lives in this file, version-controlled and testable. A tick missed while the
Mac sleeps fires once on wake (StartCalendarInterval semantics).
"""

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import batches as batchmod
from store import DATA_DIR, ROOT

MONTHLY_DAYS = 31
KICKOFF_DAY = 5          # batches start in the first week of Jan/Apr/Jul/Oct
KICKOFF_OFFSET_DAYS = 7  # ...and we pull one week later, when rosters fill in
TICK_HOUR = 10           # daily launchd check, local time

LABEL = "com.yc-monitor.auto"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
LOG_PATH = DATA_DIR / "auto.log"


# ---------------------------------------------------------------- cadence
def _kickoff_triggers(year):
    """[(pull_date, batch_slug)] for one calendar year."""
    return [(date(year, month, KICKOFF_DAY) + timedelta(days=KICKOFF_OFFSET_DAYS),
             "%s-%d" % (season, year))
            for season, month in batchmod.SEASONS]


def _last_update_day(state):
    return date.fromisoformat(state["updated_at"][:10]) if state.get("updated_at") else None


def update_due(state, today=None):
    """-> (due: bool, reason: str). The whole cadence policy in one place."""
    today = today or date.today()
    last = _last_update_day(state)
    if last is None or not state.get("batches"):
        return True, "no data yet — initial import"
    if (today - last).days >= MONTHLY_DAYS:
        return True, "monthly refresh (last update %s)" % last
    for trigger, slug in _kickoff_triggers(today.year - 1) + _kickoff_triggers(today.year):
        if last < trigger <= today:
            return True, ("%s kicked off around %s — pulling its early roster"
                          % (batchmod.display_name(slug),
                             trigger - timedelta(days=KICKOFF_OFFSET_DAYS)))
    return False, "fresh enough (last update %s)" % last


def next_due(state, today=None):
    """The next date `yc.py auto` will actually pull. -> (date, reason)"""
    today = today or date.today()
    last = _last_update_day(state)
    if last is None:
        return today, "initial import"
    candidates = [(last + timedelta(days=MONTHLY_DAYS), "monthly refresh")]
    for trigger, slug in _kickoff_triggers(today.year) + _kickoff_triggers(today.year + 1):
        if trigger > last:
            candidates.append((trigger, "%s kickoff + %d days"
                               % (batchmod.display_name(slug), KICKOFF_OFFSET_DAYS)))
    return min(candidates)


# ---------------------------------------------------------------- launchd
_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{script}</string>
    <string>auto</string>
  </array>
  <key>WorkingDirectory</key><string>{cwd}</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def _launchctl(*args):
    return subprocess.run(["launchctl"] + list(args), capture_output=True, text=True)


def installed():
    return _launchctl("list", LABEL).returncode == 0


def install():
    """Write the agent plist and (re)load it. Returns the plist path."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)  # launchd needs the log dir
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(_PLIST.format(
        label=LABEL,
        python=sys.executable,
        script=str(ROOT / "scripts" / "yc.py"),
        cwd=str(ROOT),
        hour=TICK_HOUR,
        log=str(LOG_PATH),
    ), encoding="utf-8")
    _launchctl("unload", str(PLIST_PATH))  # reload cleanly if already present
    result = _launchctl("load", str(PLIST_PATH))
    if result.returncode != 0:
        raise RuntimeError("launchctl load failed: %s" % result.stderr.strip())
    return PLIST_PATH


def uninstall():
    _launchctl("unload", str(PLIST_PATH))
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
