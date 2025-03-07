# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Gerrit python library.

This library contains all of the functionality for gerrit used by copybot.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import json
import logging
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import requests  # pylint: disable=import-error


logger = logging.getLogger(__name__)

# Matches a full 40-character commit hash.
_COMMIT_HASH_PATTERN = re.compile(r"\b[0-9a-f]{40}\b")

# Matches a full 40-character parent commit hash.
_PARENT_COMMIT_HASH_PATTERN = re.compile(r"parent \b[0-9a-f]{40}\b")


class MergeConflictBehavior(enum.Enum):
    """How to behave on merge conflicts.

    FAIL: Stop immediately. Don't upload anything.
    SKIP: Skip the commit that failed to merge. Summarize the failed
        commits at the end of the execution, and exit failure status.
    STOP: Stop immediately. Upload staged changes prior to conflict.
    ALLOW_CONFLICT: Commit the conflicted CL with conflicts.  Summarize
        the conflicted CLs at the end of execution, and exit failure
        status.  Conflicted CLs WILL be uploaded to the downstream.
        "Commit: false" will be added to the commit message to prevent
        GoB from committing conflicted changes before they are edited.
    """

    FAIL = enum.auto()
    SKIP = enum.auto()
    STOP = enum.auto()
    ALLOW_CONFLICT = enum.auto()


class ExclusionBehavior(enum.Enum):
    """How to behave on exclusions.

    DROP: Drop any CL containing content in the excluded directories.
        This method uses git path exclusions and generally runs faster.
    FILTER: Filter out files/folders matching the exclusion rules from
        the target CLs.
    """

    DROP = enum.auto()
    FILTER = enum.auto()


class MergeConflictError(Exception):
    """A commit cannot be cherry-picked due to a conflict."""


class EmptyCommitError(Exception):
    """A commit cannot be cherry-picked as it results in an empty commit."""


class CommitDoesNotApplyError(Exception):
    """A commit cannot be cherry-picked as it does not apply."""


class CopybotFatalError(Exception):
    """Copybot fatal error."""

    enum_name = "FAILURE_UNKNOWN"

    def __init__(
        self, *args: str, commits: Optional[Sequence[str]] = None, **kwargs: str
    ):
        self.commits = commits
        super().__init__(*args, **kwargs)


class UpstreamFetchError(CopybotFatalError):
    """Copybot died as the upstream failed to fetch."""

    enum_name = "FAILURE_UPSTREAM_FETCH_ERROR"


class DownstreamFetchError(CopybotFatalError):
    """Copybot died as the downstream failed to fetch."""

    enum_name = "FAILURE_DOWNSTREAM_FETCH_ERROR"


class PushError(CopybotFatalError):
    """Copybot died as it failed to push to the downstream GoB host."""

    enum_name = "FAILURE_DOWNSTREAM_PUSH_ERROR"


class MergeConflictsError(CopybotFatalError):
    """Copybot ran, but encountered merge conflicts."""

    enum_name = "FAILURE_MERGE_CONFLICTS"


@dataclasses.dataclass
class GerritClInfo:
    """Stores information for a Gerrit CL."""

    def __init__(self, change_id: str, hashtags: str, ref: str) -> None:
        """Initialize the Gerrit CL Info.

        Args:
            change_id: Change ID on Gerrit for this change.
            hashtags: Hashtags associated with this change
            ref: Current ref for this changes to be able to form a refspec
        """
        self.change_id = change_id
        self.hashtags = hashtags
        self.current_ref = ref


