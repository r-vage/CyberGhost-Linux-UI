import unittest
from unittest.mock import patch

from cyberghost_desktop import linux_support as linux


class LinuxSupportTests(unittest.TestCase):
    def test_distribution_and_derivatives_choose_their_own_package_manager(self):
        cases = (
            ({"ID": "linuxmint", "ID_LIKE": "ubuntu debian"}, "apt-get"),
            ({"ID": "pop", "ID_LIKE": "ubuntu debian"}, "apt-get"),
            ({"ID": "fedora"}, "dnf"),
            ({"ID": "rocky", "ID_LIKE": "rhel centos fedora"}, "dnf"),
            ({"ID": "endeavouros", "ID_LIKE": "arch"}, "pacman"),
            ({"ID": "opensuse-tumbleweed", "ID_LIKE": "opensuse suse"}, "zypper"),
        )
        for release, expected in cases:
            with self.subTest(release=release), patch.object(linux, "os_release", return_value=release), \
                    patch.object(linux.shutil, "which", side_effect=lambda name: "/usr/bin/" + name):
                self.assertEqual(linux.package_manager(), expected)
                self.assertIn(expected, linux.dependency_help())
                self.assertNotIn("gtk4", linux.dependency_help())

    def test_older_rhel_can_use_yum(self):
        with patch.object(linux, "os_release", return_value={"ID": "rhel"}), \
                patch.object(linux.shutil, "which", side_effect=lambda name: "/usr/bin/yum" if name == "yum" else None):
            self.assertEqual(linux.package_manager(), "yum")

    def test_missing_os_release_uses_available_manager_without_execution(self):
        with patch.object(linux.platform, "freedesktop_os_release", side_effect=FileNotFoundError), \
                patch.object(linux.shutil, "which", side_effect=lambda name: "/usr/bin/dnf" if name == "dnf" else None):
            self.assertEqual(linux.package_manager(), "dnf")
            self.assertIn("unknown", linux.distribution_name())

    def test_unknown_system_gets_generic_help(self):
        with patch.object(linux, "os_release", return_value={"ID": "other", "PRETTY_NAME": "Test Linux"}), \
                patch.object(linux.shutil, "which", return_value=None):
            self.assertIsNone(linux.package_manager())
            self.assertEqual(linux.distribution_name(), "Test Linux")
            self.assertIn("distribution's package manager", linux.dependency_help())
            self.assertNotIn("apt", linux.dependency_help())

    def test_systemctl_without_running_systemd_is_not_supported(self):
        for directory, executable, expected in ((False, "/usr/bin/systemctl", False), (True, None, False),
                                                 (True, "/usr/bin/systemctl", True)):
            with self.subTest(directory=directory, executable=executable), \
                    patch.object(linux.Path, "is_dir", return_value=directory), \
                    patch.object(linux.shutil, "which", return_value=executable):
                self.assertEqual(linux.systemd_available(), expected)


if __name__ == "__main__":
    unittest.main()
