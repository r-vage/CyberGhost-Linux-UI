# Update a clean checkout in place, without touching the VPN or privileged helpers.

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess

from . import __version__
from .i18n import LANGUAGE_CODE, catalog_data
from .locale_store import LocaleStore
from .updates import MAX_VERSION_BYTES, REPOSITORY_URL, UpdateError, published_version, version_numbers

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
GIT_URL = REPOSITORY_URL + ".git"
GIT_REQUIRED = "In-app installation requires Git and a Git checkout. Use Update instructions for ZIP installations."
SOURCE_ERROR = "Only the official repository’s main branch can be updated in the app."
LOCAL_CHANGES = "This checkout has local changes. Save your work and update it manually."
GIT_BUSY = "Another Git operation is in progress. Finish it before updating."
GIT_ERROR = "Git could not complete the update. See Activity for details."
DOWNLOAD_ERROR = "Could not download the update. Check your connection and try again."
VERSION_CHANGED = "The published version changed. Check for updates again."
LOCAL_COMMITS = "This branch has local commits. Update it manually to preserve your work."
LANGUAGE_BACKUP_ERROR = "Language edits could not be backed up. No update was installed."


class InstallError(UpdateError):
    def __init__(self, text, details="", *, restart_required=False):
        super().__init__(text)
        self.details = details
        self.restart_required = restart_required


@dataclass(frozen=True)
class InstallResult:
    version: str


def checkout_available(project=PROJECT_DIRECTORY):
    return (Path(project) / ".git").exists() and shutil.which("git") is not None


