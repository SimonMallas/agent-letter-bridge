"""Source metadata gates; built wheel/sdist metadata is checked at release."""
import pathlib
import tomllib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PackageMetadata(unittest.TestCase):
    def setUp(self):
        self.data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_backend_is_exactly_pinned_and_runtime_dependencies_stay_empty(self):
        self.assertEqual(self.data["build-system"]["build-backend"], "hatchling.build")
        requires = self.data["build-system"]["requires"]
        self.assertEqual(len(requires), 1)
        self.assertRegex(requires[0], r"\Ahatchling==[0-9]+\.[0-9]+\.[0-9]+\Z")
        self.assertEqual(self.data["project"]["dependencies"], [])

    def test_public_listing_has_links_and_classifiers(self):
        project = self.data["project"]
        for key in ("Homepage", "Repository", "Issues", "Changelog"):
            self.assertTrue(project["urls"][key].startswith("https://"), key)
        self.assertIn("Environment :: Console", project["classifiers"])
        self.assertIn("Programming Language :: Python :: 3 :: Only", project["classifiers"])

    def test_readme_has_pypi_badge_and_install_paths(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("https://img.shields.io/pypi/v/agent-letter-bridge", readme)
        self.assertIn("https://pypi.org/project/agent-letter-bridge/", readme)
        self.assertIn("pipx install agent-letter-bridge", readme)
        self.assertIn("python -m pip install agent-letter-bridge", readme)
        self.assertNotIn("Not on a package index", readme)


if __name__ == "__main__":
    unittest.main()