class GitRepo:
    """Class wrapping common Git repository actions."""

    def __init__(self, git_dir: Union[str, "os.PathLike[str]"]) -> None:
        self.git_dir = git_dir

    def _run_git(
        self, *args: Any, **kwargs: Any
    ) -> "subprocess.CompletedProcess[str]":
        """Wrapper to run git with the provided arguments."""
        argv = ["git", "-C", self.git_dir, "--no-pager", *args]
        logger.info("Run `%s`", " ".join(shlex.quote(str(arg)) for arg in argv))
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
        try:
            return subprocess.run(
                argv,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **kwargs,
            )
        except subprocess.CalledProcessError as e:
            logger.error("Git command failed!")
            logger.error("  STDOUT:")
            for line in e.stdout.splitlines():
                logger.error("    %s", line)
            logger.error("  STDERR:")
            for line in e.stderr.splitlines():
                logger.error("    %s", line)
            raise

    @classmethod
    def init(cls, git_dir: Union[str, "os.PathLike[str]"]) -> GitRepo:
        """Do a `git init` to create a new repository."""
        git_dir = pathlib.Path(git_dir)
        repo = cls(git_dir)
        if not (git_dir / ".git").exists():
            repo._run_git("init")  # pylint: disable=protected-access
        return repo

    def rev_parse(self, rev: str = "HEAD") -> str:
        """Do a `git rev-parse`."""
        result = self._run_git("rev-parse", rev)
        return result.stdout.rstrip()

    def fetch(self, remote: str, ref: str = "") -> str:
        """Do a `git fetch`.

        Returns:
            The full commit hash corresponding to FETCH_HEAD.
        """
        extra_args = []
        if ref:
            extra_args.append(ref)
        self._run_git("fetch", remote, *extra_args)
        return self.rev_parse("FETCH_HEAD")

    def checkout(self, ref: str) -> "subprocess.CompletedProcess[str]":
        """Do a `git checkout`."""
        return self._run_git("checkout", ref)

    def log(
        self,
        revision_range: str = "HEAD",
        fmt: str = "",
        num: int = 0,
        subtree: Union[str, "os.PathLike[str]"] = "",
        exclude_file_patterns: Iterable[str | "os.PathLike[str]"] = (),
    ) -> "subprocess.CompletedProcess[str]":
        """Do a `git log`."""
        extra_args = ["--first-parent"]
        if fmt:
            extra_args.append(f"--format={fmt}")
        if num:
            extra_args.append(f"-n{num}")
        extra_args.append("--")
        extra_args.extend(
            [f":!{path}" for path in (exclude_file_patterns or [])]
        )
        if subtree:
            extra_args.append(str(subtree))
        return self._run_git("log", revision_range, *extra_args)

    def log_hashes(
        self,
        revision_range: str = "HEAD",
        num: int = 0,
        subtree: Union[str, "os.PathLike[str]"] = "",
        exclude_file_patterns: Iterable[str | "os.PathLike[str]"] = (),
    ) -> List[str]:
        """Get the commit log as a list of commit hashes."""
        result = self.log(
            revision_range=revision_range,
            fmt="%H",
            num=num,
            subtree=subtree,
            exclude_file_patterns=exclude_file_patterns,
        )
        return result.stdout.splitlines()

    def get_commit_message(self, rev: str = "HEAD") -> str:
        """Get a commit message of a commit."""
        result = self.log(revision_range=rev, num=1, fmt="%B")
        return result.stdout

    def get_author_email(self, rev: str = "HEAD") -> str:
        """Get the authors email of a commit."""
        result = self.log(revision_range=rev, num=1, fmt="%aE")
        return result.stdout.strip()

    def get_author_name(self, rev: str = "HEAD") -> str:
        """Get the authors name of a commit."""
        result = self.log(revision_range=rev, num=1, fmt="%aN")
        return result.stdout.strip()

    def get_subject(self, rev: str = "HEAD") -> str:
        """Get the subject of a commit."""
        result = self.log(revision_range=rev, num=1, fmt="%s")
        return result.stdout.strip()

    def commit_file_list(self, rev: str = "HEAD") -> List[str]:
        """Get the files modified by a commit."""
        result = self._run_git("show", "--pretty=", "--name-only", rev)
        return result.stdout.splitlines()

    def show(
        self, rev: str = "HEAD", files: Iterable[str] = (), patch_dir=""
    ) -> str:
        """Do a `git show`."""
        extra_args = ["--output", f"{patch_dir}/filtered.patch"]
        if files:
            extra_args.append("--")
            extra_args.extend(files)

        self._run_git("show", rev, *extra_args)
        return f"{patch_dir}/filtered.patch"

    def apply(
        self,
        patch: Union[str, "os.PathLike[str]"],
        path: Union[str, "os.PathLike[str]"] = "",
        include_paths: Optional[List[Union[str, "os.PathLike[str]"]]] = None,
        exclude_paths: Optional[List[Union[str, "os.PathLike[str]"]]] = None,
    ) -> "subprocess.CompletedProcess[str]":
        """Apply a patch to the staging area."""
        extra_args = []
        if path and str(path) != ".":
            extra_args.append(f"--directory={path}")
        if include_paths:
            extra_args.extend(f"--include={path}" for path in include_paths)
        if exclude_paths:
            extra_args.extend(f"--exclude={path}" for path in exclude_paths)

        extra_args.append(str(patch))
        return self._run_git("apply", *extra_args)

    def commit(
        self,
        message: str,
        amend: bool = False,
        sign_off: bool = False,
        stage: bool = False,
        update_author: str = "",
    ) -> str:
        """Create a commit.

        Returns:
            The commit hash.
        """
        extra_args = []
        if update_author:
            extra_args.extend(["--author", update_author])
        if stage:
            extra_args.append("--all")
        if amend:
            extra_args.append("--amend")
        if sign_off:
            extra_args.append("--signoff")
        self._run_git("commit", *extra_args, "-m", message)
        return self.rev_parse()

    def reword(
        self, new_message: str, sign_off: bool = False, update_author: str = ""
    ) -> str:
        """Reword the commit at HEAD.

        Returns:
            The new commit hash.
        """
        return self.commit(
            new_message,
            amend=True,
            sign_off=sign_off,
            update_author=update_author,
        )

    def format_patch(
        self,
        rev: str,
        output_path: Union[str, "os.PathLike[str]"],
        num: int = 1,
        relative_path: Union[str, "os.PathLike[str]"] = "",
    ) -> pathlib.Path:
        """Generate patch from revision with an optional relative path.

        Returns:
            Path of the output patch.
        """
        extra_args = []
        if relative_path and str(relative_path) != ".":
            extra_args.append(f"--relative={relative_path}")
        extra_args.extend([f"-{num}", rev, f"--output-directory={output_path}"])

        result = self._run_git("format-patch", *extra_args)
        return pathlib.Path(result.stdout.rstrip())

    def add(
        self,
        path: Union[str, "os.PathLike[str]"],
        stage: bool = False,
        force: bool = False,
    ) -> "subprocess.CompletedProcess[str]":
        """Add unstaged files."""
        extra_args = []
        if stage:
            extra_args.append("--all")
        if force:
            extra_args.append("--force")
        if path:
            extra_args.append(str(path))
        return self._run_git("add", *extra_args)

    def get_subtree_lowest_working_dir(
        self, path: Union[str, "os.PathLike[str]"]
    ) -> Union[str, "os.PathLike[str]"]:
        """Get the lowest working directory path for subtree."""
        patch_dir = pathlib.Path(self.git_dir)
        if path:
            patch_dir = patch_dir / path
        subtree = path
        if not patch_dir.is_dir():
            subtree = pathlib.Path(path).parents[0]
        return subtree

    @contextlib.contextmanager
    def temp_worktree(self, rev="HEAD"):
        """Context manager to create and destroy a temporary worktree."""
        # pylint: disable=consider-using-with
        tmpdir = tempfile.TemporaryDirectory()
        try:
            worktree_dir = pathlib.Path(tmpdir.name)
            self._run_git("worktree", "add", "-d", worktree_dir, rev)
            try:
                yield self.__class__(worktree_dir)
            finally:
                self._run_git("worktree", "remove", "--force", worktree_dir)
        finally:
            # We use a try/finally to cleanup the temporary directory
            # instead of a context manager as, in the successful
            # condition, the worktree directory will no longer exist
            # (git will have removed it).  In Python 3.10+, one can
            # use ignore_cleanup_errors=True, but the chroot is on
            # Python 3.6 right now.
            try:
                tmpdir.cleanup()
            except FileNotFoundError:
                pass

    def filter_commit(
        self,
        rev: str = "HEAD",
        patch_dir: Union[str, "os.PathLike[str]"] = "",
        files: Iterable[str] = (),
    ):
        """Filter a commit to just certain files.

        Returns:
            The new commit hash.
        """
        old_message = self.get_commit_message(rev)
        patch = self.show(rev, files, patch_dir)

        with self.temp_worktree(f"{rev}~1") as worktree:
            worktree.apply(patch)
            worktree.add("", stage=True, force=True)
            return worktree.commit(old_message)

    def is_merge_commit(
        self,
        rev: str = "HEAD",
    ):
        extra_args = ["-p"]
        result = self._run_git("cat-file", rev, *extra_args)
        return (
            len(
                re.findall(
                    _PARENT_COMMIT_HASH_PATTERN, str(result.stdout.rstrip())
                )
            )
            > 1
        )

    def cherry_pick(
        self,
        rev: str,
        patch_dir: Union[str, "os.PathLike[str]"] = "",
        upstream_subtree: Union[str, "os.PathLike[str]"] = "",
        downstream_subtree: Union[str, "os.PathLike[str]"] = "",
        include_paths: Optional[List[Union[str, "os.PathLike[str]"]]] = None,
        exclude_paths: Optional[List[Union[str, "os.PathLike[str]"]]] = None,
        allow_conflict: bool = False,
    ) -> None:
        """Do a `git cherry-pick`.

        This will first try without any merge options, and if that fails,
        try again with -Xpatience, which is slower, but may be more likely
        to resolve a merge conflict.

        Raises:
            EmptyCommitError: The resultant commit was empty and should be
                skipped.
            CommitDoesNotApplyError: The desired upstream CL does not apply
                to the downstream repo.
            MergeConflictError: There was a merge conflict that could not
                be resolved automatically with -Xpatience.
        """

        def _try_cherry_pick(extra_flags: List[str]) -> None:
            try:
                self._run_git("cherry-pick", "-x", rev, *extra_flags)
            except subprocess.CalledProcessError as e:
                try:
                    self._run_git("rev-parse", "--verify", "CHERRY_PICK_HEAD")
                    logger.warning("Could not cherry-pick")
                except subprocess.CalledProcessError as err:
                    if "is a merge but no -m option was given" in e.stderr:
                        logger.warning("Merge commit detected")
                    raise MergeConflictError() from err
                if allow_conflict:
                    if "patch does not apply" in e.stderr:
                        logger.warning("Patch does not apply to downstream")
                        raise CommitDoesNotApplyError() from e
                    self.add(downstream_subtree, stage=True, force=True)
                    self.commit(
                        self.get_commit_message(rev),
                        amend=False,
                        sign_off=False,
                        stage=True,
                    )
                else:
                    self._run_git("cherry-pick", "--abort")
                    if "The previous cherry-pick is now empty" in e.stderr:
                        raise EmptyCommitError() from e
                    raise MergeConflictError() from e

        cherry_pick_flag_list: List[List[str]] = []
        if self.is_merge_commit(rev):
            cherry_pick_flag_list += [
                ["-m", "1"],
                ["-m", "1", "-Xpatience"],
                ["-m", "2"],
                ["-m", "2", "-Xpatience"],
            ]
        else:
            cherry_pick_flag_list += [
                [],
                ["-Xpatience"],
                ["--strategy=recursive", "-X", "theirs"],
            ]
        if downstream_subtree or upstream_subtree or include_paths:
            cherry_pick_flag_list = []
        for flags in cherry_pick_flag_list:
            try:
                _try_cherry_pick(flags)
            except MergeConflictError:
                continue
            else:
                return
        patch = self.format_patch(
            rev,
            patch_dir,
            1,
            self.get_subtree_lowest_working_dir(upstream_subtree),
        )
        try:
            self.apply(
                patch=patch,
                path=self.get_subtree_lowest_working_dir(downstream_subtree),
                include_paths=include_paths,
                exclude_paths=exclude_paths,
            )
        except subprocess.CalledProcessError as e:
            if not allow_conflict:
                raise MergeConflictError() from e
            if (
                'No valid patches in input (allow with "--allow-empty")'
                in e.stderr
            ):
                raise EmptyCommitError() from e
        self.add(downstream_subtree, stage=True, force=True)
        try:
            self.commit(
                self.get_commit_message(rev),
                amend=False,
                sign_off=False,
                stage=True,
            )
        except subprocess.CalledProcessError as e:
            if "nothing to commit, working tree clean" in e.stderr:
                logger.info("Empty commit error")
                raise EmptyCommitError() from e
        return

    def push(self, url: str, refspec: str, options: Iterable[str] = ()) -> None:
        """Do a `git push`."""
        args = []

        if options:
            for option in options:
                args.extend(["-o", option])

        args.append(url)
        args.append(refspec)
        self._run_git("push", *args)

    def get_cl_count(
        self,
        original_rev: str,
        current_rev: str,
        subtree: Union[str, "os.PathLike[str]"] = "",
    ) -> int:
        """Get the number of CLs between the specified revisions."""
        if not original_rev or not current_rev:
            return 0
        args = ["--count"]
        args.append(f"{original_rev}..{current_rev}")
        if subtree:
            args.append("--")
            args.append(str(subtree))
        result = self._run_git("rev-list", *args)
        return int(result.stdout.rstrip())


