"""Tests for scripts/package-linux.sh.

Run with: cd Linux/tests && python3 -m unittest discover
"""

import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "package-linux.sh")
DESKTOP = os.path.join(REPO_ROOT, "Linux", "packaging", "openpets.desktop")

DEPENDS_REQUIRED = [
    "python3 (>= 3.10)",
    "python3-gi",
    "python3-gi-cairo",
    "gir1.2-gtk-3.0",
    "gir1.2-ayatanaappindicator3-0.1",
    "webp-pixbuf-loader",
]


def run_script(version, output_dir, sources_dir=None):
    env = os.environ.copy()
    if sources_dir is not None:
        env["OPENPETS_LINUX_SOURCES"] = sources_dir
    return subprocess.run(
        ["bash", SCRIPT, version, output_dir],
        env=env,
        capture_output=True,
        text=True,
    )


class PackageLinuxScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="openpets-packaging-test-")
        self.out = os.path.join(self.tmp, "out")
        self.sources = os.path.join(self.tmp, "sources", "openpets_linux")
        os.makedirs(self.sources)
        with open(os.path.join(self.sources, "__init__.py"), "w") as fh:
            fh.write('# OpenPets Linux package stub\n')
        with open(os.path.join(self.sources, "__main__.py"), "w") as fh:
            fh.write("def main():\n    raise SystemExit(0)\n\nif __name__ == \"__main__\":\n    main()\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def deb_path(self, version):
        return os.path.join(self.out, f"openpets_{version}_all.deb")

    def test_rejects_invalid_versions(self):
        for bad in ["", "v", "1.2", "v1.2.3-", "1.2.3.4.5", "1.2.3 ", " 1.2.3", "abc", "1.2.3.4"]:
            result = run_script(bad, self.out, sources_dir=self.sources)
            self.assertNotEqual(result.returncode, 0, msg=f"version {bad!r} should be rejected: {result.stderr}")

    def test_accepts_optional_leading_v(self):
        result = run_script("v1.0.0", self.out, sources_dir=self.sources)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(os.path.exists(self.deb_path("1.0.0")))

    def test_creates_named_deb(self):
        result = run_script("1.2.3", self.out, sources_dir=self.sources)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(os.path.exists(self.deb_path("1.2.3")), msg=result.stderr)

    def test_creates_missing_output_directory(self):
        missing = os.path.join(self.tmp, "nested", "new-dir")
        result = run_script("2.0.0", missing, sources_dir=self.sources)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(os.path.isdir(missing))
        self.assertTrue(os.path.exists(os.path.join(missing, "openpets_2.0.0_all.deb")))

    def test_rejects_output_path_that_is_a_file(self):
        existing_file = os.path.join(self.tmp, "afile")
        with open(existing_file, "w") as fh:
            fh.write("not a directory")
        result = run_script("3.0.0", existing_file, sources_dir=self.sources)
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_missing_sources(self):
        # Point OPENPETS_LINUX_SOURCES at a path that cannot exist so the
        # default $ROOT/Linux/openpets_linux is never consulted.
        env_missing = os.path.join(self.tmp, "does-not-exist")
        result = run_script("1.2.3", self.out, sources_dir=env_missing)
        self.assertNotEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("sources not found", result.stderr.lower())

    @unittest.skipIf(shutil.which("dpkg-deb") is None, "dpkg-deb not installed")
    def test_deb_control_and_contents(self):
        result = run_script("1.2.3", self.out, sources_dir=self.sources)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        deb = self.deb_path("1.2.3")

        info = subprocess.check_output(["dpkg-deb", "-I", deb], text=True)
        self.assertIn("Package: openpets", info)
        self.assertIn("Version: 1.2.3", info)
        self.assertIn("Architecture: all", info)
        for dep in DEPENDS_REQUIRED:
            self.assertIn(dep, info, msg=f"missing dependency {dep!r}")

        contents = subprocess.check_output(["dpkg-deb", "--contents", deb], text=True)
        for path in [
            "./usr/bin/openpets",
            "./usr/lib/openpets/openpets_linux/__init__.py",
            "./usr/lib/openpets/openpets_linux/__main__.py",
            "./usr/share/applications/openpets.desktop",
            "./usr/share/doc/openpets/copyright",
        ]:
            self.assertIn(path, contents, msg=f"missing {path!r} in deb contents")

    @unittest.skipIf(shutil.which("dpkg-deb") is None, "dpkg-deb not installed")
    def test_wrapper_uses_isolated_system_python(self):
        run_script("1.2.3", self.out, sources_dir=self.sources)
        deb = self.deb_path("1.2.3")
        with tempfile.TemporaryDirectory() as extract_dir:
            subprocess.run(
                ["dpkg-deb", "-x", deb, extract_dir],
                check=True,
                capture_output=True,
            )
            wrapper = os.path.join(extract_dir, "usr", "bin", "openpets")
            with open(wrapper) as fh:
                text = fh.read()
            self.assertIn("/usr/bin/python3", text)
            self.assertIn("/usr/lib/openpets", text)
            self.assertIn("python3 -I", text)
            self.assertIn('run_module("openpets_linux"', text)
            self.assertTrue(os.access(wrapper, os.X_OK))

    def test_rejects_source_symlinks(self):
        os.symlink("/etc/passwd", os.path.join(self.sources, "unexpected.py"))
        result = run_script("1.2.3", self.out, sources_dir=self.sources)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no symlinks", result.stderr)

    def test_rejects_existing_artifact(self):
        self.assertEqual(run_script("1.2.3", self.out, sources_dir=self.sources).returncode, 0)
        with open(self.deb_path("1.2.3"), "rb") as package:
            before = package.read()
        result = run_script("1.2.3", self.out, sources_dir=self.sources)
        self.assertNotEqual(result.returncode, 0)
        with open(self.deb_path("1.2.3"), "rb") as package:
            self.assertEqual(package.read(), before)

    def test_desktop_entry(self):
        self.assertTrue(os.path.exists(DESKTOP))
        with open(DESKTOP) as fh:
            text = fh.read()
        self.assertIn("Type=Application", text)
        self.assertIn("Name=OpenPets", text)
        self.assertIn("Exec=openpets", text)
        self.assertIn("Icon=face-smile", text)
        self.assertNotIn(".webp", text)
        self.assertNotIn(".png", text)


if __name__ == "__main__":
    unittest.main()
