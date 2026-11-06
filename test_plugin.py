"""Run: python3 -m unittest test_plugin (from this folder)."""
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import date, datetime
from unittest import mock

import plugin as p

TODAY = date(2026, 11, 5)
CFG = {"today_only": True, "min_new": 1, "cooldown": 10}


class Dates(unittest.TestCase):
    def check(self, name, expected):
        self.assertIs(p._dated_today(name, TODAY), expected, name)

    def test_real_formats(self):
        self.check("Sports 59: A vs. B Nov 05 5:00PM ET (2026-11-05 17:00:15)", True)
        self.check("Sports 7: Basketball: A vs. B (A vs. B) (2026-11-05 11:00:20)", True)
        self.check("GROUP 03: A vs. B (NET) @ 5 Nov 01:00 PM ET", True)
        self.check("GROUP 02: Basketball (W) A at B Thu @ Nov 05 06:30PM ET", True)

    def test_other_dates(self):
        self.check("Sports 590: (2098-12-31 08:07:11)", False)
        self.check("GROUP 03: A vs. B (NET) @ 4 Nov 01:00 PM ET", False)
        self.check("Sports 1: A vs. B Nov 04 5:00PM ET (2026-11-04 17:00:15)", False)

    def test_time_is_not_a_day(self):
        # "Nov 06:00" must not read as Nov 6, and "5 Nov" is the real date.
        self.check("GROUP 01: A vs. B @ 5 Nov 06:00 PM ET", True)
        self.check("GROUP 01: A vs. B @ 6 Nov 05:00 PM ET", False)

    def test_undated(self):
        self.check("Football 27: 1pm Marshall at Old Dominion", None)
        self.check("Marshall 5 at Mayfield", None)


class Check(unittest.TestCase):
    def run_check(self, names, state, start=None, now=10000, write=True, **cfg):
        start = start or mock.Mock(return_value="started")
        return p.sync_check({**CFG, **cfg}, names, TODAY, state, now, start, write), start

    def test_first_check_only_saves(self):
        (msg, state), start = self.run_check({"A Nov 05"}, None)
        start.assert_not_called()
        self.assertEqual(state["names"], ["A Nov 05"])

    def test_nothing_new(self):
        (_, state), start = self.run_check({"A Nov 05"}, {"names": ["A Nov 05"], "last_run": 0})
        start.assert_not_called()
        self.assertEqual(state["names"], ["A Nov 05"])

    def test_new_name_starts_once(self):
        old = {"names": ["A Nov 05"], "last_run": 0}
        (_, state), start = self.run_check({"A Nov 05", "B Nov 05"}, old)
        start.assert_called_once()
        self.assertEqual(state, {"names": ["A Nov 05", "B Nov 05"], "last_run": 10000})
        (_, _), again = self.run_check({"A Nov 05", "B Nov 05"}, state, now=20000)
        again.assert_not_called()

    def test_other_day_ignored(self):
        (_, _), start = self.run_check({"A Nov 04", "B Nov 06"}, {"names": [], "last_run": 0})
        start.assert_not_called()

    def test_today_filter_off(self):
        (_, _), start = self.run_check({"B Nov 06"}, {"names": [], "last_run": 0}, today_only=False)
        start.assert_called_once()

    def test_undated_counts(self):
        (_, _), start = self.run_check({"Football 1: 1pm A at B"}, {"names": [], "last_run": 0})
        start.assert_called_once()

    def test_busy_stays_new(self):
        busy = mock.Mock(return_value="busy")
        old = {"names": [], "last_run": 0}
        (_, state), _ = self.run_check({"B Nov 05"}, old, start=busy)
        self.assertEqual(state["names"], [])
        (_, _), start = self.run_check({"B Nov 05"}, state)
        start.assert_called_once()

    def test_cooldown_stays_new(self):
        old = {"names": [], "last_run": 9900}
        (_, state), start = self.run_check({"B Nov 05"}, old)
        start.assert_not_called()
        self.assertEqual(state["names"], [])
        self.assertEqual(state["last_run"], 9900)

    def test_min_new_accumulates(self):
        (_, state), start = self.run_check({"A Nov 05"}, {"names": [], "last_run": 0}, min_new=2)
        start.assert_not_called()
        (_, _), start = self.run_check({"A Nov 05", "B Nov 05"}, state, min_new=2)
        start.assert_called_once()

    def test_removed_names_forgotten(self):
        (_, state), _ = self.run_check(set(), {"names": ["A Nov 05"], "last_run": 5})
        self.assertEqual(state, {"names": [], "last_run": 5})

    def test_preview_never_starts_or_saves(self):
        (_, state), start = self.run_check({"B Nov 05"}, {"names": [], "last_run": 0}, write=False)
        start.assert_not_called()
        self.assertIsNone(state)

    def test_failure_propagates(self):
        boom = mock.Mock(side_effect=OSError("down"))
        with self.assertRaises(OSError):
            self.run_check({"B Nov 05"}, {"names": [], "last_run": 0}, start=boom)