class Pseudoheaders:
    """Dictionary-like object for the pseudoheaders from a commit message.

    The pseudoheaders are the header-like lines often found in the
    bottom of a commit message.  Header names are case-insensitive.

    Pseudoheaders are parsed the same way that the "git footers"
    command parses them.
    """

    # Matches lines that look like a "header" (the conventional footer
    # lines in a commit message).
    _PSEUDOHEADER_PATTERN = re.compile(r"^(?:[A-Za-z0-9]+-)*[A-Za-z0-9]+:\s+")

    def __init__(self, header_list: Iterable[Tuple[str, str]] = ()) -> None:
        if header_list:
            self._header_list = list(header_list)
        else:
            self._header_list = []

    @classmethod
    def from_commit_message(
        cls, commit_message: str, offset: int = 1
    ) -> Tuple[Pseudoheaders, str]:
        """Parse pseudoheaders from a commit message.

        Args:
            commit_message: commit message from git log.
            offset: which line to start processing the message from.
                Lines less than the offset will not be altered.

        Returns:
            Two values, a Pseudoheaders dictionary, and the commit
            message without any pseudoheaders.
        """
        message_lines = commit_message.splitlines()
        rewritten_message = []

        header_list = []
        for i, line in enumerate(message_lines):
            if i < offset or not cls._PSEUDOHEADER_PATTERN.match(line):
                rewritten_message.append(line)
            else:
                name, _, value = line.partition(":")
                header_list.append((name, value.strip()))

        return cls(header_list), "".join(
            f"{line}\n" for line in rewritten_message
        )

    def prefix(
        self, prefix: str = "Original-", keep: Iterable[str] = ()
    ) -> Pseudoheaders:
        """Prefix all header keys with a string.

        Args:
            prefix: The prefix to use.
            keep: Headers which should not be modified.

        Returns:
            A new Pseudoheaders dictionary.
        """
        new_header_list = []

        # Constructing a new pseudoheaders dictionary ensures we
        # consider the keep list to be case insensitive.
        keep_dict: Pseudoheaders = Pseudoheaders()
        if keep:
            keep_dict = self.__class__([(key, "Keeping") for key in keep])

        for key, value in self._header_list:
            if keep_dict.get(key):
                new_header_list.append((key, value))
            else:
                new_header_list.append((f"{prefix}{key}", value))
        return self.__class__(new_header_list)

    def __getitem__(self, item: str) -> str:
        """Get a header value by name."""
        for key, value in self._header_list:
            if key.lower() == item.lower():
                return value
        raise KeyError(item)

    def get(self, item: str, default: str = "") -> str:
        """Get a header value by name, or return a default value."""
        try:
            return self[item]
        except KeyError:
            return default

    def as_dict(self) -> Dict[str, str]:
        """Get the dict of stored values."""
        return dict(self._header_list)

    def __setitem__(self, key: str, value: str) -> None:
        """Add a header."""
        self._header_list.append((key, value))

    def add_to_commit_message(self, commit_message: str) -> str:
        """Add our pseudoheaders to a commit message.

        Returns:
            The new commit message.
        """
        message_lines = commit_message.splitlines()

        if not message_lines:
            message_lines = ["NO COMMIT MESSAGE"]

        # Ensure exactly one blank line separating body and pseudoheaders.
        while not message_lines[-1].strip():
            message_lines.pop()

        message_lines.append("")

        for key, value in self._header_list:
            message_lines.append(f"{key}: {value}")
        return "".join(f"{line}\n" for line in message_lines)

    def __str__(self) -> str:
        return "\n".join(f"{key}:{value}" for key, value in self._header_list)

    def update(self, other: Union[dict, Pseudoheaders]) -> None:
        if isinstance(other, type(self)):
            for key, value in other.as_dict().items():
                self[key] = value
        elif isinstance(other, dict):
            for key, value in other:
                self[key] = value
        else:
            raise TypeError(f"Other class has conflicting type({type(other)})")


