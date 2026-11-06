"""Teamarr Assistant: helps Teamarr work with Dispatcharr.

Team tags. Teamarr's feed separation can't tell that "SPORTS NETWORK" is a
team's broadcaster when ESPN's broadcast data doesn't list it (Teamarr issue #1172).
It does recognise a team name in parentheses in the stream name, "... (Team Name)",
and then files the stream under that team's home or away feed depending on the game.
This plugin appends those tags. Dispatcharr identifies streams by URL (M3U hash key), so
a renamed stream keeps its channel assignments; each M3U refresh writes the provider's
name back, so the tags are re-applied after every refresh (m3u_refresh). A channel that
carries several teams (two teams in one city) gets one tag per team; Teamarr only uses
the one matching the game's teams.

Teamarr sync. Teamarr only sees new provider streams when it next generates. After each
refresh of the chosen account, the stream names in the chosen groups are compared with
the last check, and new names start a generation. Names are compared rather than the
refresh's "streams updated" count, because the tags above make that count non-zero on
every refresh. Sync runs after tagging, in the same action, so Teamarr sees the tags.

Daily run. With a daily time set, the first refresh at or after it also starts one
generation, so Teamarr's own scheduler can be switched off. That scheduler also refreshes
Teamarr's team and league cache, so the daily run does that first when it is stale. A
cache refresh takes minutes, so it is started and the generation follows on a later
refresh. New streams found before that day's daily run wait for it, which stops the date
change at midnight from causing two runs.
"""
import copy
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin.json"), encoding="utf-8") as _f:
    _MANIFEST = json.load(_f)

PLUGIN_KEY = "teamarr_assistant"
LOGGER_NAME = f"apps.plugins.{PLUGIN_KEY}"
STATE_FILE = "sync_state.json"
HTTP_TIMEOUT = 8
# A generation takes minutes and the request only returns when it ends. Teamarr keeps
# running after we hang up, so wait briefly and then confirm through its status endpoint.
START_TIMEOUT = 8
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_MONTH_RE = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_DAY = re.compile(rf"\b{_MONTH_RE}\.?\s+(\d{{1,2}})(?![\d:])", re.I)
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})\s+{_MONTH_RE}\b", re.I)


def _parse(text):
    """'PATTERN = Team A, Team B' lines -> [(pattern_lower, [teams])]."""
    rules = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        pattern, teams = line.split("=", 1)
        teams = [t.strip() for t in teams.split(",") if t.strip()]
        if pattern.strip() and teams:
            rules.append((pattern.strip().lower(), teams))
    return rules


def _rules(settings):
    """Rules from the 'channels' table; falls back to the old 'mappings' text (v1.0)."""
    rows = settings.get("channels")
    if not isinstance(rows, list):
        if isinstance(settings.get("mappings"), str):
            return _parse(settings["mappings"])
        rows = next(f["default"] for f in _MANIFEST["fields"] if f["id"] == "channels")
    return _parse("\n".join(f"{r.get('channel') or ''} = {r.get('teams') or ''}" for r in rows if isinstance(r, dict)))


def _strip_tags(name, teams):
    """Remove our ' (Team)' tags from the end of a name (any order)."""
    tags = {f"({t})".lower() for t in teams}
    changed = True
    while changed:
        changed = False
        for tag in tags:
            if name.lower().endswith(" " + tag):
                name = name[: -(len(tag) + 1)].rstrip()
                changed = True
    return name


def _number(settings, key, default):
    try:
        return max(0, int(float(settings.get(key, default))))
    except (TypeError, ValueError):
        return default


def _sync_settings(settings):
    field_default = {f["id"]: f.get("default") for f in _MANIFEST["fields"]}
    value = lambda key: settings.get(key, field_default.get(key))  # noqa: E731
    return {
        "enabled": bool(value("sync_enabled")),
        "url": (value("teamarr_url") or "").strip().rstrip("/"),
        "account": (value("sync_account") or "").strip(),
        "groups": _group_list(value("sync_groups")),
        "today_only": bool(value("sync_today_only")),
        "min_new": max(1, _number(settings, "sync_min_new", 1)),
        "cooldown": _number(settings, "sync_cooldown_minutes", 10),
        "daily_time": _clock(value("sync_daily_time")),
    }


def _group_list(value):
    """The saved list of groups. Comma-separated text from before the dropdown also works."""
    items = value.split(",") if isinstance(value, str) else value or []
    return [g.strip() for g in items if g.strip()]


def _clock(text):
    """'0:30' -> '00:30'. Blank or 'off' -> ''. Anything else -> None, reported as a problem."""
    text = (text or "").strip()
    if not text or text.lower() == "off":
        return ""
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", text)
    return f"{int(match[1]):02d}:{match[2]}" if match else None


