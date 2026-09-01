"""The two lookups, and the properties that make them safe to offer."""
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.setup import discover  # noqa: E402


class ReadingChatIdsConsumesNothing(unittest.TestCase):
    def _read(self, result):
        seen = {}

        def fake_request(base, method, params, token, timeout=None):
            seen["method"] = method
            seen["params"] = params
            return {"result": result}

        with mock.patch.object(discover.api, "_request", fake_request):
            found = discover.read_chat_ids("1:x")
        return found, seen

    def test_no_offset_is_sent(self):
        """The platform consumes everything at or below the mark it is given.
        A call that sends no mark cannot advance one - which is what makes this
        safe to offer during setup, before the bridge has ever run."""
        _, seen = self._read([])
        self.assertNotIn("offset", seen["params"])
        self.assertEqual(seen["method"], "getUpdates")

    def test_the_chat_id_is_returned_not_the_from_id(self):
        """The trap this exists to remove. Both ids are in the payload and they
        are the same number in a direct message, so returning the wrong one
        passes every test a person would think to run by hand."""
        found, _ = self._read([{
            "update_id": 1,
            "message": {"chat": {"id": 111, "first_name": "Sam"},
                        "from": {"id": 999, "first_name": "Sam"},
                        "text": "hi"},
        }])
        self.assertEqual([entry["chat_id"] for entry in found], ["111"])

    def test_a_group_returns_the_group_not_the_person(self):
        found, _ = self._read([{
            "update_id": 1,
            "message": {"chat": {"id": -100, "title": "Ops", "type": "group"},
                        "from": {"id": 999, "first_name": "Sam"}},
        }])
        self.assertEqual(found[0]["chat_id"], "-100")
        self.assertEqual(found[0]["label"], "Ops")

    def test_duplicates_collapse(self):
        message = {"chat": {"id": 111, "first_name": "Sam"}}
        found, _ = self._read([{"update_id": 1, "message": message},
                               {"update_id": 2, "message": message}])
        self.assertEqual(len(found), 1)

    def test_an_update_with_no_chat_is_skipped(self):
        found, _ = self._read([{"update_id": 1, "edited_message": {}}])
        self.assertEqual(found, [])


class ListingPanesNeverChooses(unittest.TestCase):
    def test_a_missing_multiplexer_is_not_an_error(self):
        with mock.patch.object(discover.subprocess, "run", side_effect=OSError):
            self.assertEqual(discover.list_panes("tmux"), [])

    def test_tmux_panes_are_listed_verbatim(self):
        completed = mock.Mock(returncode=0, stdout="%1\tmain:0.0 zsh\n%2\tmain:0.1 python\n")
        with mock.patch.object(discover.subprocess, "run", return_value=completed):
            panes = discover.list_panes("tmux")
        self.assertEqual([p["id"] for p in panes], ["%1", "%2"])

    def test_a_nonzero_exit_yields_nothing(self):
        completed = mock.Mock(returncode=1, stdout="garbage")
        with mock.patch.object(discover.subprocess, "run", return_value=completed):
            self.assertEqual(discover.list_panes("tmux"), [])


class CmuxPanesAreParsedNotDumped(unittest.TestCase):
    """Found by running it, not by reading it.

    The suite covered tmux, whose output is a chosen format string, and treated
    cmux the same way. cmux prints a TREE - box-drawing characters, nesting,
    several id-bearing lines per pane - so splitting on whitespace and taking
    the first field offers the operator a list of '|--' to paste into their
    config.
    """

    TREE = (
        'window ccb180a3-0000-4000-8000-000000000001 [current]\n'
        '├──  workspace 0fa0dbe0-0000-4000-8000-000000000002 "Agent Grid"\n'
        '│  ├── pane 6bbf5d0d-0000-4000-8000-000000000003\n'
        '│  │   └── surface ce966abe-0000-4000-8000-000000000004 '
        '[terminal] "an agent" [selected] tty=ttys000\n'
        '│  ├── pane c43ce8a0-0000-4000-8000-000000000005\n'
        '│  │   └── surface a6289ae8-0000-4000-8000-000000000006 '
        '[terminal] "another agent" [selected] tty=ttys001\n'
    )

    def _panes(self):
        completed = mock.Mock(returncode=0, stdout=self.TREE)
        with mock.patch.object(discover.subprocess, "run", return_value=completed):
            return discover.list_panes("cmux")

    def test_only_surfaces_are_offered(self):
        """A window id and a pane id are both real ids and neither is what the
        ring types into. Offering them is offering a value that fails."""
        ids = [p["id"] for p in self._panes()]
        self.assertEqual(ids, ["ce966abe-0000-4000-8000-000000000004",
                               "a6289ae8-0000-4000-8000-000000000006"])

    def test_no_tree_drawing_is_ever_offered_as_an_id(self):
        for pane in self._panes():
            self.assertNotIn("─", pane["id"])
            self.assertNotIn("├", pane["id"])
            self.assertTrue(pane["id"].replace("-", "").isalnum())

    def test_the_title_is_kept_as_the_label(self):
        """The operator picks by recognising their agent, so the label is the
        whole reason the listing is worth showing."""
        self.assertEqual(self._panes()[0]["label"], "an agent")

    def test_uppercase_ids_parse_too(self):
        """cmux emits uppercase. The fixture above is lowercased because the
        privacy scan refuses uppercase UUID literals in this repo on sight -
        which is the right blunt rule - so the case this actually ships against
        is built here rather than written out."""
        completed = mock.Mock(returncode=0, stdout=self.TREE.upper().replace(
            "SURFACE ", "surface ").replace("[TERMINAL]", "[terminal]"))
        with mock.patch.object(discover.subprocess, "run", return_value=completed):
            panes = discover.list_panes("cmux")
        self.assertEqual(len(panes), 2)
        self.assertTrue(panes[0]["id"].isupper())

    def test_an_unparseable_tree_offers_nothing_rather_than_rubbish(self):
        completed = mock.Mock(returncode=0, stdout="something we do not understand\n")
        with mock.patch.object(discover.subprocess, "run", return_value=completed):
            self.assertEqual(discover.list_panes("cmux"), [])