class Daily(unittest.TestCase):
    def go(self, state, clock, names=frozenset({"A Nov 05"}), write=True, daily_result="started"):
        start = mock.Mock(return_value="started")
        daily = mock.Mock(return_value=daily_result)
        cfg = {**CFG, "daily_time": "00:30"}
        out = p.sync_check(cfg, set(names), TODAY, state, 10000, start, write, clock=clock, start_daily=daily)
        return out, start, daily

    def test_due_runs_once(self):
        (_, state), start, daily = self.go({"names": ["A Nov 05"], "last_run": 0}, "00:30")
        daily.assert_called_once()
        start.assert_not_called()
        self.assertEqual(state["last_daily"], "2026-11-05")
        (_, _), _, daily = self.go(state, "00:45")
        daily.assert_not_called()

    def test_catches_up_late(self):
        (_, _), _, daily = self.go({"names": [], "last_run": 0, "last_daily": "2026-11-03"}, "09:15")
        daily.assert_called_once()

    def test_ignores_cooldown(self):
        (_, _), _, daily = self.go({"names": [], "last_run": 9999}, "00:30")
        daily.assert_called_once()

    def test_midnight_names_wait(self):
        (_, state), start, daily = self.go({"names": [], "last_run": 0}, "00:00")
        start.assert_not_called()
        daily.assert_not_called()
        self.assertEqual(state["names"], [])

    def test_busy_retries(self):
        (_, state), _, _ = self.go({"names": [], "last_run": 0}, "00:30", daily_result="busy")
        self.assertNotIn("last_daily", state)
        (_, _), _, daily = self.go(state, "00:45")
        daily.assert_called_once()

    def test_new_after_daily_runs_normally(self):
        state = {"names": [], "last_run": 0, "last_daily": "2026-11-05"}
        (_, new), start, _ = self.go(state, "14:00")
        start.assert_called_once()
        self.assertEqual(new["last_daily"], "2026-11-05")

    def test_preview_writes_nothing(self):
        (_, state), _, daily = self.go({"names": [], "last_run": 0}, "00:30", write=False)
        daily.assert_not_called()
        self.assertIsNone(state)

    def test_off_when_blank(self):
        start = mock.Mock(return_value="started")
        p.sync_check({**CFG, "daily_time": ""}, {"A Nov 05"}, TODAY, {"names": [], "last_run": 0}, 10000, start, True)
        start.assert_called_once()

    def test_clock(self):
        self.assertEqual(p._clock("0:30"), "00:30")
        self.assertEqual(p._clock(" "), "")
        self.assertIsNone(p._clock("25:00"))
        self.assertIsNone(p._clock("noon"))


class Starting(unittest.TestCase):
    def response(self, body=b"{}"):
        r = mock.MagicMock()
        r.__enter__.return_value = r
        r.read.return_value = body
        return r

    def status(self, running):
        return self.response(json.dumps({"in_progress": running}).encode())

    def test_busy_skips_post(self):
        with mock.patch("urllib.request.urlopen", side_effect=[self.status(True)]) as u:
            self.assertEqual(p._start_generation("http://t"), "busy")
        self.assertEqual(u.call_count, 1)

    def test_fast_finish(self):
        with mock.patch("urllib.request.urlopen", side_effect=[self.status(False), self.response()]):
            self.assertEqual(p._start_generation("http://t"), "started")

    def test_conflict_is_busy(self):
        err = urllib.error.HTTPError("u", 409, "x", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=[self.status(False), err]):
            self.assertEqual(p._start_generation("http://t"), "busy")

    def test_timeout_confirmed_by_status(self):
        for timeout in (TimeoutError(), urllib.error.URLError(TimeoutError())):
            with mock.patch("urllib.request.urlopen", side_effect=[self.status(False), timeout, self.status(True)]):
                self.assertEqual(p._start_generation("http://t"), "started")

    def test_timeout_not_confirmed_raises(self):
        with mock.patch("urllib.request.urlopen", side_effect=[self.status(False), TimeoutError(), self.status(False)]):
            with self.assertRaises(RuntimeError):
                p._start_generation("http://t")