class CheckoutUpdater:
    def __init__(self, project=PROJECT_DIRECTORY, *, locale_directory=None):
        self.project = Path(project).resolve()
        self.git = shutil.which("git")
        self.locale_directory = Path(locale_directory) if locale_directory is not None else (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cyberghost-desktop/locales")
        if not self.git or not (self.project / ".git").exists():
            raise InstallError(GIT_REQUIRED)
        self.environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.environment.update({"LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false",
                                 "GIT_SSH_COMMAND": "ssh -oBatchMode=yes", "GIT_OPTIONAL_LOCKS": "0"})

    def _git(self, *arguments, timeout=15, error=GIT_ERROR, allowed=(0,)):
        command = [self.git, "-c", "core.hooksPath=/dev/null", "-c", "merge.autoStash=false",
                   "-c", "http.sslVerify=true", "-c", "http.followRedirects=false", "-c", "http.proxy=",
                   "-c", "credential.helper=", "-c", "gc.auto=0", *arguments]
        try:
            result = subprocess.run(command, cwd=self.project, env=self.environment, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as failure:
            raise InstallError(error, str(failure)) from None
        if result.returncode not in allowed:
            raise InstallError(error, (result.stderr or result.stdout).strip()[-2000:])
        return result

    def _inspect(self):
        root = self._git("rev-parse", "--show-toplevel", error=GIT_REQUIRED).stdout.strip()
        branch = self._git("symbolic-ref", "--quiet", "--short", "HEAD", error=SOURCE_ERROR).stdout.strip()
        remote = self._git("remote", "get-url", "origin", error=SOURCE_ERROR).stdout.strip()
        official = {GIT_URL, GIT_URL.removesuffix(".git"), "git@github.com:r-vage/CyberGhost-Linux-UI.git",
                    "ssh://git@github.com/r-vage/CyberGhost-Linux-UI.git"}
        if Path(root).resolve() != self.project or branch != "main" or remote not in official:
            raise InstallError(SOURCE_ERROR)
        git_dir = Path(self._git("rev-parse", "--absolute-git-dir").stdout.strip())
        if any((git_dir / name).exists() for name in
               ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "BISECT_LOG", "index.lock")):
            raise InstallError(GIT_BUSY)
        edits = {}
        changes = self._git("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
        for entry in changes.split("\0"):
            if not entry:
                continue
            state, name = entry[:2], entry[3:]
            path = Path(name)
            if (state not in {" M", "??"} or path.parent.as_posix() != "cyberghost_desktop/locales"
                    or path.suffix != ".json" or not LANGUAGE_CODE.fullmatch(path.stem)):
                raise InstallError(LOCAL_CHANGES)
            location = self.project / path
            if location.is_symlink():
                raise InstallError(LOCAL_CHANGES)
            try:
                body = location.read_bytes()
                catalog_data(body, path.stem)
            except (OSError, ValueError):
                raise InstallError(LOCAL_CHANGES) from None
            edits[name] = (state, body)
        return self._git("rev-parse", "--verify", "HEAD").stdout.strip(), edits

    def _preserve_languages(self, head, edits):
        store = LocaleStore(self.locale_directory)
        try:
            for name, (state, body) in edits.items():
                baseline = self._git("show", head + ":" + name).stdout.encode("utf-8") if state == " M" else None
                store.adopt(Path(name).stem, body, baseline)
        except (OSError, ValueError) as error:
            raise InstallError(LANGUAGE_BACKUP_ERROR, str(error)) from None
        # Back up every file before restoring any tracked default. Preserve new
        # edits made during that work by refusing the update if the snapshot changed.
        if self._inspect() != (head, edits):
            raise InstallError(LOCAL_CHANGES)
        tracked = [name for name, (state, _body) in edits.items() if state == " M"]
        if tracked:
            self._git("restore", "--source=" + head, "--worktree", "--", *tracked)
        for name, (state, body) in edits.items():
            if state == "??":
                path = self.project / name
                try:
                    if path.read_bytes() != body:
                        raise InstallError(LOCAL_CHANGES)
                    path.unlink()
                except OSError as error:
                    raise InstallError(GIT_ERROR, str(error)) from None

    def install(self, expected_version, current_version=__version__):
        minimum = max(version_numbers(expected_version), version_numbers(current_version))
        before, edits = self._inspect()
        # Separate fetch and merge so the downloaded version and ancestry can be
        # checked before changing any working files. This is a fast-forward pull.
        self._git("fetch", "--no-tags", "--no-recurse-submodules", "--refmap=", "origin",
                  "refs/heads/main:refs/remotes/origin/main", timeout=90, error=DOWNLOAD_ERROR)
        target = self._git("rev-parse", "--verify", "FETCH_HEAD^{commit}").stdout.strip()
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", target):
            raise InstallError(VERSION_CHANGED)
        version_path = target + ":cyberghost_desktop/__init__.py"
        size = self._git("cat-file", "-s", version_path, error=VERSION_CHANGED).stdout.strip()
        if not size.isdigit() or int(size) > MAX_VERSION_BYTES:
            raise InstallError(VERSION_CHANGED)
        try:
            version = published_version(self._git("show", version_path).stdout.encode("utf-8"))
        except UpdateError:
            raise InstallError(VERSION_CHANGED) from None
        if version_numbers(version) < minimum:
            raise InstallError(VERSION_CHANGED)
        if self._git("cat-file", "-t", target + ":run.sh", error=VERSION_CHANGED).stdout.strip() != "blob":
            raise InstallError(VERSION_CHANGED)
        if self._git("merge-base", "--is-ancestor", before, target, allowed=(0, 1)).returncode:
            raise InstallError(LOCAL_COMMITS)
        # Downloads can take time: recheck local edits, branch and HEAD afterwards.
        if self._inspect() != (before, edits):
            raise InstallError(LOCAL_CHANGES)
        if edits:
            self._preserve_languages(before, edits)
            if self._inspect() != (before, {}):
                raise InstallError(LOCAL_CHANGES)
        try:
            self._git("merge", "--ff-only", "--no-autostash", "--no-edit", "--no-overwrite-ignore", target, timeout=60)
        except InstallError as error:
            # Never reset or discard files after an interrupted/failed checkout.
            try:
                changed = (self._git("rev-parse", "HEAD").stdout.strip() != before
                           or bool(self._git("status", "--porcelain=v1", "--untracked-files=all").stdout))
            except InstallError:
                changed = True
            raise InstallError(str(error), error.details, restart_required=changed) from None
        return InstallResult(version)


def install_update(expected_version, *, locale_directory=None):
    return CheckoutUpdater(locale_directory=locale_directory).install(expected_version)
