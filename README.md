<img src="logo.png" alt="" width="96">

# Teamarr Assistant

A plugin for [Dispatcharr](https://github.com/Dispatcharr/Dispatcharr) that helps it work with [Teamarr](https://github.com/Pharaoh-Labs/teamarr). Formerly Team Feeds.

- **Team tags:** appends the team to regional sports network stream names, for example `SPORTS NETWORK (Team Name)`, so Teamarr can put the stream in that team's home or away feed. Tags are re-applied after every M3U refresh.
- **Teamarr sync:** starts a Teamarr generation when new streams appear in the M3U groups you choose, so channels show up soon after your provider posts them.

Tested on Dispatcharr 0.33.0 and Teamarr 2.25.1.

## Install

Copy this repository's files into `/data/plugins/teamarr_assistant` in your Dispatcharr data folder, then enable **Teamarr Assistant** on the Plugins page.

Or download `teamarr_assistant-2.0.0.1.zip` from Releases and import it on the Plugins page. Use that zip rather than GitHub's source zip, because Dispatcharr names the plugin after the zip's top folder.

Upgrading from Team Feeds: this installs as a new plugin. Copy your channel rows across, then delete Team Feeds.

## Team tags

1. Add one row per channel in the plugin settings: text to find in the stream name or tvg-id, and the full team names as Teamarr spells them, as in this example. The table starts empty.

   | Channel | Team(s) |
   |---|---|
   | `SPORTS NETWORK` | `City Team, Other City Team` |

2. Use **Preview Tags** to check the matches, then **Apply Tags**.
3. **Remove Tags** takes the tags off again.

## Teamarr sync

1. Give the M3U account a frequent refresh schedule in Dispatcharr, for example a cron of `*/15 * * * *`. Dispatcharr only sees provider changes when it refreshes.
2. Fill in the Teamarr URL, pick the M3U account and groups to watch, then turn on **Start Teamarr when new streams appear**. Press **Reload Plugin** on this plugin's page to see newly added accounts and groups in the lists.
3. Use **Check Sync** to see what it would do.

Keep Teamarr's own schedule running as well, unless you use the daily run below. Generations also create the day's channels, remove finished ones, and attach and detach time-windowed streams from linear channels.

### Plugin-only generations

To have Teamarr generate only when this plugin says so:

1. Set **Daily run time**, for example `12:30 AM`.
2. In Teamarr, set the stream pre-buffer to `1440` minutes, so time-windowed streams attach during the daily run.
3. Turn off Teamarr's scheduler.

The daily run also refreshes Teamarr's team and league cache when it is stale, then generates on a later refresh. If Dispatcharr is down, Teamarr will not generate at all until it is back.

The first refresh after turning it on only records the current streams. A generation starts when later refreshes find new names. Teamarr's API must be reachable from Dispatcharr without a login.

## Release notes

See [Releases](https://github.com/Brxdy22/Dispatcharr-Teamarr-Assistant-Plugin/releases/latest).

## Licence

MIT
