"""`alb --reply-to <id>` finds its bridge from a per-user registry.

Live test, 25 Oct: an agent read a question from a phone, answered with
`letterbox reply`, and was refused - it had never been told its bridge's
--config and --root. The running bridge now records them. These pin its
trust boundary: nothing from a letter becomes a path or a command, the
letter must be the registered bridge's own, and zero or several owners is a
refusal, never a guess.
"""
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from alb import cli, msgindex, registry  # noqa: E402
from alb.adapters.telegram import api  # noqa: E402
from alb.bridge import run  # noqa: E402
from alb.letter import store  # noqa: E402
from alb.outbound import store as outbound  # noqa: E402
from fake_platform import FakePlatform  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(os.path.realpath(self.tmp.name))
        env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.base / "xdg")})
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def bridge(self, name, mail, platform="telegram", sender="telegram-bridge",
               recipient="builder", register=True):
        """A bridge root with a loadable config, registered like a start would."""
        root = run.prepare_root(self.base / name)
        (root / "allowlist.json").write_text(json.dumps({"chats": ["111"]}),
                                             encoding="utf-8")
        env = self.base / f"{name}.env"
        env.write_text(f"ALB_TOKEN=1:x\nALB_MAIL_ROOT={mail}\nALB_TO={recipient}\n",
                       encoding="utf-8")
        env.chmod(0o600)
        if register:
            registry.register(root, env, mail, platform, sender, recipient)
        return root, env

    def mailbox(self, name="builder"):
        mail = self.base / "box" / name
        for sub in ("inbox", "processed"):
            (mail / sub).mkdir(parents=True, exist_ok=True)
        return mail

    def letter(self, folder, letter_id, sender="telegram-bridge", to="builder",
               extra="", published_by=None, platform="telegram"):
        """A letter on disk; published_by=root also records it in that
        bridge's private message index, as a real publish does."""
        if published_by is not None:
            msgindex.record(pathlib.Path(published_by) / "state", platform, "111",
                            f"m-{letter_id}", letter_id)
        path = pathlib.Path(folder) / f"{letter_id}.md"
        path.write_text(f"---\nid: {letter_id}\nfrom: {sender}\nto: {to}\n"
                        f"type: info\n{extra}---\nwhat is 7 times 8?\n",
                        encoding="utf-8")
        return path

    def refused(self, letter_id, *needles):
        with self.assertRaises(registry.RegistryError) as caught:
            registry.resolve(letter_id)
        for needle in needles:
            self.assertIn(needle, str(caught.exception))
        return str(caught.exception)


class TheRegistryFile(Base):
    def test_it_is_private_from_birth(self):
        mail = self.mailbox()
        self.bridge("alb", mail)
        folder = registry.directory()
        self.assertEqual(folder, self.base / "xdg" / "agent-letter-bridge")
        self.assertEqual(os.stat(folder).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(folder / "bridges.json").st_mode & 0o777, 0o600)

    def test_entries_are_canonical_and_keyed_by_root(self):
        mail = self.mailbox()
        root, env = self.bridge("alb", mail)
        self.bridge("alb", mail)  # a restart replaces, never duplicates
        table = json.loads((registry.directory() / "bridges.json").read_text())
        self.assertEqual(list(table), [str(root)])
        self.assertEqual(table[str(root)]["config"], str(env))
        self.assertEqual(table[str(root)]["to"], "builder")

    def test_a_relative_xdg_falls_back_to_home(self):
        self.assertEqual(
            registry.directory({"XDG_CONFIG_HOME": "rel", "HOME": "/h"}),
            pathlib.Path("/h/.config/agent-letter-bridge"))

    def test_a_linked_registry_is_refused(self):
        mail = self.mailbox()
        self.bridge("alb", mail)
        real = registry.directory() / "bridges.json"
        moved = self.base / "elsewhere.json"
        real.rename(moved)
        real.symlink_to(moved)
        self.refused("2026-10-25T120000-aaaa1111", "link")

    def test_a_linked_registry_folder_is_refused(self):
        target = self.base / "target"
        target.mkdir(mode=0o700)
        (self.base / "xdg").mkdir()
        (self.base / "xdg" / "agent-letter-bridge").symlink_to(target)
        self.refused("2026-10-25T120000-aaaa1111", "not a plain folder")

    def test_a_registry_open_to_others_is_refused(self):
        mail = self.mailbox()
        self.bridge("alb", mail)
        (registry.directory() / "bridges.json").chmod(0o644)
        self.refused("2026-10-25T120000-aaaa1111", "open to other users")
        (registry.directory() / "bridges.json").chmod(0o600)
        registry.directory().chmod(0o755)
        with self.assertRaises(registry.RegistryError):
            self.bridge("other", mail)

    def test_a_lock_open_to_others_or_linked_is_refused(self):
        mail = self.mailbox()
        self.bridge("alb", mail)
        lock = registry.directory() / "bridges.lock"
        lock.chmod(0o644)
        with self.assertRaises(registry.RegistryError):
            self.bridge("other", mail)
        lock.unlink()
        lock.symlink_to(self.base / "elsewhere.lock")
        with self.assertRaises(registry.RegistryError):
            self.bridge("other", mail)

    def test_concurrent_starts_keep_every_entry(self):
        mail = self.mailbox()
        envs = [self.bridge(f"r{i}", mail, register=False) for i in range(6)]
        code = ("import sys; from alb import registry; "
                "registry.register(sys.argv[1], sys.argv[2], sys.argv[3], "
                "'telegram', 'telegram-bridge', 'builder')")
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        procs = [subprocess.Popen([sys.executable, "-c", code, str(r), str(e), str(mail)],
                                  env=env) for r, e in envs]
        self.assertEqual([p.wait() for p in procs], [0] * 6)
        table = json.loads((registry.directory() / "bridges.json").read_text())
        self.assertEqual(sorted(table), sorted(str(r) for r, _ in envs))