def _dated_today(name, today):
    """True if the name carries today's date, False if only other dates, None if undated.

    Names can carry a date twice, and not always the same way, so any match counts.
    A day and month with no year is taken as this year.
    """
    found = False
    for year, month, day in _ISO_DATE.findall(name):
        found = True
        if (int(year), int(month), int(day)) == (today.year, today.month, today.day):
            return True
    pairs = [(m, d) for m, d in _MONTH_DAY.findall(name)] + [(m, d) for d, m in _DAY_MONTH.findall(name)]
    for month, day in pairs:
        found = True
        if (MONTHS[month[:3].lower()], int(day)) == (today.month, today.day):
            return True
    return False if found else None


def _relevant(names, today, today_only):
    if not today_only:
        return set(names)
    return {n for n in names if _dated_today(n, today) is not False}


def _load_state(data_dir):
    try:
        with open(os.path.join(data_dir, STATE_FILE), encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) and isinstance(state.get("names"), list) else None
    except (OSError, ValueError):
        return None


def _save_state(data_dir, state):
    path = os.path.join(data_dir, STATE_FILE)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(path + ".tmp", path)


def _get_json(url):
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as response:
        return json.load(response)


def _teamarr_running(url):
    return bool(_get_json(f"{url}/api/v1/epg/generate/status").get("in_progress"))


def _start_generation(url):
    """Start a generation. Returns 'started', or 'busy' if one is already running."""
    if _teamarr_running(url):
        return "busy"
    request = urllib.request.Request(
        f"{url}/api/v1/epg/generate",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=START_TIMEOUT):
            return "started"
    except urllib.error.HTTPError as e:
        if e.code == 409:
            return "busy"
        raise
    except TimeoutError:
        pass
    except urllib.error.URLError as e:
        if not isinstance(e.reason, TimeoutError):
            raise
    if _teamarr_running(url):
        return "started"
    raise RuntimeError("Teamarr did not confirm the generation started")


def _start_daily(url):
    """Like _start_generation, but brings a stale cache up to date first.

    Returns 'busy' while the cache refreshes, so a later refresh starts the generation.
    A cache that failed to refresh must not block the day's generation.
    """
    cache = _get_json(f"{url}/api/v1/cache/status")
    if cache.get("refresh_in_progress"):
        return "busy"
    if cache.get("is_stale") and not cache.get("last_error"):
        request = urllib.request.Request(f"{url}/api/v1/cache/refresh", data=b"", method="POST")
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT):
                pass
        except urllib.error.HTTPError as e:
            if e.code != 409:
                raise
        return "busy"
    return _start_generation(url)


def _now():
    try:
        from core.models import CoreSettings

        return datetime.now(ZoneInfo(CoreSettings.get_system_time_zone()))
    except Exception:
        return datetime.now()


def _watched_names(cfg):
    from django.db.models import Q
    from apps.channels.models import Stream

    groups = Q()
    for group in cfg["groups"]:
        groups |= Q(channel_group__name__iexact=group)
    streams = Stream.objects.filter(groups, m3u_account__name__iexact=cfg["account"], is_stale=False)
    return set(streams.values_list("name", flat=True))


def sync_check(cfg, names, today, state, now, start, write, clock="00:00", start_daily=None):
    """Decide what one refresh means. Returns (message, new_state or None).

    `state` is None before the first check. Names only move into the saved list once
    Teamarr accepts a run, so anything that has to wait stays new for the next refresh.
    """
    current = _relevant(names, today, cfg["today_only"])
    if state is None:
        return f"Sync: first check, saved {len(current)} stream(s) to compare against.", {
            "names": sorted(current),
            "last_run": 0,
        }
    seen = set(state["names"])
    new = sorted(current - seen)
    keep = {**state, "names": sorted(seen & current)}
    daily = cfg.get("daily_time")
    pending_daily = bool(daily) and state.get("last_daily") != today.isoformat()
    if pending_daily and clock >= daily:
        if not write:
            return "Sync: the daily run is due. Would start it.", None
        if start_daily() == "busy":
            return "Sync: daily run is due, Teamarr is busy, will retry on the next refresh.", keep
        done = {**state, "names": sorted(current), "last_run": now, "last_daily": today.isoformat()}
        return "Sync: started the daily Teamarr generation.", done
    if len(new) < cfg["min_new"]:
        return f"Sync: {len(new)} new of {len(current)} stream(s), no generation needed.", keep
    listing = f"{len(new)} new stream(s), e.g. {new[0]}"
    if pending_daily:
        return f"Sync: {listing}. Waiting for the daily run at {daily}.", keep
    if not write:
        return f"Sync: {listing}. Would start a generation.", None
    wait = cfg["cooldown"] * 60 - (now - state.get("last_run", 0))
    if wait > 0:
        return f"Sync: {listing}. Waiting, last generation was too recent.", keep
    if start() == "busy":
        return f"Sync: {listing}. Teamarr is busy, will retry on the next refresh.", keep
    return f"Sync: {listing}. Started a Teamarr generation.", {**state, "names": sorted(current), "last_run": now}