class Cache(unittest.TestCase):
    def response(self, data):
        r = mock.MagicMock()
        r.__enter__.return_value = r
        r.read.return_value = json.dumps(data).encode()
        return r

    def test_stale_starts_refresh_then_waits(self):
        calls = [self.response({"is_stale": True}), self.response({})]
        with mock.patch("urllib.request.urlopen", side_effect=calls) as u, \
                mock.patch.object(p, "_start_generation") as g:
            self.assertEqual(p._start_daily("http://t"), "busy")
        g.assert_not_called()
        self.assertEqual(u.call_args_list[1].args[0].get_method(), "POST")

    def test_refreshing_waits(self):
        with mock.patch("urllib.request.urlopen", side_effect=[self.response({"refresh_in_progress": True})]), \
                mock.patch.object(p, "_start_generation") as g:
            self.assertEqual(p._start_daily("http://t"), "busy")
        g.assert_not_called()

    def test_fresh_generates(self):
        with mock.patch("urllib.request.urlopen", side_effect=[self.response({"is_stale": False})]), \
                mock.patch.object(p, "_start_generation", return_value="started"):
            self.assertEqual(p._start_daily("http://t"), "started")

    def test_failed_cache_does_not_block(self):
        with mock.patch("urllib.request.urlopen", side_effect=[self.response({"is_stale": True, "last_error": "x"})]), \
                mock.patch.object(p, "_start_generation", return_value="started"):
            self.assertEqual(p._start_daily("http://t"), "started")


