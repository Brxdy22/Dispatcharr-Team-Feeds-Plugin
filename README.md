<img src="logo.png" alt="" width="96">

# Team Feeds

A plugin for [Dispatcharr](https://github.com/Dispatcharr/Dispatcharr).

Tells [Teamarr](https://github.com/Pharaoh-Labs/teamarr) which team a regional sports network carries. It appends the team to matching stream names, for example `SPACE CITY HOME NETWORK (Houston Rockets)`, so Teamarr can put the stream in that team's home or away feed. Tags are re-applied after every M3U refresh.

Tested on Dispatcharr 0.33.0.

## Install

Copy this repository's files into `/data/plugins/team_feeds` in your Dispatcharr data folder, then enable **Team Feeds** on the Plugins page.

Or download `team_feeds-1.1.0.zip` from Releases and import it on the Plugins page. Use that zip rather than GitHub's source zip, because Dispatcharr names the plugin after the zip's top folder.

## Use

1. Add one row per channel in the plugin settings: text to find in the stream name or tvg-id, and the full team names as Teamarr spells them, for example `YES NETWORK` and `Brooklyn Nets, New York Yankees`.
2. Use **Preview** to check the matches, then **Apply Now**.
3. **Remove Tags** takes the tags off again.

## Changelog

### 1.1.0
- Channels are now set in a table, one row per channel, instead of a text box. Rules from 1.0 are still read.
- Added the "How to fill this in" help to the settings.
- A stream that matches several rows goes to the longest match, so `MSGSN` beats `MSG`.

### 1.0.0
- First version: tags matching streams with their teams, and tags them again after each M3U refresh.
- Added **Apply Now**, **Preview** and **Remove Tags**.

## Licence

MIT