def _time_options():
    options = [{"value": "off", "label": "Off"}]
    for minutes in range(0, 24 * 60, 30):
        hour, minute = divmod(minutes, 60)
        label = f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"
        options.append({"value": f"{hour:02d}:{minute:02d}", "label": label})
    return options


def _keep_saved(options, saved):
    """Add saved values missing from the list. Dispatcharr refuses to save a multi-select
    holding a value it doesn't offer, so a removed group would otherwise block every save."""
    known = {o["value"] for o in options}
    return options + [{"value": v, "label": v} for v in saved if v and v not in known]


def _build_fields(accounts, groups, settings):
    """Manifest fields with their dropdowns filled. groups: (group, account) name pairs."""
    fields = copy.deepcopy(_MANIFEST["fields"])
    owners = {}
    for group, account in groups:
        owners.setdefault(group, []).append(account)
    group_options = [
        {"value": group, "label": f"{group} ({', '.join(sorted(owners[group]))})"}
        for group in sorted(owners, key=str.lower)
    ]
    options = {
        "sync_account": _keep_saved(
            [{"value": a, "label": a} for a in sorted(accounts, key=str.lower)],
            [(settings.get("sync_account") or "").strip()],
        ),
        "sync_groups": _keep_saved(group_options, _group_list(settings.get("sync_groups"))),
        "sync_daily_time": _keep_saved(_time_options(), [(settings.get("sync_daily_time") or "").strip()]),
    }
    for field in fields:
        if field["id"] in options:
            field["options"] = options[field["id"]]
    return fields


def _choices():
    """Accounts, groups and saved settings from Dispatcharr."""
    from apps.channels.models import ChannelGroupM3UAccount
    from apps.m3u.models import M3UAccount
    from apps.plugins.models import PluginConfig

    live = {"is_active": True, "locked": False}
    accounts = M3UAccount.objects.filter(**live).values_list("name", flat=True)
    groups = ChannelGroupM3UAccount.objects.filter(
        enabled=True, m3u_account__is_active=True, m3u_account__locked=False
    ).values_list("channel_group__name", "m3u_account__name")
    config = PluginConfig.objects.filter(key=PLUGIN_KEY).first()
    return list(accounts), list(groups), (config.settings if config else None) or {}