class FindingTheOwner(Base):
    ID = "2026-10-25T120034-2512d004-u4763f741f128"

    def test_the_letter_names_its_bridge(self):
        mail = self.mailbox()
        root, _ = self.bridge("alb", mail)
        self.letter(mail / "inbox", self.ID, published_by=root)
        self.assertEqual(registry.resolve(self.ID)["root"], str(root))

    def test_a_letter_this_bridge_never_published_has_no_owner(self):
        """Right folder, right sender and recipient, but no publish record:
        a forged letter, refused."""
        mail = self.mailbox()
        self.bridge("alb", mail)
        self.letter(mail / "inbox", self.ID)
        self.refused(self.ID, "no registered Agent Letter Bridge")

    def test_a_filed_letter_is_still_found(self):
        mail = self.mailbox()
        root, _ = self.bridge("alb", mail)
        self.letter(mail / "processed", self.ID, published_by=root)
        self.assertEqual(registry.resolve(self.ID)["root"], str(root))

    def test_two_platforms_sharing_a_mailbox_split_by_sender(self):
        mail = self.mailbox()
        tg, _ = self.bridge("tg", mail)
        sl, _ = self.bridge("sl", mail, platform="slack", sender="slack-bridge")
        self.letter(mail / "inbox", self.ID, sender="slack-bridge", published_by=sl,
                    platform="slack")
        self.assertEqual(registry.resolve(self.ID)["root"], str(sl))

    def test_the_same_id_under_two_bridges_is_refused(self):
        a, b = self.mailbox("a"), self.mailbox("b")
        ra, _ = self.bridge("ra", a, recipient="a")
        rb, _ = self.bridge("rb", b, recipient="b")
        self.letter(a / "inbox", self.ID, to="a", published_by=ra)
        self.letter(b / "inbox", self.ID, to="b", published_by=rb)
        message = self.refused(self.ID, "2 registered bridges", "refusing to guess")
        self.assertNotIn(str(self.base), message, "never a map of config paths")

    def test_a_stale_entry_that_still_matches_is_ambiguity_not_a_guess(self):
        mail = self.mailbox()
        old, _ = self.bridge("old", mail)
        new, _ = self.bridge("new", mail)
        self.letter(mail / "inbox", self.ID, published_by=old)
        msgindex.record(new / "state", "telegram", "111", "m2", self.ID)
        self.refused(self.ID, "2 registered bridges")

    def test_a_deleted_root_is_skipped(self):
        import shutil
        mail = self.mailbox()
        old, _ = self.bridge("old", mail)
        new, _ = self.bridge("new", mail)
        shutil.rmtree(old)
        self.letter(mail / "inbox", self.ID, published_by=new)
        self.assertEqual(registry.resolve(self.ID)["root"], str(new))

    def test_a_forged_sender_or_recipient_has_no_owner(self):
        mail = self.mailbox()
        root, _ = self.bridge("alb", mail)
        self.letter(mail / "inbox", self.ID, sender="someone-else", published_by=root)
        message = self.refused(self.ID, "no registered Agent Letter Bridge")
        self.assertNotIn(str(self.base), message)
        self.letter(mail / "inbox", self.ID, to="planner")
        self.refused(self.ID, "no registered Agent Letter Bridge")

    def test_a_publish_record_for_another_platform_does_not_count(self):
        mail = self.mailbox()
        root, _ = self.bridge("alb", mail)
        self.letter(mail / "inbox", self.ID, published_by=root, platform="slack")
        self.refused(self.ID, "no registered Agent Letter Bridge")

    def test_an_unregistered_letter_has_no_owner(self):
        mail = self.mailbox()
        self.letter(mail / "inbox", self.ID)
        self.refused(self.ID, "no registered Agent Letter Bridge")

    def test_an_id_that_names_another_file_is_refused(self):
        mail = self.mailbox()
        root, _ = self.bridge("alb", mail)
        path = self.letter(mail / "inbox", self.ID, published_by=root)
        path.write_text(path.read_text().replace(f"id: {self.ID}", "id: other"))
        self.refused(self.ID, "no registered")

    def test_a_repeated_field_is_refused(self):
        mail = self.mailbox()
        self.bridge("alb", mail)
        self.letter(mail / "inbox", self.ID, extra="from: telegram-bridge\n")
        self.refused(self.ID, "repeats the field from")

    def test_a_linked_letter_is_refused(self):
        mail = self.mailbox()
        self.bridge("alb", mail)
        real = self.letter(self.base, self.ID)
        (mail / "inbox" / f"{self.ID}.md").symlink_to(real)
        self.refused(self.ID, "is a link")

    def test_a_fifo_is_refused_without_hanging(self):
        mail = self.mailbox()
        self.bridge("alb", mail)
        os.mkfifo(mail / "inbox" / f"{self.ID}.md")
        self.refused(self.ID, "not a regular file")

    def test_a_linked_inbox_is_refused(self):
        mail = self.base / "box" / "builder"
        mail.mkdir(parents=True)
        (self.base / "real-inbox").mkdir()
        (mail / "inbox").symlink_to(self.base / "real-inbox")
        self.bridge("alb", mail)
        self.letter(self.base / "real-inbox", self.ID)
        self.refused(self.ID, "mailbox folder is a link")

    def test_a_path_shaped_id_is_refused_before_any_lookup(self):
        with self.assertRaises(store.UnsafeIdentifier):
            registry.resolve("../../etc/passwd")


