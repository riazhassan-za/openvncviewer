"""The recent-servers list: storage, ordering, and surviving a mangled file."""

import json
import tempfile
import unittest
from pathlib import Path

from openvncviewer.history import MAX_ENTRIES, ServerHistory


class ServerHistoryTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "servers.json"
        self.addCleanup(self.directory.cleanup)

    def test_starts_empty_when_there_is_no_file(self):
        history = ServerHistory(self.path)
        self.assertEqual(history.entries(), [])
        self.assertEqual(history.hosts(), [])

    def test_remembers_across_instances(self):
        history = ServerHistory(self.path)
        history.remember("mac.local", 5900, "someone", "Studio Mac")

        reloaded = ServerHistory(self.path)
        self.assertEqual(reloaded.hosts(), ["mac.local"])
        entry = reloaded.find("mac.local")
        self.assertEqual(entry["port"], 5900)
        self.assertEqual(entry["username"], "someone")
        self.assertEqual(entry["name"], "Studio Mac")

    def test_most_recent_first_and_no_duplicates(self):
        history = ServerHistory(self.path)
        for host in ("one", "two", "three"):
            history.remember(host, 5900)
        self.assertEqual(history.hosts(), ["three", "two", "one"])

        history.remember("one", 5901, "user", "First")
        self.assertEqual(history.hosts(), ["one", "three", "two"],
                         "reconnecting should promote, not duplicate")
        self.assertEqual(history.find("one")["port"], 5901,
                         "details should be refreshed on reconnect")

    def test_list_is_capped(self):
        history = ServerHistory(self.path)
        for index in range(MAX_ENTRIES + 10):
            history.remember(f"host{index}", 5900)
        self.assertEqual(len(history.entries()), MAX_ENTRIES)
        self.assertEqual(history.hosts()[0], f"host{MAX_ENTRIES + 9}")
        self.assertIsNone(history.find("host0"), "oldest should fall off")

    def test_remove_takes_one_server_and_leaves_the_rest(self):
        history = ServerHistory(self.path)
        for host in ("one", "two", "three"):
            history.remember(host, 5900)

        self.assertTrue(history.remove("two"))
        self.assertEqual(history.hosts(), ["three", "one"])
        self.assertEqual(ServerHistory(self.path).hosts(), ["three", "one"],
                         "removal did not reach the file")

    def test_removing_repeatedly_empties_the_list(self):
        """Three clicks of Remove Server should remove three servers."""
        history = ServerHistory(self.path)
        for host in ("one", "two", "three"):
            history.remember(host, 5900)
        for _ in range(3):
            history.remove(history.hosts()[0])
        self.assertEqual(history.entries(), [])
        self.assertEqual(ServerHistory(self.path).entries(), [])

    def test_removing_something_absent_reports_it_and_changes_nothing(self):
        history = ServerHistory(self.path)
        history.remember("one", 5900)
        self.assertFalse(history.remove("never-seen"))
        self.assertFalse(history.remove(""))
        self.assertEqual(history.hosts(), ["one"])

    def test_blank_hosts_are_ignored(self):
        history = ServerHistory(self.path)
        history.remember("", 5900)
        history.remember("   ", 5900)
        self.assertEqual(history.entries(), [])

    def test_hosts_are_trimmed(self):
        history = ServerHistory(self.path)
        history.remember("  mac.local  ", 5900)
        self.assertEqual(history.hosts(), ["mac.local"])
        self.assertIsNotNone(history.find("mac.local"))

    def test_nothing_is_stored_unless_a_token_is_given(self):
        history = ServerHistory(self.path)
        history.remember("mac.local", 5900, "someone", "Studio Mac")
        self.assertEqual(history.find("mac.local")["password"], "")

    def test_an_opaque_token_round_trips_untouched(self):
        """This module never encrypts or decrypts; it just carries the token."""
        history = ServerHistory(self.path)
        history.remember("mac.local", 5900, "u", "N", password="AQAAtoken==")
        self.assertEqual(ServerHistory(self.path).find("mac.local")["password"],
                         "AQAAtoken==")

    def test_reconnecting_without_saving_forgets_the_old_password(self):
        """Unticking the box must actually drop what was stored before."""
        history = ServerHistory(self.path)
        history.remember("mac.local", 5900, "u", "N", password="AQAAtoken==")
        history.remember("mac.local", 5900, "u", "N", password="")
        self.assertEqual(history.find("mac.local")["password"], "")
        self.assertNotIn("AQAAtoken", self.path.read_text(encoding="utf-8"))

    def test_forgetting_a_password_keeps_the_entry_and_its_place(self):
        """Revocation is not a history update: it must not reorder the list."""
        history = ServerHistory(self.path)
        history.remember("first", 5900, "amy", "First", "token-1")
        history.remember("second", 5900, "bo", "Second", "token-2")
        self.assertEqual(history.hosts(), ["second", "first"])

        self.assertTrue(history.forget_password("first"))

        self.assertEqual(history.hosts(), ["second", "first"],
                         "revoking a password promoted the server")
        entry = history.find("first")
        self.assertEqual(entry["password"], "")
        self.assertEqual(entry["username"], "amy", "metadata was discarded")
        self.assertEqual(entry["name"], "First")
        on_disk = json.loads(self.path.read_text())["servers"]
        self.assertEqual([s["host"] for s in on_disk], ["second", "first"])
        self.assertEqual(on_disk[1]["password"], "", "the token is still on disk")

    def test_forgetting_a_password_reports_whether_there_was_one(self):
        history = ServerHistory(self.path)
        history.remember("host", 5900, password="token")
        self.assertTrue(history.forget_password("host"))
        self.assertFalse(history.forget_password("host"), "nothing left to do")
        self.assertFalse(history.forget_password("never-seen"))

    def test_removing_a_server_takes_its_password_off_disk(self):
        history = ServerHistory(self.path)
        history.remember("mac.local", 5900, "u", "N", password="AQAAtoken==")
        history.remove("mac.local")
        self.assertNotIn("AQAAtoken", self.path.read_text(encoding="utf-8"))

    def test_a_corrupt_file_does_not_stop_the_viewer(self):
        for junk in ("not json at all", "[]", '{"servers": "nonsense"}', ""):
            with self.subTest(junk=junk):
                self.path.write_text(junk, encoding="utf-8")
                self.assertEqual(ServerHistory(self.path).entries(), [])

    def test_junk_entries_are_dropped_and_bad_ports_defaulted(self):
        self.path.write_text(json.dumps({"servers": [
            "a bare string",
            {"no_host": True},
            {"host": "good.local", "port": 999999, "username": 5, "name": None},
            {"host": "fine.local", "port": 5901, "username": "u", "name": "N"},
        ]}), encoding="utf-8")

        history = ServerHistory(self.path)
        self.assertEqual(history.hosts(), ["good.local", "fine.local"])
        self.assertEqual(history.find("good.local")["port"], 5900,
                         "an out-of-range port should fall back to the default")
        self.assertEqual(history.find("good.local")["username"], "")
        self.assertEqual(history.find("good.local")["name"], "")
        self.assertEqual(history.find("good.local")["password"], "")

    def test_an_unwritable_location_is_survivable(self):
        """Losing the list is a nuisance; failing to connect would be worse."""
        history = ServerHistory(self.path)
        # An embedded null cannot be created on any platform. Kept inside the
        # temporary directory so the scratch file save() makes lands there and
        # not in the working tree.
        history.path = Path(self.directory.name) / "\x00invalid"
        history.remember("mac.local", 5900)  # must not raise
        self.assertEqual(history.hosts(), ["mac.local"])

    def test_a_failed_save_leaves_no_scratch_file_behind(self):
        history = ServerHistory(self.path)
        history.path = Path(self.directory.name) / "\x00invalid"
        history.remember("mac.local", 5900)
        leftovers = list(Path(self.directory.name).glob("*.tmp"))
        self.assertEqual(leftovers, [], "a temporary file was orphaned")

    def test_entries_are_copies(self):
        history = ServerHistory(self.path)
        history.remember("mac.local", 5900, name="Original")
        history.entries()[0]["name"] = "Mutated"
        self.assertEqual(history.find("mac.local")["name"], "Original")


if __name__ == "__main__":
    unittest.main()