class Plugin:
    name = _MANIFEST["name"]
    version = _MANIFEST["version"]
    description = _MANIFEST["description"]
    author = _MANIFEST["author"]

    @property
    def fields(self):
        """Read when the plugin loads, so Reload Plugin updates the dropdowns."""
        try:
            return _build_fields(*_choices())
        except Exception:
            logging.getLogger(LOGGER_NAME).exception("Could not load the account and group lists")
            return _build_fields([], [], {})

    def run(self, action_id, params, context):
        settings = context.get("settings") or {}
        params = params or {}
        if action_id == "sync_check":
            return self._sync(settings, context, write=False)
        if action_id == "sync_now":
            return self._sync_now(settings, context)
        if params.get("event") == "m3u_refresh" and action_id == "apply":
            return self._after_refresh(settings, context, params.get("payload") or {})
        rules = _rules(settings)
        if not rules:
            return {"status": "error", "message": "No mappings set. Add lines like 'SPORTS NETWORK = Team Name'."}
        if action_id in ("apply", "preview"):
            return self._apply(rules, write=action_id == "apply")
        if action_id == "remove_tags":
            return self._remove(rules)
        return {"status": "error", "message": f"Unknown action '{action_id}'"}

    def _after_refresh(self, settings, context, payload):
        """Tag first so Teamarr sees the tags, then run the sync for the chosen account."""
        log = logging.getLogger(LOGGER_NAME)
        messages = []
        rules = _rules(settings)
        if rules:
            try:
                messages.append(self._apply(rules, write=True)["message"])
            except Exception as e:
                log.exception("Teamarr Assistant: tagging failed")
                messages.append(f"Tagging failed: {e}")
        cfg = _sync_settings(settings)
        if cfg["enabled"] and (payload.get("account_name") or "").lower() == cfg["account"].lower():
            messages.append(self._sync(settings, context, write=True)["message"])
        return {"status": "ok", "message": "\n".join(messages)}

    @staticmethod
    def _sync_problem(cfg):
        if not cfg["url"] or not cfg["account"] or not cfg["groups"]:
            return "Sync: fill in the Teamarr URL, M3U account and groups first."
        if cfg["daily_time"] is None:
            return "Sync: the daily run time must look like 00:30, or be blank."
        return None

    def _sync(self, settings, context, write):
        log = logging.getLogger(LOGGER_NAME)
        cfg = _sync_settings(settings)
        problem = self._sync_problem(cfg)
        if problem:
            return {"status": "error", "message": problem}
        data_dir = context["data_dir"]
        state = _load_state(data_dir)
        names = _watched_names(cfg)
        try:
            now = _now()
            message, new_state = sync_check(
                cfg,
                names,
                now.date(),
                state,
                time.time(),
                lambda: _start_generation(cfg["url"]),
                write,
                clock=now.strftime("%H:%M"),
                start_daily=lambda: _start_daily(cfg["url"]),
            )
        except Exception as e:
            # Nothing is saved, so the same streams count as new on the next refresh.
            log.warning("Teamarr Assistant: could not start Teamarr: %s", e)
            return {"status": "error", "message": f"Sync: could not reach Teamarr at {cfg['url']}: {e}"}
        if write and new_state is not None:
            _save_state(data_dir, new_state)
            log.info("Teamarr Assistant: %s", message)
        if not write:
            message = f"Watching {len(names)} stream(s) in {', '.join(cfg['groups'])}.\n{message}"
            if state is None:
                message += " (Preview only, nothing was saved.)"
        return {"status": "ok", "message": message}

    def _sync_now(self, settings, context):
        cfg = _sync_settings(settings)
        problem = self._sync_problem(cfg)
        if problem:
            return {"status": "error", "message": problem}
        try:
            result = _start_generation(cfg["url"])
        except Exception as e:
            return {"status": "error", "message": f"Could not reach Teamarr at {cfg['url']}: {e}"}
        if result == "busy":
            return {"status": "ok", "message": "Teamarr is already generating."}
        current = _relevant(_watched_names(cfg), _now().date(), cfg["today_only"])
        state = _load_state(context["data_dir"]) or {}
        _save_state(context["data_dir"], {**state, "names": sorted(current), "last_run": time.time()})
        return {"status": "ok", "message": "Started a Teamarr generation."}

    @staticmethod
    def _matches(rules):
        from django.db.models import Q
        from apps.channels.models import Stream

        # A stream matched by several rows ("XYZ" and "XYZ PLUS") goes to the longest pattern.
        best = {}
        for pattern, teams in rules:
            qs = Stream.objects.filter(Q(name__icontains=pattern) | Q(tvg_id__icontains=pattern.replace(" ", "")))
            for s in qs.only("id", "name", "tvg_id"):
                if s.pk not in best or len(pattern) > len(best[s.pk][0]):
                    best[s.pk] = (pattern, teams, s)
        for pattern, teams in rules:
            yield pattern, teams, [s for p, _, s in best.values() if p == pattern]

    def _apply(self, rules, write):
        from apps.channels.models import Stream

        log = logging.getLogger(LOGGER_NAME)
        lines, changed, unchanged = [], 0, 0
        all_teams = [t for _, teams in rules for t in teams]
        for pattern, teams, streams in self._matches(rules):
            if not streams:
                lines.append(f"'{pattern}': no matching streams")
                continue
            for s in streams:
                base = _strip_tags(s.name, all_teams)
                new = (base + " " + " ".join(f"({t})" for t in teams))[:255]
                if new == s.name:
                    unchanged += 1
                    continue
                changed += 1
                lines.append(f"{s.name}  ->  {new}")
                if write:
                    Stream.objects.filter(pk=s.pk).update(name=new)
        verb = "Tagged" if write else "Would tag"
        summary = f"{verb} {changed} stream(s), {unchanged} already tagged."
        if write and changed:
            log.info("Teamarr Assistant: %s", summary)
        return {"status": "ok", "message": "\n".join([summary] + lines[:40])}

    def _remove(self, rules):
        from apps.channels.models import Stream

        all_teams = [t for _, teams in rules for t in teams]
        n = 0
        for _, _, streams in self._matches(rules):
            for s in streams:
                base = _strip_tags(s.name, all_teams)
                if base != s.name:
                    Stream.objects.filter(pk=s.pk).update(name=base)
                    n += 1
        return {"status": "ok", "message": f"Removed tags from {n} stream(s)."}
