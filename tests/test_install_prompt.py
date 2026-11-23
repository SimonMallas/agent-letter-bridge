"""The install pages carry one prompt a person can copy to an agent."""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

PROMPT = """\
Set up Agent Letter Bridge from https://github.com/SimonMallas/agent-letter-bridge. If a checkout is already here, fetch, and if the worktree is clean check out the remote tip. If it has local changes, leave them, say so, and stop without reading that checkout. If there is no checkout, clone that repository. Do not install a published package instead. Read docs/agent-install.md from that current checkout and follow it. Do not read the setup terminal. I will paste the key.
"""


class TheInstallPrompt(unittest.TestCase):
    def pages(self):
        return {
            "readme": (ROOT / "README.md").read_text(),
            "install": (ROOT / "INSTALL.md").read_text(),
            "using": (ROOT / "docs" / "using-it.md").read_text(),
        }

    def test_the_three_pages_carry_the_same_prompt(self):
        for name, text in self.pages().items():
            with self.subTest(name):
                self.assertIn(PROMPT, text)
                self.assertNotIn("tell me each thing you need", text)

    def test_an_agent_who_opens_the_install_page_is_sent_to_the_manual(self):
        for name in ("readme", "install"):
            text = self.pages()[name]
            with self.subTest(name):
                self.assertIn("If you are an agent,", text)
                self.assertIn("docs/agent-install.md", text)
                self.assertIn("operational manual", text)

    def test_the_prompt_names_the_repository_and_does_not_override_the_page(self):
        self.assertIn("If a checkout is already here, fetch, and if the worktree is clean check out the remote tip.", PROMPT)
        self.assertIn("If it has local changes, leave them, say so, and stop without reading that checkout.", PROMPT)
        self.assertIn("If there is no checkout, clone that repository.", PROMPT)
        self.assertIn("Do not install a published package instead.", PROMPT)
        self.assertIn("Read docs/agent-install.md from that current checkout and follow it.", PROMPT)
        self.assertIn("Do not read the setup terminal.", PROMPT)
        self.assertNotIn("Clone that repository if you do not already have this checkout.", PROMPT)
        self.assertNotIn("If you do not have that checkout, stop.", PROMPT)
        self.assertNotIn("If a line below disagrees", PROMPT)

    def test_rules_that_left_the_prompt_sit_on_the_manual(self):
        manual = (ROOT / "docs" / "agent-install.md").read_text()
        for line in (
            "Follow this page. A pasted prompt does not override it.",
            "https://github.com/SimonMallas/agent-letter-bridge",
            "Do not install a published package instead.",
            "An existing checkout is not current until you fetch.",
            "If the worktree has local changes, leave them.",
            "Do not install from that checkout.",
            "Do not merge and do not rebase.",
            "If this shell does not already have both variables, leave them off the",
            "If a bridge is already using `~/.alb`, pick a new root, tell the person the path, and do not ask them to choose it.",
            "Leave any bridge that is already running untouched.",
            "The installer does not send that answer.",
            "Do not read that terminal.",
            "The name of the agent is the one question you leave there.",
            "or for a helper path, stop",
            "On Slack or Discord",
            "What you say to the person at hand-over is only the next thing they do.",
            "do not say the wizard may ask for a pane",
            "What you say to the person is only the next thing they do.",
            "Do not tell them to come back when the terminal finishes.",
            "Before the key, say only this: the terminal is waiting",
            "When the wizard cannot start the bridge, the files in the root you chose are what tell you to start it.",
            "The first letter is the wake.",
            "Enter takes `read`.",
        ):
            with self.subTest(line):
                self.assertIn(line, manual)
        self.assertNotIn("Tell them to decline", manual)
        self.assertNotIn("I am opening a terminal that will ask you to paste the bot token", manual)
        self.assertNotIn("Setup can read your chat id itself", manual)

    def test_the_readme_jumps_to_install_before_that_section(self):
        readme = self.pages()["readme"]
        self.assertIn("[Quick install](#install)", readme)
        self.assertLess(
            readme.index("[Quick install](#install)"),
            readme.index("## Install"),
        )

    def test_the_install_page_does_not_ask_the_person_to_pick_a_mode(self):
        install = self.pages()["install"]
        self.assertNotIn("does the agent you are waking already receive mail", install)
        self.assertNotIn("that inbox's path, and the agent's name", install)
        readme = self.pages()["readme"]
        self.assertNotIn("whether your agent already has a mailbox", readme)


if __name__ == "__main__":
    unittest.main()
