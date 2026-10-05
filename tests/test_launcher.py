"""Launcher fixtures; optional native tests do not create a Flatpak sandbox."""

import configparser
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import tempfile
import unittest


WRAPPER = Path(__file__).resolve().parents[1] / "src" / "floorp.sh"
BINARY_PATH = "/app/lib/floorp/floorp"
NATIVE_BINARY = os.environ.get("FLOORP_TEST_BINARY")
INFORMATION_FLAGS = (
    "-v", "--v", "-version", "--version", "-full-version", "--full-version",
    "-h", "--h", "-help", "--help", "-?", "--?", "--VERSION", "--help=ignored",
)


class LauncherFixture(unittest.TestCase):
    def setUp(self):
        evidence = os.environ.get("FLOORP_TEST_WORK_DIR")
        if evidence:
            Path(evidence).mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(
            prefix=self._testMethodName + "-", dir=evidence
        )
        self.root = Path(self.directory.name)
        if evidence:
            # Keep the isolated fixtures and native logs for review.
            self.directory._finalizer.detach()
        else:
            self.addCleanup(self.directory.cleanup)
        self.home = self.root / "home with spaces"
        self.app = self.home / ".var" / "app" / "one.ablaze.floorp"
        self.legacy = self.app / ".floorp"
        self.config = self.app / "config"
        self.xdg = self.config / "floorp"
        self.record = self.config / "floorp-profile-root"
        self.home.mkdir()
        self.environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_CACHE_HOME": str(self.app / "cache"),
        }
        self.stub = self.root / "browser-stub.py"
        self.stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "keys = ('MOZ_APP_DATA', 'MOZ_LOCAL_APP_DATA', 'TMPDIR', "
            "'MOZ_LEGACY_PROFILES', 'MOZ_LEGACY_HOME')\n"
            "print(json.dumps({'env': {k: os.environ.get(k) for k in keys}, "
            "'args': sys.argv[1:]}))\n"
        )
        self.stub.chmod(0o755)
        self.wrapper = self.make_wrapper(self.stub)

    def make_wrapper(self, binary):
        wrapper = self.root / ("launcher-" + binary.name + ".sh")
        source = WRAPPER.read_text()
        self.assertEqual(source.count(BINARY_PATH), 1)
        wrapper.write_text(source.replace(BINARY_PATH, shlex.quote(str(binary))))
        return wrapper

    def database(self, root, name="daily"):
        root.mkdir(parents=True, exist_ok=True)
        profile = root / (name + ".default-default")
        profile.mkdir()
        (profile / "user.js").write_text('user_pref("fixture.identity", "' + name + '");\n')
        (root / "profiles.ini").write_text(
            "[General]\nStartWithLastProfile=1\nVersion=2\n\n"
            "[Profile0]\nName=" + name + "\nIsRelative=1\nPath="
            + profile.name + "\nDefault=1\n"
        )
        (root / "installs.ini").write_text("[FIXTURE]\nDefault=" + profile.name + "\n")

    def snapshot(self, root):
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()
        }

    def persist_visible(self):
        self.legacy.mkdir(parents=True, exist_ok=True)
        (self.home / ".floorp").symlink_to(self.legacy, target_is_directory=True)

    def launch(self, *arguments, overrides=None, succeeds=True):
        environment = self.environment | (overrides or {})
        result = subprocess.run(
            ["bash", str(self.wrapper), *arguments],
            env=environment, cwd=self.root, capture_output=True, text=True, timeout=10,
        )
        if not succeeds:
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            return result
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_root(self, output, root):
        self.assertEqual(output["env"]["MOZ_APP_DATA"], str(root))

    def absolute_database(self, root, profile):
        root.mkdir(parents=True, exist_ok=True)
        (root / "profiles.ini").write_text(
            "[General]\nStartWithLastProfile=1\nVersion=2\n\n"
            "[Profile0]\nName=absolute-daily\nIsRelative=0\nPath=" + str(profile) + "\n"
        )


