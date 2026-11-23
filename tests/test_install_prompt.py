"""The install pages carry one prompt a person can copy to an agent."""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

PROMPT = """\
Install Agent Letter Bridge from the checkout this prompt came from. If you do not have that checkout, stop. Do not install a published package in its place.

Read docs/agent-install.md. If a line below disagrees with that page, follow the line below.

Do not ask me for a folder, a path, a bot, or an allowlist. Do not invent a seat. There is no seat flag. Do not search this machine for a mailbox. Do not pass a name. The setup terminal may ask the name of the agent. Leave that question there.

If LETTERBOX_DIR and LETTERBOX_AGENT are both already set and non-empty in this terminal, pass those references into the setup terminal. If either is empty, leave both off the command.

Open a terminal and leave it open. If no bridge is using ~/.alb, run alb --init --root ~/.alb there. If a bridge is already using it, pick a new root, tell me the path, and do not ask me to choose it. Do not read that terminal. I will paste the key and press Enter.

The setup terminal may ask which chat is mine. Leave that question there. Do not ask it yourself.

If that terminal asks for a folder, a path, or a helper path, stop. Do not answer. Do not answer the name for me.

Leave any bridge that is already running untouched.

On Slack or Discord, follow that platform's page in this checkout. The same rules apply.

When my first message arrives as a letter, answer it with alb --reply-to on this bridge. The installer does not send that answer.
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

    def test_the_prompt_forbids_the_questions_a_plain_sentence_produced(self):
        for line in (
            "Do not ask me for a folder, a path, a bot, or an allowlist.",
            "Do not pass a name.",
            "The setup terminal may ask the name of the agent.",
            "If either is empty, leave both off the command.",
            "Do not read that terminal.",
            "If that terminal asks for a folder, a path, or a helper path, stop.",
            "Do not answer the name for me.",
            "Leave any bridge that is already running untouched.",
            "answer it with alb --reply-to on this bridge.",
            "The installer does not send that answer.",
            "If you do not have that checkout, stop.",
        ):
            with self.subTest(line):
                self.assertIn(line, PROMPT)

    def test_the_install_page_does_not_ask_the_person_to_pick_a_mode(self):
        install = self.pages()["install"]
        self.assertNotIn("does the agent you are waking already receive mail", install)
        self.assertNotIn("that inbox's path, and the agent's name", install)
        readme = self.pages()["readme"]
        self.assertNotIn("whether your agent already has a mailbox", readme)


if __name__ == "__main__":
    unittest.main()
