# Team Feeds

A plugin for [Dispatcharr](https://github.com/Dispatcharr/Dispatcharr).

Tells [Teamarr](https://github.com/Pharaoh-Labs/teamarr) which team a regional sports network carries. It appends the team to matching stream names, for example `SPACE CITY HOME NETWORK (Houston Rockets)`, so Teamarr can put the stream in that team's home or away feed. Tags are re-applied after every M3U refresh.

## Install

Copy this repository's files into `/data/plugins/team_feeds` in your Dispatcharr data folder, then enable **Team Feeds** on the Plugins page.

## Use

1. Add rules in the plugin settings, one per line: `PATTERN = Team A, Team B`.
2. Use **Preview** to check the matches, then **Apply Now**.
3. **Remove Tags** takes the tags off again.

## Licence

MIT
