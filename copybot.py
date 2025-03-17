#!/usr/bin/env vpython3
# Copyright 2022 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""CopyBot script.

This script copies commits from one repo (the "upstream") to another
(the "downstream").

Usage: copybot.py [options...] upstream_repo:branch downstream_repo:branch
"""

# [VPYTHON:BEGIN]
# python_version: "3.11"
# wheel: <
#   name: "infra/python/wheels/configargparse-py3"
#   version: "version:1.7"
# >
# wheel: <
#   name: "infra/python/wheels/requests-py3"
#   version: "version:2.31.0"
# >
# wheel: <
#   name: "infra/python/wheels/certifi-py2_py3"
#   version: "version:2020.11.8"
# >
# wheel: <
#   name: "infra/python/wheels/idna-py2_py3"
#   version: "version:2.8"
# >
# wheel: <
#   name: "infra/python/wheels/charset_normalizer-py3"
#   version: "version:2.0.4"
# >
# wheel: <
#   name: "infra/python/wheels/urllib3-py2_py3"
#   version: "version:1.26.6"
# >
# [VPYTHON:END]

from __future__ import annotations

from collections.abc import Iterable
import json
import logging
import os
import pathlib
import re
import subprocess
import tempfile
from typing import Any, Final

import copybot_argparser
import gerrit


PRESERVE_TAG: Final[str] = "copybot-preserve"
REWORD_TAG: Final[str] = "copybot-reword"
REBASE_TAG: Final[str] = "copybot-rebase"


logger = logging.getLogger(__name__)


class NothingToDo(Exception):
    """Break out of control flow when there is nothing to do."""


def are_repos_related(
    upstream: copybot_argparser.TargetConfig,
    downstream: copybot_argparser.TargetConfig,
) -> bool:
    """Checks if repos are either same or on Git-on-Borg instances."""
    return bool(
        upstream.url == downstream.url
        or (
            is_server_gob(str(downstream.url))
            and is_server_gob(str(upstream.url))
        )
    )


def find_last_merged_rev(
    repo: gerrit.GitRepoInterface,
    upstream: copybot_argparser.UpstreamConfig,
    downstream: copybot_argparser.DownstreamConfig,
    exclude_file_patterns: Iterable[str | "os.PathLike[str]"] = (),
    pending_changes: dict[str, gerrit.GerritClInfo] | None = None,
) -> tuple[str, int]:
    """Find the last merged revision in a Git repo.

    Args:
        repo: The GitRepo.
        upstream: Configuration for upstream location.
        downstream: Configuration for downstream location.
        exclude_file_patterns: list of paths to be excluded.
        pending_changes: Changes pending in downstream repo.

    Returns:
        Two values,
            1. A commit hash of the last merged revision by CopyBot, or the
                first common commit hash in both logs.
            2. The number of CLs which are eligible to be downstreamed.

    Raises:
        ValueError: No common history could be found.
    """
    upstream_hashes = repo.log_hashes(
        revision_range=upstream.head_sha,
        subtree=upstream.subtree,
        exclude_file_patterns=exclude_file_patterns,
        num=upstream.history_length,
    )
    downstream_hashes = repo.log_hashes(
        revision_range=downstream.head_sha,
        subtree=downstream.subtree,
        exclude_file_patterns=exclude_file_patterns,
        num=downstream.history_length,
    )

    upstream_change_ids = {}
    include_change_id = are_repos_related(upstream, downstream)
    if include_change_id:
        for rev in upstream_hashes:
            change_id = gerrit.get_change_id(repo.get_commit_message(rev))
            if change_id:
                upstream_change_ids[change_id] = rev

    for rev in downstream_hashes:
        commit_message = repo.get_commit_message(rev)
        origin_revid = gerrit.get_origin_rev_id(commit_message)
        change_id = gerrit.get_change_id(commit_message)
        if (
            rev in upstream_hashes
            or origin_revid
            or (change_id and include_change_id)
        ):
            if origin_revid in upstream_hashes:
                counter = upstream_hashes.index(origin_revid or rev)
            elif include_change_id and change_id in upstream_change_ids:
                origin_revid = upstream_change_ids[change_id]
                counter = upstream_hashes.index(origin_revid or rev)
            else:
                continue
            return origin_revid or rev, counter

    for rev in upstream_hashes:
        if pending_changes and rev in pending_changes:
            counter = upstream_hashes.index(rev)
            return rev, counter

    raise ValueError(
        "Downstream has no GitOrigin-RevId commits, and upstream and "
        "downstream share no common history."
    )


def get_downstreamed_list(
    repo: gerrit.GitRepoInterface,
    downstream: copybot_argparser.DownstreamConfig,
    exclude_file_patterns: Iterable[str | "os.PathLike[str]"] = (),
    upstream_change_ids: dict[str, str] | None = None,
) -> list[str]:
    """Find the last merged revision in a Git repo.

    Args:
        repo: The GitRepo.
        downstream: Configuration for downstream location.
        exclude_file_patterns: list of paths to be excluded.
        upstream_change_ids: dictionary of upstream Change-Id's and their
            associated upstream commit hash.

    Returns:
        The set of upstream commit hashes that have already been downstreamed.
    """
    downstream_hashes = repo.log_hashes(
        revision_range=downstream.head_sha,
        subtree=downstream.subtree,
        exclude_file_patterns=exclude_file_patterns,
        num=downstream.history_length,
    )
    downstreamed_revs = list(downstream_hashes)

    for counter, rev in enumerate(downstream_hashes):
        if counter > downstream.history_limit > 0:
            break

        commit_message = repo.get_commit_message(rev)
        origin_revid = gerrit.get_origin_rev_id(commit_message)
        change_id = gerrit.get_change_id(commit_message)

        if origin_revid:
            downstreamed_revs.append(origin_revid)
        if (
            change_id
            and upstream_change_ids
            and change_id in upstream_change_ids
        ):
            downstreamed_revs.append(upstream_change_ids[change_id])
    return downstreamed_revs


def find_commits_to_copy(
    repo: gerrit.GitRepoInterface,
    upstream: copybot_argparser.UpstreamConfig,
    downstream: copybot_argparser.DownstreamConfig,
    exclude_file_patterns: Iterable[str | "os.PathLike[str]"] = (),
    filter_file_patterns: list[re.Pattern[Any]] | None = None,
    pending_changes: dict[str, gerrit.GerritClInfo] | None = None,
    abandoned_changes: dict[str, gerrit.GerritClInfo] | None = None,
    skip_copybot_job_names: Iterable[str] = (),
    skip_author_emails: Iterable[str] = (),
    include_change_id: bool = False,
) -> tuple[
    list[str], dict[str, list[str]], dict[str, list[str]], list[str], bool
]:
    """Find the commits to copy to downstream.

    Args:
        repo: The GitRepo.
        upstream: Configuration for upstream location.
        downstream: Configuration for downstream location.
        exclude_file_patterns: File paths that should not be copied.
            CLs will be dropped containing these paths.
        filter_file_patterns: File paths that should be filtered out.
            CLs will be modified to drop these paths.
        pending_changes: Changes pending in downstream repo.
        abandoned_changes: Changes abandoned in downstream repo.
        skip_copybot_job_names: Names of copybot jobs to not copy CLs from
        skip_author_emails: Emails of authors to not copy CLs from
        include_change_id: Bool specifying whether or not to
            consider Change-Ids

    Returns:
        * A list of the commit hashes to copy.
        * A dictionary mapping commit hashes to the files that should be
        included.
        * A dictionary mapping commit hashes to the files
        that should be skipped.
        * A list of the CLs found with the
        copybot-skip hashtag.
        * A boolean denoting if there are pending changes that should be
        acted upon.

    Raises:
        ValueError: If the provided last merged commit hash does not
           exist in upstream commit history.
    """
    commits_to_copy = []
    commit_files_map = {}
    skipped_files_map = {}
    copybot_skip_cls = []
    upstream_change_ids = {}
    upstream_hashes = repo.log_hashes(
        revision_range=upstream.head_sha,
        subtree=upstream.subtree,
        exclude_file_patterns=exclude_file_patterns,
        num=upstream.history_length,
    )
    if include_change_id:
        for counter, rev in enumerate(upstream_hashes):
            if counter > upstream.history_limit > 0:
                break
            change_id = gerrit.get_change_id(repo.get_commit_message(rev))
            if change_id:
                upstream_change_ids[change_id] = rev
    downstreamed_revs = get_downstreamed_list(
        repo=repo,
        downstream=downstream,
        exclude_file_patterns=exclude_file_patterns,
        upstream_change_ids=upstream_change_ids,
    )

    counter = 0
    pending_to_submit = False
    for rev in upstream_hashes:
        # Early exit if limit reached to avoid inadvertent continuation
        if counter > upstream.history_limit > 0:
            logger.info("Hit upstream limit of %s", upstream.history_limit)
            break

        # Check if this is a filtered commit.
        commit_message = repo.get_commit_message(rev)
        (
            pseudoheaders,
            commit_message,
        ) = gerrit.Pseudoheaders.from_commit_message(commit_message)
        job_name = pseudoheaders.get("Copybot-Job-Name")
        if skip_copybot_job_names and job_name in skip_copybot_job_names:
            logger.info(
                "Skip %s due to Copybot-Job-Name: %s",
                rev,
                job_name,
            )
            continue

        if not job_name and skip_author_emails:
            author_email = repo.get_author_email(rev=rev)
            if author_email in skip_author_emails:
                logger.info(
                    "Skip %s due to author %s",
                    rev,
                    author_email,
                )
                continue

        # Increment counter after filtering Copybot-Job-Name CLs to treat
        # them as if they don't belong to the target repo.
        counter += 1

        commit_subject = repo.get_subject(rev=rev)
        if "marking set of ebuilds as stable" in commit_subject.lower():
            logger.info(
                "Skip %s due to marking ebuilds stable in subject: %s",
                rev,
                commit_subject,
            )
            continue

        if abandoned_changes and rev in abandoned_changes:
            logger.info("Skipping %s because it's abandoned", rev)
            continue

        if pending_changes and rev in pending_changes:
            if "copybot-skip" in pending_changes[rev].hashtags:
                logger.info("Skip %s due to copybot-skip hashtag", rev)
                copybot_skip_cls.append(rev)
                continue

        # If change is in pending list, allow relands
        if rev in downstreamed_revs:
            if pending_changes and rev in pending_changes:
                logger.info(
                    "Found pending change that has already merged: %s", rev
                )
            else:
                logger.info("Skip %s because it has already merged", rev)
                continue

        commit_files = repo.commit_file_list(rev)
        filtered_commit_files = []
        for path in commit_files:
            if not any(
                re.fullmatch(p, path) for p in filter_file_patterns or []
            ):
                filtered_commit_files.append(path)

        if not filtered_commit_files:
            logger.info(
                "Skip commit %s due to empty file list after filtering "
                "(before filtering was %r)",
                rev,
                commit_files,
            )
            continue

        commit_files_map[rev] = filtered_commit_files
        skipped_files_map[rev] = [
            path for path in commit_files if path not in filtered_commit_files
        ]

        if downstream.subtree and downstream.include_paths:
            commit_files = repo.commit_file_list(rev)
            filtered_commit_files = []
            for path in commit_files:
                filtered_path = pathlib.Path(path).relative_to(upstream.subtree)
                if filtered_path in downstream.include_paths:
                    filtered_commit_files.append(path)
                    break

            if not filtered_commit_files:
                logger.info(
                    "Skip commit %s due to empty file list after filtering "
                    "(before filtering was %r)",
                    rev,
                    commit_files,
                )
                continue
        if pending_changes and rev in pending_changes:
            pending_to_submit = True
        commits_to_copy.append(rev)

    return (
        commits_to_copy,
        commit_files_map,
        skipped_files_map,
        copybot_skip_cls,
        pending_to_submit,
    )


def rewrite_commit_message(
    repo: gerrit.GitRepoInterface,
    upstream_rev: str,
    upstream: copybot_argparser.UpstreamConfig,
    downstream: copybot_argparser.DownstreamConfig,
    change_id: str,
    skipped_files=(),
    sign_off: bool = False,
    additional_pseudoheaders: Iterable[str] = (),
) -> None:
    """Reword the commit at HEAD with appropriate metadata.

    Args:
        repo: The GitRepo to operate on.
        upstream_rev: The upstream commit hash corresponding to this commit.
        upstream: Configuration for upstream location.
        downstream: Configuration for downstream location.
        change_id: The Change-Id to add to the commit.
        skipped_files: The list of files skipped.
        sign_off: True if Signed-off-by should be added to the commit message.
        keep_pseudoheaders: Pseudoheaders which should not be prefixed.
        additional_pseudoheaders: Psuedoheaders to be added to the commit
            message.
    """
    commit_message = repo.get_commit_message()
    if downstream.prepend_subject:
        commit_message = downstream.prepend_subject + commit_message
    if downstream.insert_into_msg:
        tmp_commit_msg = commit_message.splitlines()
        for line, msg in sorted(
            downstream.insert_into_msg.items(), reverse=True
        ):
            tmp_commit_msg.insert(line, msg)
        commit_message = "\n".join(tmp_commit_msg)

    if are_repos_related(upstream, downstream):
        if "Change-Id" not in downstream.keep_pseudoheaders:
            downstream.keep_pseudoheaders.append("Change-Id")

    pseudoheaders, commit_message = gerrit.Pseudoheaders.from_commit_message(
        commit_message
    )
    pseudoheaders = pseudoheaders.prefix(keep=downstream.keep_pseudoheaders)

    for path in skipped_files:
        pseudoheaders["CopyBot-Skipped-File"] = path

    pseudoheaders["GitOrigin-RevId"] = upstream_rev
    if additional_pseudoheaders:
        for additional_header in additional_pseudoheaders:
            parsed, _ = gerrit.Pseudoheaders.from_commit_message(
                additional_header, offset=0
            )
            pseudoheaders.update(parsed)
    if (
        not pseudoheaders.get("Change-Id")
        or not downstream.keep_pseudoheaders
        or "Change-Id" not in downstream.keep_pseudoheaders
    ):
        pseudoheaders["Change-Id"] = change_id

    commit_message = pseudoheaders.add_to_commit_message(commit_message)
    orig_author = repo.get_author_email(rev=upstream_rev)
    author, sym, domain = orig_author.rpartition("@")
    orig_author_name = repo.get_author_name(rev=upstream_rev)
    updated_author = (
        orig_author_name + "<" + author + sym + domain + "-copybot-pick" + ">"
    )
    repo.reword(commit_message, sign_off=sign_off, update_author=updated_author)


def get_push_refspec(
    config: copybot_argparser.CopybotConfig,
    downstream_branch: str,
    skip_cq: bool,
) -> str:
    """Generate a push refspec for Gerrit.

    Args:
        config: The parsed command line arguments.
        downstream_branch: The branch to push to.
        skip_cq: Whether the copied CL stack should not be submitted to CQ.

    Returns:
        A push refspec as a string.
    """
    push_options = ["ready"]

    def _add_push_option(key, value):
        for option in value.split(","):
            push_options.append(f"{key}={option}")

    for label in config.downstream.labels:
        if skip_cq and (label in ["Bot-Commit+1", "Commit-Queue+2"]):
            logger.info("Skipping CQ")
            continue
        _add_push_option("l", label)

    for cc in config.downstream.ccs:
        _add_push_option("cc", cc)

    for reviewer in config.downstream.reviewers:
        _add_push_option("r", reviewer)

    for hashtag in [config.topic, *config.downstream.hashtags]:
        _add_push_option("t", hashtag)

    return f"HEAD:refs/for/{downstream_branch}%{','.join(push_options)}"


def is_server_gob(url: str) -> re.Match[str] | None:
    return re.fullmatch(
        r"https://(chromium|chrome-internal)"
        r"(?:-review)?\.googlesource\.com/(.*)",
        url,
    )


def fetch_repo_head_sha(
    repo: gerrit.GitRepoInterface, url: str, branch: str
) -> str:
    """Fetch HEAD sha of the given repository and branch."""
    try:
        return repo.fetch(url, branch)
    except subprocess.CalledProcessError as e:
        raise gerrit.FetchError(
            f"Failed to fetch branch {branch} from {url}"
        ) from e


def fetch_history_length(
    repo: gerrit.GitRepoInterface,
    target: copybot_argparser.TargetConfig,
    location: str,
) -> int:
    """Fetch the history length from where it starts to HEAD."""
    if target.history_starts_with:
        history_length = (
            repo.get_cl_count(
                target.history_starts_with,
                target.head_sha,
                target.subtree,
            )
            + 1
        )
        logger.info("%s history length: %d", location, history_length)
        return history_length
    return 0


def verify_repos_share_history_to_adjust_limits(
    repo: gerrit.GitRepoInterface,
    config: copybot_argparser.CopybotConfig,
    pending_changes: dict[str, gerrit.GerritClInfo],
) -> None:
    """Verify history and adjust limits if there are more CLs downstream."""

    num_cls_to_downstream = 0
    last_related_rev = ""

    last_related_rev, num_cls_to_downstream = find_last_merged_rev(
        repo,
        config.upstream,
        config.downstream,
        config.drop_paths,
        pending_changes=pending_changes,
    )
    if last_related_rev in pending_changes:
        logger.info("Last related revision from pending changes!")
    logger.info("Last related revision: %s", last_related_rev)

    logger.info("Found: %s new changes to downstream", num_cls_to_downstream)

    pending_modifications = False
    for _, pending_cl in pending_changes.items():
        if (
            REWORD_TAG in pending_cl.hashtags
            or PRESERVE_TAG not in pending_cl.hashtags
        ):
            pending_modifications = True
            break
    if not num_cls_to_downstream and not pending_modifications:
        raise NothingToDo("No CLs to downstream, and no pending modifications")

    num_cls_to_downstream += len(pending_changes)

    if num_cls_to_downstream > config.upstream.history_limit > 0:
        logger.warning(
            "There are %s CLs between HEAD and %s but the history limit is"
            " set to %s. Raising the history limit to accommodate this.",
            num_cls_to_downstream,
            last_related_rev,
            config.upstream.history_limit,
        )
        config.upstream.history_limit = num_cls_to_downstream
        # The reference CL may have been cherry-picked out of order.
        # Remove the downstream limit to find it in the history correctly.
        config.downstream.history_limit = config.downstream.history_length


def find_pending_change_at_bottom_of_stack(
    copybot_skip_cls: list[str],
    commits_to_copy: list[str],
    pending_changes: dict[str, gerrit.GerritClInfo],
) -> tuple[str | None, int]:
    """Determine if there is a pending change at the beginning of the stack.

    If so, find the CL at the top of the pending stack.
    If not, or if the copybot-rebase hashtag is used, revert to the
      original default behavior of starting with ToT HEAD of the downstream
      and cherry-picking from upstream.
    * Note - this operates on the assumption that there is a single stack
      of CLs.  CLs in separate stacks beneath the lowest pending change are
      effectively ignored.  Additionally, any pending copybot-skip changes
      will cause the entire stack to be cherry-picked.

    Args:
        copybot_skip_cls: A list of CLs that should be skipped
        commits_to_copy: A stack of commits to go through
        pending_changes: Changes pending in downstream repo.

    Returns:
        * Pending revision at the beginning of the stack
        * CL count of pending changes
    """
    pending_rev = None
    cl_count = 0
    if not copybot_skip_cls:
        for rev in reversed(commits_to_copy):
            if rev not in pending_changes:
                break
            if any(
                tag in (REBASE_TAG, REWORD_TAG)
                for tag in pending_changes[rev].hashtags
            ):
                break
            pending_rev = rev
            cl_count += 1
    return pending_rev, cl_count


def checkout_downstream_repo(
    repo: gerrit.GitRepoInterface,
    downstream: copybot_argparser.DownstreamConfig,
    commits_to_copy: list[str],
    pending_changes: dict[str, gerrit.GerritClInfo],
    cl_count: int,
    pending_rev: str | None,
) -> None:
    """Checkout downstream repo to pending_rev or HEAD.

    If there are pending changes at the stack, checkout `pending_rev`.
    Otherwise, checkout HEAD of downstream repository.
    """
    if cl_count > 0:
        logger.info(
            "Found %d pending changes at the bottom of the stack.", cl_count
        )
        if cl_count == len(commits_to_copy):
            raise NothingToDo("All found changes are pending")
        logger.info("Checking out the top change: %s.", pending_rev)
        repo.fetch(
            downstream.url,
            pending_changes[str(pending_rev)].current_ref,
        )
        repo.checkout("FETCH_HEAD")
    else:
        assert downstream.head_sha is not None
        repo.checkout(downstream.head_sha)


def push_changes_to_downstream(
    repo: gerrit.GitRepoInterface,
    config: copybot_argparser.CopybotConfig,
    downstream: copybot_argparser.DownstreamConfig,
    skip_cq: bool,
) -> None:
    """Push changes to downstream location."""
    push_refspec = get_push_refspec(config, downstream.branch, skip_cq)
    if not config.dry_run and not config.downstream.is_local:
        try:
            repo.push(
                config.downstream.url,
                push_refspec,
                options=config.downstream.push_options,
            )
        except subprocess.CalledProcessError as e:
            raise gerrit.PushError(
                f"Failed to push to {config.downstream.url}"
            ) from e
    else:
        logger.info("Skip push due to dry/local run")


def should_preserve_pending_change(
    pending_changes: dict[str, gerrit.GerritClInfo], rev: str
) -> bool:
    """Return if pending change should be preserved.

    Preserved changes are cherry picked from the downstream gerrit. It allows
    for modifications by users and a mechanism in which copybot won't overwrite
    the desired content.
    """
    if rev in pending_changes and PRESERVE_TAG in pending_changes[rev].hashtags:
        logger.info(
            "Preserving pending change due to %s hashtag.",
            PRESERVE_TAG,
        )
        return True
    return False


def should_reword_pending_change(
    pending_changes: dict[str, gerrit.GerritClInfo], rev: str
) -> bool:
    """Return if pending change commit message should be reworded."""
    if rev in pending_changes and REWORD_TAG in pending_changes[rev].hashtags:
        logger.info(
            "Rewording commit message due to %s hashtag.",
            REWORD_TAG,
        )
        return True
    return False


def commit_with_conflicts(
    repo: gerrit.GitRepoInterface,
    config: copybot_argparser.CopybotConfig,
    patch_dir: str | "os.PathLike[str]",
    skipped_files_map: dict[str, list[str]],
    pending_changes: dict[str, gerrit.GerritClInfo],
    pending_change: bool,
    reword_pending_change: bool,
    filtered_rev: str | None,
    rev: str,
) -> bool:
    """Try to commit the pending change with a conflict.

    Returns:
        A boolean indicating if the commit was empty.
    """
    if pending_change:
        repo.fetch(config.downstream.url, pending_changes[rev].current_ref)
        try:
            repo.cherry_pick(rev="FETCH_HEAD", allow_conflict=True)
        except gerrit.EmptyCommitError:
            return True
    else:
        try:
            repo.cherry_pick(
                filtered_rev or rev,
                patch_dir=patch_dir,
                upstream_subtree=config.upstream.subtree,
                downstream_subtree=config.downstream.subtree,
                include_paths=config.downstream.include_paths,
                exclude_paths=config.drop_paths,
                allow_conflict=True,
            )
        except gerrit.EmptyCommitError:
            return True
    if not pending_change or reword_pending_change:
        change_id = (
            pending_changes.get(rev) or gerrit.GerritClInfo("", "", "")
        ).change_id
        rewrite_commit_message(
            repo,
            upstream_rev=rev,
            upstream=config.upstream,
            downstream=config.downstream,
            change_id=change_id or gerrit.generate_change_id(),
            skipped_files=skipped_files_map[rev],
            sign_off=config.add_signed_off_by,
            additional_pseudoheaders=[
                *config.downstream.add_pseudoheaders,
                "Commit: false",
            ],
        )
    return False


def cherry_pick_commits_to_downstream(
    repo: gerrit.GitRepoInterface,
    config: copybot_argparser.CopybotConfig,
    patch_dir: str | "os.PathLike[str]",
    skipped_files_map: dict[str, list[str]],
    commit_files_map: dict[str, list[str]],
    commits_to_copy: list[str],
    updated_commits_to_copy: list[str],
    pending_changes: dict[str, gerrit.GerritClInfo],
) -> tuple[list[str], list[str], list[str]]:
    """Cherry pick commits to downstream.

    Args:
        repo: gerrit.GitRepoInterface instance
        config: Copybot configuration object
        patch_dir: A temporary directory to use for storing patch files.
        skipped_files_map: A mapping of commit hashes to the files that should
        be skipped.
        commit_files_map: A mapping of commit hashes to the files that should be
        included.
        commits_to_copy: A list of the commit hashes to copy.
        updated_commits_to_copy: A list of updated commits_to_copy
        pending_changes: Changes pending in downstream repo.

    Returns:
        * A list of unapplied commits due to conflicts.
        * A list of unapplied commits as they were empty.
        * A list of unapplied commits due to merge conflicts.
    """
    conflicted_revs = []
    empty_revs = []
    skipped_revs = []

    for i, rev in enumerate(reversed(updated_commits_to_copy)):
        logger.info(
            "(%s/%s) Cherry-pick %s",
            i + 1,
            len(updated_commits_to_copy),
            rev,
        )
        pending_change = should_preserve_pending_change(pending_changes, rev)
        reword_pending_change = should_reword_pending_change(
            pending_changes, rev
        )
        filtered_rev = None
        if skipped_files_map[rev]:
            filtered_rev = repo.filter_commit(
                rev=rev, patch_dir=patch_dir, files=commit_files_map[rev]
            )
        try:
            if pending_change:
                repo.fetch(
                    config.downstream.url, pending_changes[rev].current_ref
                )
                repo.cherry_pick("FETCH_HEAD")
            else:
                repo.cherry_pick(
                    filtered_rev or rev,
                    patch_dir=patch_dir,
                    upstream_subtree=config.upstream.subtree,
                    downstream_subtree=config.downstream.subtree,
                    include_paths=config.downstream.include_paths,
                    exclude_paths=config.drop_paths,
                )
        except gerrit.EmptyCommitError:
            logger.warning("Skip cherry-pick due to empty commit")
            empty_revs.append(rev)
            continue
        except gerrit.MergeConflictError as e:
            logger.error("Merge conflict cherry-picking %s!", rev)
            if (
                config.merge_conflict_behavior
                is gerrit.MergeConflictBehavior.SKIP
            ):
                logger.warning("Skipping %s", rev)
                skipped_revs.append(rev)
                continue
            elif (
                config.merge_conflict_behavior
                is gerrit.MergeConflictBehavior.STOP
            ):
                logger.warning("Stopping at revision %s", rev)
                skipped_revs.extend(list(reversed(commits_to_copy))[i:])
                break
            elif (
                config.merge_conflict_behavior
                is gerrit.MergeConflictBehavior.ALLOW_CONFLICT
            ):
                logger.warning("Committing %s with conflicts", rev)

                was_commit_empty = commit_with_conflicts(
                    repo,
                    config,
                    patch_dir,
                    skipped_files_map,
                    pending_changes,
                    pending_change,
                    reword_pending_change,
                    filtered_rev,
                    rev,
                )
                if was_commit_empty:
                    logger.warning("Skip cherry-pick due to empty commit")
                    empty_revs.append(rev)
                else:
                    conflicted_revs.append(rev)
                continue
            raise gerrit.MergeConflictsError(commits=[rev]) from e
        if not pending_change or reword_pending_change:
            change_id = (
                pending_changes.get(rev) or gerrit.GerritClInfo("", "", "")
            ).change_id
            rewrite_commit_message(
                repo,
                upstream_rev=rev,
                upstream=config.upstream,
                downstream=config.downstream,
                change_id=change_id or gerrit.generate_change_id(),
                skipped_files=skipped_files_map[rev],
                sign_off=config.add_signed_off_by,
                additional_pseudoheaders=config.downstream.add_pseudoheaders,
            )
        current_change = repo.log(num=1, fmt="%H").stdout.strip()
        logger.info("Revision %s cherry-picked as %s", rev, current_change)
    return conflicted_revs, empty_revs, skipped_revs


def log_unapplied_empty_commits(
    repo: gerrit.GitRepoInterface, empty_revs: list[str]
) -> None:
    """Log warning commits that were not applied as they were empty."""
    emptylist = [
        repo.log(rev, fmt="%H %s", num=1).stdout.strip() for rev in empty_revs
    ]
    if emptylist:
        logger.warning(
            "The following commits were not applied because they were empty:"
        )
        for rev in emptylist:
            logger.warning("- %s", rev)


def log_unapplied_merge_conflicted_commits(
    repo: gerrit.GitRepoInterface, skipped_revs: list[str]
) -> None:
    """Log error commits that were not applied due to merge conflict."""
    revlist = [
        repo.log(rev, fmt="%H %s", num=1).stdout.strip() for rev in skipped_revs
    ]
    if revlist:
        logger.error(
            "The following commits were not applied due to merge conflict:"
        )
        for rev in revlist:
            logger.error("- %s", rev)
        raise gerrit.MergeConflictsError(commits=skipped_revs)


def log_unapplied_commits_with_conflicts(
    repo: gerrit.GitRepoInterface, conflicted_revs: list[str]
) -> None:
    """Log error commits that were uploaded with conflicts."""
    conflictedlist = [
        repo.log(rev, fmt="%H %s", num=1).stdout.strip()
        for rev in conflicted_revs
    ]
    if conflictedlist:
        logger.error("The following commits were uploaded with conflicts:")
        for rev in conflictedlist:
            logger.error("- %s", rev)
        raise gerrit.MergeConflictsError(commits=conflicted_revs)


def log_unapplied_commits(
    repo: gerrit.GitRepoInterface,
    empty_revs: list[str],
    skipped_revs: list[str],
    conflicted_revs: list[str],
) -> None:
    """Log all unapplied commits."""
    log_unapplied_empty_commits(repo, empty_revs)
    log_unapplied_merge_conflicted_commits(repo, skipped_revs)
    log_unapplied_commits_with_conflicts(repo, conflicted_revs)


def run_copybot(
    GitRepoCls: type[gerrit.GitRepoInterface],
    GerritCls: type[gerrit.GerritInterface],
    config: copybot_argparser.CopybotConfig,
    git_dir: str | "os.PathLike[str]",
    patch_dir: str | "os.PathLike[str]",
) -> None:
    """Run copybot.

    Args:
        GitRepoCls: A class implementing GitRepoInterface interface.
        GerritCls: A class implementing GerritInterface interface.
        config: The parsed command line arguments.
        git_dir: A temporary or local directory to use for Git operations.
        patch_dir: A temporary directory to use for storing patch files.
    """
    if config.downstream.is_local:
        git_dir = config.downstream.url

    pending_changes: dict[str, gerrit.GerritClInfo] = {}
    abandoned_changes: dict[str, gerrit.GerritClInfo] = {}
    gerrit_inst: gerrit.GerritInterface | None = None
    if (m := is_server_gob(str(config.downstream.url))) is not None:
        downstream_gob_host = m.group(1)
        downstream_project = m.group(2)

        gerrit_inst = GerritCls(
            f"{downstream_gob_host}-review.googlesource.com"
        )
        pending_changes, abandoned_changes = gerrit_inst.find_pending_changes(
            project=downstream_project,
            branch=config.downstream.branch,
            hashtags=[config.topic],
            subtree=config.downstream.subtree,
            exclude_paths=config.drop_paths,
        )
        logger.info(
            "Found %s pending and %s abandoned changes already on Gerrit",
            len(pending_changes),
            len(abandoned_changes),
        )
    repo = GitRepoCls(git_dir)
    config.upstream.head_sha = fetch_repo_head_sha(
        repo, config.upstream.url, config.upstream.branch
    )
    config.downstream.head_sha = fetch_repo_head_sha(
        repo, config.downstream.url, config.downstream.branch
    )
    config.upstream.history_length = fetch_history_length(
        repo, config.upstream, "Upstream"
    )
    config.downstream.history_length = fetch_history_length(
        repo, config.upstream, "Downstream"
    )

    verify_repos_share_history_to_adjust_limits(repo, config, pending_changes)

    commit_files_map: dict[str, list[str]] = {}
    skipped_files_map: dict[str, list[str]] = {}

    (
        commits_to_copy,
        commit_files_map,
        skipped_files_map,
        copybot_skip_cls,
        pending_to_submit,
    ) = find_commits_to_copy(
        repo,
        config.upstream,
        config.downstream,
        exclude_file_patterns=config.drop_paths,
        filter_file_patterns=config.filter_file_patterns,
        pending_changes=pending_changes,
        abandoned_changes=abandoned_changes,
        skip_copybot_job_names=config.skip_job_name,
        skip_author_emails=config.skip_author_email,
    )

    if not commits_to_copy:
        raise NothingToDo

    if not config.filter_changes:
        config.downstream.subtree = ""
        config.upstream.subtree = ""

    if 0 < config.downstream.limit < len(commits_to_copy):
        logger.warning(
            "Limiting commits to copy from %s to %s",
            len(commits_to_copy),
            config.downstream.limit,
        )
        commits_to_copy = commits_to_copy[-config.downstream.limit :]

    pending_rev, cl_count = find_pending_change_at_bottom_of_stack(
        copybot_skip_cls, commits_to_copy, pending_changes
    )

    checkout_downstream_repo(
        repo,
        config.downstream,
        commits_to_copy,
        pending_changes,
        cl_count,
        pending_rev,
    )

    updated_commits_to_copy = commits_to_copy[
        : (len(commits_to_copy) - cl_count)
    ]
    (
        conflicted_revs,
        empty_revs,
        skipped_revs,
    ) = cherry_pick_commits_to_downstream(
        repo,
        config,
        patch_dir,
        skipped_files_map,
        commit_files_map,
        commits_to_copy,
        updated_commits_to_copy,
        pending_changes,
    )

    if repo.rev_parse() == config.downstream.head_sha:
        logger.info("Nothing to push!")
    else:
        skip_cq = any(conflicted_revs) or pending_to_submit
        push_changes_to_downstream(repo, config, config.downstream, skip_cq)

    log_unapplied_commits(
        repo,
        empty_revs,
        skipped_revs,
        conflicted_revs,
    )


def write_json_error(path: pathlib.Path, err: Exception | None) -> None:
    """Write out the JSON-serialized protobuf from an exception.

    Args:
        path: The Path to write to.
        err: The exception to serialize.
    """
    err_json: dict[str, Any] = {}
    if err:
        if isinstance(err, gerrit.CopybotFatalError):
            err_json["failure_reason"] = err.enum_name
            if err.commits:
                err_json["merge_conflicts"] = [{"hash": x} for x in err.commits]
        else:
            err_json["failure_reason"] = gerrit.CopybotFatalError.enum_name
    logger.debug("JSON response: %s", err_json)
    path.write_text(json.dumps(err_json))


def main(argv: list[str] | None = None) -> None:
    config = copybot_argparser.parse_copybot_config(argv)
    logging.basicConfig(
        format="%(asctime)s %(levelname)s: %(message)s",
        level=logging.INFO,
    )

    if 0 < config.downstream.history_limit < config.upstream.history_limit:
        logger.warning(
            "Using a lower downstream limit than upstream limit may cause"
            " previously downstreamed changes to be chosen again."
        )

    err = None
    try:
        with (
            tempfile.TemporaryDirectory(".copybot") as git_dir,
            tempfile.TemporaryDirectory("_patches") as patch_dir,
        ):
            run_copybot(
                gerrit.GitRepo, gerrit.Gerrit, config, git_dir, patch_dir
            )
    except NothingToDo as e:
        logger.info("%s. Nothing to do!", str(e))
    except Exception as e:
        err = e
        raise
    finally:
        if config.json_out:
            write_json_error(config.json_out, err)


if __name__ == "__main__":
    main()
