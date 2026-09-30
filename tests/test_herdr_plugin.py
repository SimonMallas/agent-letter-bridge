"""Herdr status popup: shows `alb --status` for one configured root, read-only.

The popup runs unsandboxed as the user inside someone's terminal, so it must
never see a token and must not pass state-file text through unfiltered.
"""
import os
import pathlib
import stat
import subprocess
import tempfile
import tomllib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "herdr"


class StatusPopup(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.conf = self.tmp / "conf"
        self.conf.mkdir()
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.argv_log = self.tmp / "argv"
        self.alb = self.tmp / "alb"
        self.alb.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$@" > "{self.argv_log}"\n'
            'printf "ALB_TOKEN=%s\\n" "${ALB_TOKEN:-unset}"\n'
            "printf 'bridge : failing - heartbeat 900s old\\n'\n"
            "printf 'canary : \\033]0;owned\\007ok \\302\\233evil\\n'\n"
            "exit 1\n",
            encoding="utf-8",
        )
        self.alb.chmod(self.alb.stat().st_mode | stat.S_IXUSR)

    def configure(self, text):
        (self.conf / "alb-plugin.env").write_text(text, encoding="utf-8")

    def view(self, **env):
        base = {"PATH": "/usr/bin:/bin", "HOME": str(self.tmp),
                "HERDR_PLUGIN_CONFIG_DIR": str(self.conf)}
        base.update(env)
        return subprocess.run(["bash", str(PLUGIN / "view.sh")], env=base,
                              stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=20, cwd=PLUGIN).stdout

    def test_it_runs_status_on_exactly_the_configured_root(self):
        self.configure(f"ALB_ROOT={self.root}\nALB_BIN={self.alb}\n")
        self.view()
        self.assertEqual(self.argv_log.read_text().splitlines(),
                         ["--status", "--root", str(self.root)])

    def test_an_unhealthy_bridge_is_still_reported(self):
        """--status exits 1 exactly when there is something to see."""
        self.configure(f"ALB_ROOT={self.root}\nALB_BIN={self.alb}\n")
        out = self.view()
        self.assertIn(b"bridge : failing - heartbeat 900s old", out)
        self.assertIn(b"Press any key to close.", out)

    def test_the_config_is_parsed_not_sourced_so_no_token_leaks_in(self):
        """Pointing the plugin at the bridge's own env file must not load ALB_TOKEN."""
        marker = self.tmp / "sourced"
        self.configure(f"ALB_ROOT={self.root}\nALB_BIN={self.alb}\n"
                       f"ALB_TOKEN=123:secret\ntouch {marker}\n")
        out = self.view()
        self.assertIn(b"ALB_TOKEN=unset", out)
        self.assertNotIn(b"secret", out)
        self.assertFalse(marker.exists())

    def test_control_characters_are_stripped_c0_and_c1(self):
        self.configure(f"ALB_ROOT={self.root}\nALB_BIN={self.alb}\n")
        out = self.view()
        self.assertIn(b"canary : ]0;ownedok evil", out)
        self.assertNotIn(b"\x1b", out)
        self.assertNotIn(b"\x07", out)
        self.assertNotIn(b"\xc2\x9b", out)

    def test_without_a_root_it_says_how_to_configure_and_runs_nothing(self):
        self.configure(f"ALB_BIN={self.alb}\n")
        out = self.view()
        self.assertIn(b"No bridge root configured", out)
        self.assertFalse(self.argv_log.exists())

    def test_a_missing_root_directory_is_refused(self):
        self.configure(f"ALB_ROOT={self.tmp / 'nope'}\nALB_BIN={self.alb}\n")
        out = self.view()
        self.assertIn(b"is not a directory", out)
        self.assertFalse(self.argv_log.exists())

    def test_a_missing_alb_is_named(self):
        self.configure(f"ALB_ROOT={self.root}\n")
        out = self.view()
        self.assertIn(b"alb was not found", out)

    def test_tilde_in_the_root_means_home(self):
        self.configure(f"ALB_ROOT=~/root\nALB_BIN={self.alb}\n")
        self.view()
        self.assertEqual(self.argv_log.read_text().split("\n")[2], str(self.root))


class Manifest(unittest.TestCase):
    def test_every_command_names_a_script_in_the_plugin(self):
        manifest = tomllib.loads((PLUGIN / "herdr-plugin.toml").read_text(encoding="utf-8"))
        for entry in manifest["actions"] + manifest["panes"]:
            self.assertEqual(entry["command"][0], "bash")
            self.assertTrue((PLUGIN / entry["command"][1]).is_file(), entry["command"])

    def test_plugin_version_matches_the_package(self):
        manifest = tomllib.loads((PLUGIN / "herdr-plugin.toml").read_text(encoding="utf-8"))
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], project["project"]["version"])

    def test_the_sdist_ships_the_plugin(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("herdr", project["tool"]["hatch"]["build"]["targets"]["sdist"]["only-include"])


if __name__ == "__main__":
    unittest.main()