class Refresh(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ctx = lambda s: {"settings": s, "data_dir": self.dir}
        self.on = {"sync_enabled": True, "teamarr_url": "http://t", "sync_account": "Prov", "sync_groups": "G"}

    def event(self, settings, account="Prov"):
        params = {"event": "m3u_refresh", "payload": {"account_name": account}}
        with mock.patch.object(p.Plugin, "_apply", return_value={"message": "tagged"}) as a, \
                mock.patch.object(p.Plugin, "_sync", return_value={"message": "synced"}) as s:
            result = p.Plugin().run("apply", params, self.ctx(settings))
        return a, s, result

    def test_tags_then_sync(self):
        a, s, result = self.event({**self.on, "channels": [{"channel": "X", "teams": "Y"}]})
        a.assert_called_once()
        s.assert_called_once()
        self.assertEqual(result["message"], "tagged\nsynced")

    def test_other_account_no_sync(self):
        _, s, _ = self.event(self.on, account="Other")
        s.assert_not_called()

    def test_disabled_no_sync(self):
        _, s, _ = self.event({**self.on, "sync_enabled": False})
        s.assert_not_called()

    def test_no_rules_still_syncs(self):
        _, s, result = self.event({**self.on, "channels": []})
        s.assert_called_once()
        self.assertEqual(result["status"], "ok")

    def test_tag_failure_still_syncs(self):
        with mock.patch.object(p.Plugin, "_apply", side_effect=ValueError("x")), \
                mock.patch.object(p.Plugin, "_sync", return_value={"message": "synced"}) as s:
            r = p.Plugin().run("apply", {"event": "m3u_refresh", "payload": {"account_name": "Prov"}},
                               self.ctx({**self.on, "channels": [{"channel": "X", "teams": "Y"}]}))
        s.assert_called_once()
        self.assertIn("Tagging failed", r["message"])

    def test_manual_apply_does_not_sync(self):
        with mock.patch.object(p.Plugin, "_apply", return_value={"message": "t"}), \
                mock.patch.object(p.Plugin, "_sync") as s:
            p.Plugin().run("apply", {}, self.ctx({**self.on, "channels": [{"channel": "X", "teams": "Y"}]}))
        s.assert_not_called()

    def test_failed_start_saves_nothing(self):
        p._save_state(self.dir, {"names": [], "last_run": 0})
        with mock.patch.object(p, "_watched_names", return_value={"B Nov 05"}), \
                mock.patch.object(p, "_now", return_value=datetime(2026, 11, 5, 12, 0)), \
                mock.patch.object(p, "_start_generation", side_effect=OSError("down")):
            r = p.Plugin()._sync(self.on, self.ctx(self.on), write=True)
        self.assertEqual(r["status"], "error")
        self.assertEqual(p._load_state(self.dir)["names"], [])

    def test_missing_settings(self):
        r = p.Plugin().run("sync_check", {}, self.ctx({}))
        self.assertEqual(r["status"], "error")

    def test_corrupt_state_is_first_check(self):
        with open(os.path.join(self.dir, p.STATE_FILE), "w") as f:
            f.write("{bad")
        self.assertIsNone(p._load_state(self.dir))


class Dropdowns(unittest.TestCase):
    def build(self, accounts=(), groups=(), settings=None):
        return {f["id"]: f for f in p._build_fields(list(accounts), list(groups), settings or {})}

    def values(self, field):
        return [o["value"] for o in field["options"]]

    def test_accounts_and_groups(self):
        fields = self.build(["b", "A"], [("Zed", "A"), ("news", "A"), ("news", "b")])
        self.assertEqual(self.values(fields["sync_account"]), ["A", "b"])
        self.assertEqual(
            fields["sync_groups"]["options"],
            [{"value": "news", "label": "news (A, b)"}, {"value": "Zed", "label": "Zed (A)"}],
        )

    def test_saved_values_stay_offered(self):
        saved = {"sync_account": "Gone", "sync_groups": ["Old", "Live"], "sync_daily_time": "07:45"}
        fields = self.build(["A"], [("Live", "A")], saved)
        self.assertEqual(self.values(fields["sync_account"]), ["A", "Gone"])
        self.assertEqual(self.values(fields["sync_groups"]), ["Live", "Old"])
        self.assertIn("07:45", self.values(fields["sync_daily_time"]))

    def test_old_text_groups_stay_offered(self):
        fields = self.build(settings={"sync_groups": "A, B"})
        self.assertEqual(self.values(fields["sync_groups"]), ["A", "B"])

    def test_no_blank_option_values(self):
        # Dispatcharr's field check drops a whole field over one blank option value.
        for field in self.build(settings={"sync_account": "", "sync_daily_time": ""}).values():
            self.assertTrue(all(o["value"] for o in field.get("options", [])), field["id"])

    def test_times(self):
        options = {o["value"]: o["label"] for o in p._time_options()}
        self.assertEqual(len(options), 49)
        self.assertEqual(options["off"], "Off")
        self.assertEqual(options["00:30"], "12:30 AM")
        self.assertEqual(options["12:00"], "12:00 PM")
        self.assertEqual(options["23:30"], "11:30 PM")

    def test_settings_read_both_forms(self):
        self.assertEqual(p._sync_settings({"sync_groups": ["A, B", " C "]})["groups"], ["A, B", "C"])
        self.assertEqual(p._sync_settings({"sync_groups": "A, B"})["groups"], ["A", "B"])
        self.assertEqual(p._sync_settings({})["groups"], [])

    def test_daily_off(self):
        self.assertEqual(p._sync_settings({"sync_daily_time": "off"})["daily_time"], "")
        self.assertEqual(p._sync_settings({})["daily_time"], "")
        self.assertEqual(p._sync_settings({"sync_daily_time": "00:30"})["daily_time"], "00:30")

    def test_lists_failing_keeps_plugin_loading(self):
        with mock.patch.object(p, "_choices", side_effect=RuntimeError):
            with self.assertLogs(p.LOGGER_NAME, "ERROR"):
                fields = {f["id"]: f for f in p.Plugin().fields}
        self.assertEqual(fields["sync_groups"]["options"], [])
        self.assertEqual(len(fields["sync_daily_time"]["options"]), 49)


class Manifest(unittest.TestCase):
    def test_no_provider_defaults(self):
        fields = {f["id"]: f for f in p._MANIFEST["fields"]}
        self.assertFalse(fields["sync_enabled"]["default"])
        self.assertEqual(fields["sync_account"]["default"], "")
        self.assertEqual(fields["sync_groups"]["default"], [])
        self.assertEqual(fields["sync_groups"]["options"], [])
        self.assertEqual(fields["sync_account"]["options"], [])

    def test_event_on_apply_only(self):
        with_events = [a["id"] for a in p._MANIFEST["actions"] if a.get("events")]
        self.assertEqual(with_events, ["apply"])


if __name__ == "__main__":
    unittest.main()