class WrapperTests(LauncherFixture):
    def test_information_flags_do_not_read_or_record_profile_state(self):
        for state in ("fresh", "ambiguous", "invalid-record"):
            if state == "ambiguous":
                self.database(self.legacy, "legacy")
                self.database(self.xdg, "xdg")
                self.database(self.home / ".floorp", "host")
            elif state == "invalid-record":
                self.record.write_text("invalid-relative-path\n")
            before = self.snapshot(self.app)
            for flag in INFORMATION_FLAGS:
                with self.subTest(state=state, flag=flag):
                    result = self.launch(flag)
                    self.assertEqual(result["args"], ["--name", "one.ablaze.floorp", flag])
                    self.assertIsNone(result["env"]["MOZ_APP_DATA"])
                    self.assertEqual(self.snapshot(self.app), before)

    def test_fresh_and_empty_persist_marker_use_xdg(self):
        self.persist_visible()
        (self.legacy / "profiles.ini").touch()
        result = self.launch("https://example.invalid/path with spaces")
        self.assert_root(result, self.xdg)
        self.assertEqual(self.record.read_text(), str(self.xdg) + "\n")
        self.assertEqual(self.record.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result["env"]["MOZ_LOCAL_APP_DATA"], str(self.app / "cache" / "floorp"))
        self.assertEqual(result["env"]["TMPDIR"], str(self.app / "cache" / "tmp"))
        self.assertIsNone(result["env"]["MOZ_LEGACY_PROFILES"])
        self.assertEqual(result["args"], ["--name", "one.ablaze.floorp", "https://example.invalid/path with spaces"])
        self.assertEqual((self.legacy / "profiles.ini").read_bytes(), b"")

    def test_legacy_database_survives_home_permission_toggle(self):
        self.database(self.legacy, "legacy")
        before = self.snapshot(self.legacy)
        self.persist_visible()
        self.assert_root(self.launch(), self.legacy)
        (self.home / ".floorp").unlink()
        host = self.home / ".floorp"
        self.database(host, "host")
        host_before = self.snapshot(host)
        self.assert_root(self.launch(), self.legacy)
        self.assertEqual(self.snapshot(host), host_before)
        self.assertEqual(self.snapshot(self.legacy), before)

    def test_xdg_database_wins_over_empty_marker_and_host_profile(self):
        self.database(self.xdg, "xdg")
        self.database(self.home / ".floorp", "host")
        self.legacy.mkdir(parents=True)
        before = self.snapshot(self.xdg)
        host_before = self.snapshot(self.home / ".floorp")
        self.assert_root(self.launch(), self.xdg)
        self.assertEqual(self.snapshot(self.xdg), before)
        self.assertEqual(self.snapshot(self.home / ".floorp"), host_before)

    def test_host_only_profile_is_never_adopted(self):
        self.database(self.home / ".floorp", "host")
        before = self.snapshot(self.home / ".floorp")
        self.assert_root(self.launch(), self.xdg)
        self.assertEqual(self.snapshot(self.home / ".floorp"), before)

    def test_both_databases_preserve_native_persist_root(self):
        self.database(self.legacy, "legacy")
        self.database(self.xdg, "xdg")
        before = (self.snapshot(self.legacy), self.snapshot(self.xdg))
        self.persist_visible()
        self.assert_root(self.launch(), self.legacy)
        (self.home / ".floorp").unlink()
        self.assert_root(self.launch(), self.legacy)
        self.assertEqual((self.snapshot(self.legacy), self.snapshot(self.xdg)), before)

    def test_both_databases_preserve_native_xdg_root(self):
        self.database(self.legacy, "legacy")
        self.database(self.xdg, "xdg")
        self.assert_root(self.launch(), self.xdg)
        self.persist_visible()
        self.assert_root(self.launch(), self.xdg)

    def test_ambiguous_databases_do_not_guess_or_write(self):
        self.database(self.legacy, "legacy")
        self.database(self.xdg, "xdg")
        self.database(self.home / ".floorp", "host")
        before = self.snapshot(self.app)
        result = self.launch("-P", "daily", succeeds=False)
        self.assertIn("Both Flatpak profile locations", result.stderr)
        self.assertFalse(self.record.exists())
        self.assertEqual(self.snapshot(self.app), before)
        self.assert_root(self.launch(overrides={"MOZ_APP_DATA": str(self.xdg)}), self.xdg)
        self.assertFalse(self.record.exists())

    def test_explicit_profile_bypasses_ambiguity_without_recording_default(self):
        self.database(self.legacy, "legacy")
        self.database(self.xdg, "xdg")
        self.database(self.home / ".floorp", "host")
        selected = self.root / "explicit profile"
        result = self.launch("-profile", str(selected), "--no-remote")
        self.assertEqual(result["args"], ["--name", "one.ablaze.floorp", "-profile", str(selected), "--no-remote"])
        self.assertFalse(self.record.exists())
        self.launch(overrides={"XRE_PROFILE_PATH": str(selected)})
        self.assertFalse(self.record.exists())

    def test_named_profile_arguments_and_explicit_env_are_preserved(self):
        self.database(self.legacy)
        self.assert_root(self.launch("-P", "daily"), self.legacy)
        saved = self.record.read_bytes()
        explicit = self.root / "custom app data"
        cache = self.root / "custom local data"
        result = self.launch("-P", "other", overrides={
            "MOZ_APP_DATA": str(explicit), "MOZ_LOCAL_APP_DATA": str(cache),
            "MOZ_LEGACY_PROFILES": "1",
        })
        self.assert_root(result, explicit)
        self.assertEqual(result["env"]["MOZ_LOCAL_APP_DATA"], str(cache))
        self.assertEqual(result["env"]["MOZ_LEGACY_PROFILES"], "1")
        self.assertEqual(result["args"], ["--name", "one.ablaze.floorp", "-P", "other"])
        self.assertEqual(self.record.read_bytes(), saved)
        self.assert_root(self.launch(), self.legacy)

    def test_absolute_xdg_paths_are_respected_and_recorded(self):
        custom_config = self.root / "custom config"
        custom_cache = self.root / "custom cache"
        self.database(custom_config / "floorp")
        self.database(self.home / ".floorp", "host")
        result = self.launch(overrides={
            "XDG_CONFIG_HOME": str(custom_config), "XDG_CACHE_HOME": str(custom_cache),
        })
        self.assert_root(result, custom_config / "floorp")
        self.assertEqual(result["env"]["MOZ_LOCAL_APP_DATA"], str(custom_cache / "floorp"))
        self.assertTrue(self.record.exists())
        self.assert_root(self.launch(), custom_config / "floorp")

    def test_relative_or_missing_xdg_do_not_adopt_host_config(self):
        self.database(self.home / ".config" / "floorp", "host")
        before = self.snapshot(self.home / ".config")
        result = self.launch(overrides={
            "XDG_CONFIG_HOME": "relative-config", "XDG_CACHE_HOME": "relative-cache",
            "MOZ_APP_DATA": "relative-data", "MOZ_LOCAL_APP_DATA": "relative-local",
        })
        self.assert_root(result, self.xdg)
        self.assertEqual(result["env"]["MOZ_LOCAL_APP_DATA"], str(self.app / "cache" / "floorp"))
        self.assertEqual(self.snapshot(self.home / ".config"), before)
        self.assertFalse((self.root / "relative-config").exists())
        self.environment.pop("XDG_CONFIG_HOME")
        self.environment.pop("XDG_CACHE_HOME")
        self.assert_root(self.launch(), self.xdg)

    def test_forced_legacy_layout_for_fresh_install_is_private(self):
        self.database(self.home / ".floorp", "host")
        self.assert_root(self.launch(overrides={"MOZ_LEGACY_HOME": "1"}), self.legacy)
        self.assert_root(self.launch(), self.legacy)

    def test_install_backup_alone_is_preserved(self):
        self.legacy.mkdir(parents=True)
        (self.legacy / "installs.ini").write_text("[EXISTING]\nDefault=daily\n")
        self.assert_root(self.launch(), self.legacy)

    def test_invalid_saved_record_fails_without_changing_databases(self):
        self.database(self.xdg)
        self.record.write_text("relative-path\n")
        before = self.snapshot(self.app)
        result = self.launch(succeeds=False)
        self.assertIn("Invalid saved profile location", result.stderr)
        self.assertEqual(self.snapshot(self.app), before)
        self.assert_root(self.launch(overrides={"MOZ_APP_DATA": str(self.xdg)}), self.xdg)
        self.assertEqual(self.snapshot(self.app), before)

    def test_explicit_profile_bypasses_invalid_or_directory_record(self):
        self.config.mkdir(parents=True)
        selected = self.root / "explicit profile"
        self.record.write_text("invalid-relative-path\n")
        self.launch("--profile", str(selected))
        self.assertEqual(self.record.read_text(), "invalid-relative-path\n")
        self.record.unlink()
        self.record.mkdir()
        self.launch("-profile", str(selected))
        self.launch(overrides={"XRE_PROFILE_PATH": str(selected)})
        self.assertEqual(list(self.record.iterdir()), [])
        self.launch(succeeds=False)
        self.assertEqual(list(self.record.iterdir()), [])

    def test_concurrent_first_launch_publishes_one_complete_record(self):
        processes = [subprocess.Popen(
            ["bash", str(self.wrapper)], env=self.environment, cwd=self.root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) for _ in range(8)]
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            self.assert_root(json.loads(stdout), self.xdg)
        self.assertEqual(self.record.read_text(), str(self.xdg) + "\n")
        self.assertEqual(list(self.config.glob("floorp-profile-root.*")), [])

    def test_absolute_alias_is_checked_before_recording_and_after_home_toggle(self):
        alias = self.home / ".floorp"
        self.absolute_database(self.legacy, alias / "absolute-daily")
        alias.mkdir()
        before = self.snapshot(self.app)
        error = self.launch("-P", "absolute-daily", succeeds=False)
        self.assertIn("Stored absolute profile path", error.stderr)
        self.assertIn(str(self.legacy / "absolute-daily"), error.stderr)
        self.assertIn("MOZ_APP_DATA alone", error.stderr)
        self.assertEqual(self.snapshot(self.app), before)
        self.assertFalse(self.record.exists())

        alias.rmdir()
        self.persist_visible()
        self.assert_root(self.launch(), self.legacy)
        saved = self.record.read_bytes()
        alias.unlink()
        alias.mkdir()
        for arguments in ((), ("-P", "absolute-daily")):
            self.launch(*arguments, succeeds=False)
        self.launch(overrides={"MOZ_APP_DATA": str(self.legacy)}, succeeds=False)
        self.assertEqual(self.record.read_bytes(), saved)
        alias.rmdir()
        self.persist_visible()
        self.assert_root(self.launch("-P", "absolute-daily"), self.legacy)

    def test_alias_fields_accept_order_whitespace_comments_and_unterminated_lines(self):
        self.legacy.mkdir(parents=True)
        alias_path = str(self.home / ".floorp" / "daily=value")
        cases = (
            "[Profile0]\nPath=" + alias_path + "\nName=daily\nIsRelative=0",
            "  [Profile0]  \r\n\tPath = " + alias_path + "\t\r\n; comment\r\nIsRelative = 0 \r\n",
            "[Profile0]\nIsRelative=0\n# comment\nPath=" + alias_path + "\n[Profile1]\nIsRelative=1\nPath=other\n",
        )
        for metadata in cases:
            with self.subTest(metadata=metadata):
                (self.legacy / "profiles.ini").write_text(metadata)
                before = self.snapshot(self.legacy)
                self.launch(succeeds=False)
                self.assertEqual(self.snapshot(self.legacy), before)
                self.assertFalse(self.record.exists())

    def test_install_defaults_and_backup_alias_references_are_checked(self):
        self.legacy.mkdir(parents=True)
        alias_path = str(self.home / ".floorp" / "daily")
        cases = (
            ("profiles.ini", "[Profile0]\nIsRelative=1\nPath=daily\n[InstallCURRENT]\nDefault=" + alias_path + "\n"),
            ("profiles.ini", " [InstallCURRENT] \r\nLocked=1\r\n Default = " + alias_path + " \r\n"),
            ("installs.ini", "[OLDINSTALL]\nLocked=1\nDefault=" + alias_path),
        )
        for filename, metadata in cases:
            with self.subTest(filename=filename, metadata=metadata):
                for existing in self.legacy.glob("*.ini"):
                    existing.unlink()
                (self.legacy / filename).write_text(metadata)
                before = self.snapshot(self.legacy)
                self.launch(succeeds=False)
                self.assertEqual(self.snapshot(self.legacy), before)
                self.assertFalse(self.record.exists())

    def test_stable_absolute_paths_and_alias_prefix_boundary_remain_supported(self):
        for profile in (self.root / "stable-profile", self.home / ".floorp-other" / "daily"):
            with self.subTest(profile=profile):
                self.absolute_database(self.legacy, profile)
                (self.legacy / "installs.ini").write_text("[EXISTING]\nDefault=" + str(profile) + "\n")
                before = self.snapshot(self.legacy)
                self.assert_root(self.launch(), self.legacy)
                self.assertEqual(self.snapshot(self.legacy), before)

    def test_database_override_is_guarded_but_direct_profile_is_authoritative(self):
        custom = self.app / "custom-database"
        self.absolute_database(custom, self.home / ".floorp" / "daily")
        before = self.snapshot(custom)
        overrides = {"MOZ_APP_DATA": str(custom)}
        self.launch("-P", "absolute-daily", overrides=overrides, succeeds=False)
        self.assertFalse(self.record.exists())
        selected = self.legacy / "daily"
        for arguments in (("-profile", str(selected)), ("--profile=" + str(selected),)):
            result = self.launch(*arguments, overrides=overrides)
            self.assert_root(result, custom)
            self.assertEqual(result["args"], ["--name", "one.ablaze.floorp", *arguments])
        self.launch(overrides=overrides | {"XRE_PROFILE_PATH": str(selected)})
        self.assertEqual(self.snapshot(custom), before)

    def test_unrelated_defaults_and_relative_profiles_do_not_trigger_alias_guard(self):
        self.legacy.mkdir(parents=True)
        (self.legacy / "profiles.ini").write_text(
            "[General]\nDefault=" + str(self.home / ".floorp" / "unused") + "\n"
            "[Profile0]\nIsRelative=1\nPath=daily\nDefault=1\n"
        )
        self.assert_root(self.launch(), self.legacy)