class TheCommandAnAgentRuns(Base):
    ID = "2026-10-25T120034-2512d004-u4763f741f128"

    def setUp(self):
        super().setUp()
        self.mail = self.mailbox()
        self.root, self.env = self.bridge("alb", self.mail)
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": ["111", "222"]}), encoding="utf-8")
        # Every platform supplies a message id; the publish records it.
        phone = {"update_id": 1, "chat_id": "111", "message_id": 5001,
                 "text": "what is 7 times 8?"}
        with mock.patch.object(run, "_bus_ring"):
            run.run_once(FakePlatform([phone]), None, "", self.root,
                         mail_root=self.mail, recipient="builder")
        [self.path] = (self.mail / "inbox").glob("*.md")
        self.letter_id = self.path.stem

    def _redirect(self):
        """Rewrite the letter to name another permitted correspondent,
        keeping id, from and to, as a mailbox writer could."""
        other = outbound.correspondent_key(self.root / "state", "222", "telegram")
        text = self.path.read_text(encoding="utf-8")
        mine = [l for l in text.split("\n") if l.startswith("correspondent: ")][0]
        self.path.write_text(text.replace(mine, f"correspondent: {other}"),
                             encoding="utf-8")

    def _main(self, *argv):
        sent, err = [], io.StringIO()
        with mock.patch.object(api.Telegram, "send",
                               lambda s, chat, text: sent.append((chat, text))), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, sent, err.getvalue()

    def test_reply_by_id_alone_reaches_the_chat(self):
        code, sent, _ = self._main("--reply-to", self.letter_id, "--text", "56")
        self.assertEqual(code, 0)
        self.assertEqual(sent, [("111", "56")])

    def test_a_letter_swapped_after_lookup_is_not_redirected(self):
        real = registry.resolve

        def resolve_then_swap(letter_id):
            entry = real(letter_id)
            self._redirect()
            return entry

        with mock.patch.object(registry, "resolve", resolve_then_swap):
            code, sent, _ = self._main("--reply-to", self.letter_id, "--text", "56")
        self.assertNotEqual(code, 0)
        self.assertEqual(sent, [])

    def test_an_edited_letter_is_refused_with_explicit_flags_too(self):
        self._redirect()
        code, sent, _ = self._main("--config", str(self.env), "--root", str(self.root),
                                   "--reply-to", self.letter_id, "--text", "56")
        self.assertNotEqual(code, 0)
        self.assertEqual(sent, [])

    def test_explicit_flags_behave_as_before(self):
        code, sent, _ = self._main("--config", str(self.env), "--root", str(self.root),
                                   "--reply-to", self.letter_id, "--text", "56")
        self.assertEqual((code, sent), (0, [("111", "56")]))

    def test_an_unowned_id_sends_nothing_and_names_no_path(self):
        code, sent, err = self._main("--reply-to", self.ID, "--text", "56")
        self.assertEqual((code, sent), (2, []))
        self.assertNotIn(str(self.base), err)

    def test_other_commands_still_require_root(self):
        with self.assertRaises(SystemExit) as caught, \
                contextlib.redirect_stderr(io.StringIO()):
            cli.main(["--status"])
        self.assertEqual(caught.exception.code, 2)

    def test_a_partial_location_is_not_completed_from_the_registry(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["--config", str(self.env), "--reply-to", self.letter_id,
                      "--text", "56"])


if __name__ == "__main__":
    unittest.main()
