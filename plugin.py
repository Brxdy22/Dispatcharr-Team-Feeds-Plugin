"""Team Feeds: tells Teamarr which team a regional sports network carries.

Teamarr's feed separation can't tell that "SPACE CITY HOME NETWORK" is the Rockets'
broadcaster when ESPN's broadcast data doesn't list it (Teamarr issue #1172). It does
recognise a team name in parentheses in the stream name, "... (Houston Rockets)", and
then files the stream under that team's home or away feed depending on the game.

This plugin appends those tags. Dispatcharr identifies streams by URL (M3U hash key),
so a renamed stream keeps its channel assignments; each M3U refresh writes the
provider's name back, so the tags are re-applied after every refresh (m3u_refresh).
A channel that carries several teams (YES: Nets and Yankees) gets one tag per team;
Teamarr only uses the one matching the game's teams.
"""
import json
import os
import re

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin.json"), encoding="utf-8") as _f:
    _MANIFEST = json.load(_f)

LOGGER_NAME = "apps.plugins.team_feeds"


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


class Plugin:
    name = _MANIFEST["name"]
    version = _MANIFEST["version"]
    description = _MANIFEST["description"]
    author = _MANIFEST["author"]

    def run(self, action_id, params, context):
        rules = _rules(context.get("settings") or {})
        if not rules:
            return {"status": "error", "message": "No mappings set. Add lines like 'YES NETWORK = Brooklyn Nets'."}
        if action_id in ("apply", "preview"):
            return self._apply(rules, write=action_id == "apply")
        if action_id == "remove_tags":
            return self._remove(rules)
        return {"status": "error", "message": f"Unknown action '{action_id}'"}

    @staticmethod
    def _matches(rules):
        from django.db.models import Q
        from apps.channels.models import Stream

        # A stream matched by several rows ("MSG" and "MSGSN") goes to the longest pattern.
        best = {}
        for pattern, teams in rules:
            qs = Stream.objects.filter(Q(name__icontains=pattern) | Q(tvg_id__icontains=pattern.replace(" ", "")))
            for s in qs.only("id", "name", "tvg_id"):
                if s.pk not in best or len(pattern) > len(best[s.pk][0]):
                    best[s.pk] = (pattern, teams, s)
        for pattern, teams in rules:
            yield pattern, teams, [s for p, _, s in best.values() if p == pattern]

    def _apply(self, rules, write):
        import logging

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
            log.info("Team Feeds: %s", summary)
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