@unittest.skipUnless(NATIVE_BINARY, "set FLOORP_TEST_BINARY to test a native Floorp build")
class NativeTests(LauncherFixture):
    def setUp(self):
        super().setUp()
        self.binary = Path(NATIVE_BINARY).resolve(strict=True)
        self.wrapper = self.make_wrapper(self.binary)
        runtime = self.root / "runtime"
        runtime.mkdir(mode=0o700)
        self.environment.update({
            "XDG_RUNTIME_DIR": str(runtime), "MOZ_HEADLESS": "1", "NO_AT_BRIDGE": "1",
            "MOZ_CRASHREPORTER_DISABLE": "1", "MOZ_CRASHREPORTER_NO_REPORT": "1",
        })
        (self.app / "cache" / "tmp").mkdir(parents=True)
        self.launch_number = 0

    def native(self, *arguments, seed_root=None, unwrapped=False, overrides=None,
               expected_returncode=0):
        environment = self.environment | (overrides or {})
        if seed_root is not None:
            environment["MOZ_APP_DATA"] = str(seed_root)
            environment["MOZ_LOCAL_APP_DATA"] = str(self.app / "cache" / "floorp")
        launcher = [str(self.binary)] if unwrapped else ["bash", str(self.wrapper)]
        command = [*launcher, "--headless", "--no-remote", *arguments]
        self.launch_number += 1
        with (self.root / f"launch-{self.launch_number}.stdout").open("wb") as stdout, \
                (self.root / f"launch-{self.launch_number}.stderr").open("wb") as stderr:
            process = subprocess.Popen(
                command, env=environment, cwd=self.root, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, start_new_session=True,
            )
            try:
                returncode = process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
                self.fail("Native fixture timed out; only its own process group was terminated.")
        self.assertEqual(returncode, expected_returncode, f"See native logs in {self.root}")
        return self.root / f"launch-{self.launch_number}.stderr"

    def names(self, root):
        database = configparser.ConfigParser()
        database.read(root / "profiles.ini")
        return {database[section]["Name"] for section in database.sections()
                if section.startswith("Profile")}

    def test_native_legacy_root_is_stable_across_namespace_changes(self):
        # Start as the old launcher did, through HOME/.floorp, then switch to
        # the stable private path without changing the existing database.
        self.persist_visible()
        self.native("--screenshot", str(self.root / "initial.png"), "about:blank",
                    unwrapped=True)
        database = configparser.ConfigParser()
        database.read(self.legacy / "profiles.ini")
        defaults = {database[section]["Default"] for section in database.sections()
                    if section.startswith("Install")}
        self.assertEqual(len(defaults), 1)
        active = self.legacy / defaults.pop()
        (active / "user.js").write_text('user_pref("fixture.profile_identity", "legacy-daily");\n')
        metadata = {name: (self.legacy / name).read_bytes()
                    for name in ("profiles.ini", "installs.ini")}

        def ordinary_start(phase):
            screenshot = self.root / (phase + ".png")
            self.native("--screenshot", str(screenshot), "about:blank")
            self.assertTrue(screenshot.exists())
            self.assertIn('user_pref("fixture.profile_identity", "legacy-daily");',
                          (active / "prefs.js").read_text())
            self.assertEqual({name: (self.legacy / name).read_bytes() for name in metadata},
                             metadata)

        ordinary_start("persist-visible")
        (self.home / ".floorp").unlink()
        self.database(self.home / ".floorp", "host")
        host_before = self.snapshot(self.home / ".floorp")
        ordinary_start("home-granted")
        (self.home / ".floorp").rename(self.root / "host-saved")
        self.persist_visible()
        ordinary_start("persist-visible-again")
        self.assertEqual(self.snapshot(self.root / "host-saved"), host_before)
        self.assertFalse((self.xdg / "profiles.ini").exists())
        self.assertEqual(self.record.read_text(), str(self.legacy) + "\n")

    def test_native_xdg_root_and_named_profile_survive_persist_visibility(self):
        self.native("--createprofile", "legacy-unused", seed_root=self.legacy)
        self.native("--createprofile", "xdg-daily", seed_root=self.xdg)
        legacy_before = self.snapshot(self.legacy)
        self.native("--createprofile", "home-visible")
        self.persist_visible()
        self.native("--createprofile", "persist-visible")
        screenshot = self.root / "named-profile.png"
        self.native("-P", "xdg-daily", "--screenshot", str(screenshot), "about:blank")
        self.assertTrue(screenshot.exists())
        self.assertEqual(self.names(self.xdg), {"xdg-daily", "home-visible", "persist-visible"})
        self.assertEqual(self.snapshot(self.legacy), legacy_before)
        self.assertEqual(self.record.read_text(), str(self.xdg) + "\n")

    def test_native_explicit_profile_does_not_rewrite_ambiguous_databases(self):
        self.native("--createprofile", "legacy", seed_root=self.legacy)
        self.native("--createprofile", "xdg", seed_root=self.xdg)
        self.database(self.home / ".floorp", "host")
        before = (self.snapshot(self.legacy), self.snapshot(self.xdg),
                  self.snapshot(self.home / ".floorp"))
        selected = self.root / "explicit profile"
        selected.mkdir()
        screenshot = self.root / "explicit-profile.png"
        self.native("-profile", str(selected), "--screenshot", str(screenshot), "about:blank")
        self.assertTrue(screenshot.exists())
        self.assertTrue((selected / "prefs.js").exists())
        self.assertFalse(self.record.exists())
        self.assertEqual((self.snapshot(self.legacy), self.snapshot(self.xdg),
                          self.snapshot(self.home / ".floorp")), before)

    def test_native_information_flags_leave_database_state_unchanged(self):
        for state in ("fresh", "ambiguous", "invalid-record"):
            if state == "ambiguous":
                self.database(self.legacy, "legacy")
                self.database(self.xdg, "xdg")
                self.database(self.home / ".floorp", "host")
            elif state == "invalid-record":
                self.record.write_text("invalid-relative-path\n")
            metadata = {str(path): path.read_bytes() for path in self.app.rglob("*.ini")}
            saved = self.record.read_bytes() if self.record.exists() else None
            for flag in ("-v", "--version", "--full-version", "-h", "--help", "--help=ignored"):
                with self.subTest(state=state, flag=flag):
                    self.native(flag)
                    stdout = self.root / f"launch-{self.launch_number}.stdout"
                    self.assertTrue(stdout.read_text().strip())
                    self.assertEqual({str(path): path.read_bytes() for path in self.app.rglob("*.ini")}, metadata)
                    self.assertEqual(self.record.read_bytes() if self.record.exists() else None, saved)

    def test_native_absolute_alias_is_blocked_before_host_profile_is_used(self):
        self.persist_visible()
        self.native("--screenshot", str(self.root / "initial.png"), "about:blank", unwrapped=True)
        database = configparser.ConfigParser()
        database.optionxform = str
        database.read(self.legacy / "profiles.ini")
        install = next(section for section in database.sections() if section.startswith("Install"))
        leaf = database[install]["Default"]
        profile_section = next(section for section in database.sections()
                               if section.startswith("Profile") and database[section]["Path"] == leaf)
        alias_profile = self.home / ".floorp" / leaf
        private_profile = self.legacy / leaf
        database[profile_section]["Name"] = "absolute-daily"
        database[profile_section]["IsRelative"] = "0"
        database[profile_section]["Path"] = str(alias_profile)
        database[install]["Default"] = str(alias_profile)
        with (self.legacy / "profiles.ini").open("w") as stream:
            database.write(stream, space_around_delimiters=False)
        (self.legacy / "installs.ini").write_text(
            "[" + install.removeprefix("Install") + "]\nDefault=" + str(alias_profile) + "\nLocked=1\n"
        )
        (private_profile / "user.js").write_text('user_pref("fixture.profile_identity", "private-absolute");\n')
        self.native("-P", "absolute-daily", "--screenshot", str(self.root / "private.png"), "about:blank")
        self.assertIn('"private-absolute"', (private_profile / "prefs.js").read_text())
        metadata = {name: (self.legacy / name).read_bytes() for name in ("profiles.ini", "installs.ini")}
        saved = self.record.read_bytes()

        (self.home / ".floorp").unlink()
        alias_profile.mkdir(parents=True)
        (alias_profile / "user.js").write_text('user_pref("fixture.profile_identity", "host-absolute");\n')
        host_before = self.snapshot(self.home / ".floorp")
        for arguments, overrides in (
            ((), None), (("-P", "absolute-daily"), None),
            (("-P", "absolute-daily"), {"MOZ_APP_DATA": str(self.legacy)}),
        ):
            error = self.native(*arguments, overrides=overrides, expected_returncode=1)
            self.assertIn("Stored absolute profile path", error.read_text())
            self.assertIn(str(private_profile), error.read_text())
            self.assertEqual(self.snapshot(self.home / ".floorp"), host_before)
            self.assertEqual({name: (self.legacy / name).read_bytes() for name in metadata}, metadata)
            self.assertEqual(self.record.read_bytes(), saved)

        # Negative control: the same native binary without the guard selects
        # the host backing from the absolute Path despite fixed MOZ_APP_DATA.
        self.native("-P", "absolute-daily", "--screenshot", str(self.root / "unguarded-host.png"),
                    "about:blank", seed_root=self.legacy, unwrapped=True)
        self.assertIn('"host-absolute"', (alias_profile / "prefs.js").read_text())
        host_after_control = self.snapshot(self.home / ".floorp")
        self.native("--profile=" + str(private_profile), "--screenshot",
                    str(self.root / "explicit-private.png"), "about:blank")
        self.assertIn('"private-absolute"', (private_profile / "prefs.js").read_text())
        self.assertEqual(self.snapshot(self.home / ".floorp"), host_after_control)
        (self.home / ".floorp").rename(self.root / "host-saved")
        self.persist_visible()
        self.native("-P", "absolute-daily", "--screenshot", str(self.root / "restored-private.png"), "about:blank")
        self.assertIn('"private-absolute"', (private_profile / "prefs.js").read_text())

    def test_native_stable_absolute_custom_profile_remains_supported(self):
        custom_profile = self.app / "custom-profiles" / "daily"
        self.native("--createprofile", "stable-daily " + str(custom_profile), seed_root=self.legacy)
        (custom_profile / "user.js").write_text('user_pref("fixture.profile_identity", "stable-custom");\n')
        self.persist_visible()
        self.native("-P", "stable-daily", "--screenshot", str(self.root / "persist-stable.png"), "about:blank")
        (self.home / ".floorp").unlink()
        self.database(self.home / ".floorp", "host")
        host_before = self.snapshot(self.home / ".floorp")
        self.native("-P", "stable-daily", "--screenshot", str(self.root / "home-stable.png"), "about:blank")
        self.assertIn('"stable-custom"', (custom_profile / "prefs.js").read_text())
        self.assertEqual(self.snapshot(self.home / ".floorp"), host_before)


if __name__ == "__main__":
    unittest.main()