class Gerrit:
    """Wrapper for actions on a Gerrit host."""

    def __init__(self, hostname: str) -> None:
        self.hostname = hostname

    def search(self, query: str) -> List[Dict[str, Any]]:
        """Do a query on Gerrit."""
        url = f"https://{self.hostname}/changes/"
        params = [
            ("q", query),
            ("o", "CURRENT_REVISION"),
            ("o", "CURRENT_COMMIT"),
            ("o", "COMMIT_FOOTERS"),
        ]
        while True:
            r = requests.get(url, params=params)
            if r.ok:
                break
            if r.status_code == requests.codes.too_many:
                time.sleep(1)
                continue
            r.raise_for_status()
            assert False

        if r.text[:5] != ")]}'\n":
            logger.error("Bad response from Gerrit: %r", r.text)
            raise ValueError("Unexpected JSON payload from gerrit")

        result = json.loads(r.text[5:])
        return result

    def find_pending_changes(
        self,
        project: str,
        branch: str,
        hashtags: Iterable[str] = (),
        subtree: Union[str, "os.PathLike[str]"] = "",
        exclude_paths: Iterable[str | "os.PathLike[str]"] = (),
    ) -> Tuple[Dict[str, GerritClInfo], Dict[str, GerritClInfo]]:
        """Find pending changes previously opened by CopyBot on Gerrit.

        Returns:
            A dictionary mapping upstream commit hashes to their
            current Change-Id on Gerrit and their associated hashtags.
        """
        query = [
            "(status:open or status:abandoned)",
            f"project:{project}",
            f"branch:{branch}",
        ]
        _, subtree_ext = os.path.splitext(subtree)
        if hashtags:
            query.extend(f"hashtag:{hashtag}" for hashtag in hashtags)
        if subtree and not subtree_ext:
            query.append(f"directory:{subtree}")
        if exclude_paths:
            for path in exclude_paths:
                _, path_ext = os.path.splitext(path)
                if not path_ext:
                    query.append(f"-directory:{path}")

        query_result = self.search(" ".join(query))
        pending_change_ids = {}
        abandoned_change_ids = {}
        for cl in query_result:
            change_id = cl["change_id"]
            current_revision_hash = cl["current_revision"]
            current_revision_data = cl["revisions"][current_revision_hash]
            commit_message = current_revision_data["commit"]["message"]
            origin_rev_id = get_origin_rev_id(commit_message)
            rev_id = []
            if origin_rev_id:
                rev_id.append(origin_rev_id)
            else:
                for commit_hash in _COMMIT_HASH_PATTERN.finditer(
                    commit_message
                ):
                    rev_id.append(commit_hash.group(0))
            for rev in rev_id:
                if cl["status"] == "ABANDONED":
                    abandoned_change_ids[rev] = GerritClInfo(
                        change_id,
                        cl["hashtags"].copy(),
                        current_revision_data["ref"],
                    )
                else:
                    pending_change_ids[rev] = GerritClInfo(
                        change_id,
                        cl["hashtags"].copy(),
                        current_revision_data["ref"],
                    )
        return pending_change_ids, abandoned_change_ids


def generate_change_id() -> str:
    """Generate a Unique Change-Id."""
    return f"I{os.urandom(20).hex()}"


def get_change_id(commit_message: str) -> str:
    """Get the Change-Id from a commit message.

    Returns:
        The Change-Id if one was found, or None otherwise.
    """
    pseudoheaders, _ = Pseudoheaders.from_commit_message(commit_message)
    return pseudoheaders.get("Change-Id")


def get_origin_rev_id(commit_message: str) -> str:
    """Get the origin revision hash from a commit message.

    Returns:
        The revision hash if one was found, or None otherwise.
    """
    pseudoheaders, _ = Pseudoheaders.from_commit_message(commit_message)
    origin_revid = pseudoheaders.get("GitOrigin-RevId")
    if not origin_revid:
        origin_revid = pseudoheaders.get("Original-Commit-Id")
    return origin_revid
