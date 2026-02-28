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
# wheel: <
#   name: "infra/python/wheels/pyyaml-py3"
#   version: "version:6.0.1"
# >
# wheel: <
#   name: "infra/python/wheels/protobuf-py3"
#   version: "version:6.32.1"
# >
# wheel: <
#   name: "infra/python/wheels/types-protobuf-py3"
#   version: "version:6.32.1.20251105"
# >
# [VPYTHON:END]

from __future__ import annotations

import ast
from collections.abc import Iterable
import configparser
import contextlib
import itertools
import json
import logging
import os
import pathlib
import re
import subprocess
import tempfile
from typing import Any, Final, Union

import copybot_argparser
import gerrit

# pylint: disable=import-error
from google.protobuf import text_format

# pylint: enable=import-error
import kernel_cl_dispatch

# pylint: disable=no-name-in-module
from proto.copybot_job_pb2 import CopybotJob
from proto.copybot_job_pb2 import CopybotJobs


# pylint: enable=no-name-in-module


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


def fetch_upstream_change_ids(
    repo: gerrit.GitRepoInterface, commit_hashes: list[str], limit: int = -1
) -> dict[str, str]:
    """Fetch a mapping of commit's Change-Id's to their hashes."""
    iterable = (
        commit_hashes if limit < 0 else itertools.islice(commit_hashes, limit)
    )
    return {
        change_id: rev
        for rev in iterable
        if (change_id := gerrit.get_change_id(repo.get_commit_message(rev)))
    }


def find_first_unmerged_rev(
    config: copybot_argparser.CopybotConfig,
    upstream: copybot_argparser.UpstreamConfig,
    downstream: copybot_argparser.DownstreamConfig,
    pending_changes: dict[str, gerrit.GerritClInfo] | None = None,
) -> tuple[str, str, int]:
    """Find the first unmerged revision in a Git repo.

    Args:
        config: The parsed command line arguments.
        upstream: Configuration for upstream location.
        downstream: Configuration for downstream location.
        pending_changes: Changes pending in downstream repo.

    Returns:
        Three values,
            1. A commit hash of the last merged revision by CopyBot, or the
                first common commit hash in both logs.
            2. The downstream commit hash
            3. The number of CLs which are eligible to be downstreamed.

    Raises:
        ValueError: No common history could be found.
    """
    upstream_hashes = upstream.repo.log_hashes(
        revision_range=upstream.head_sha,
        subtree=upstream.subtree,
        exclude_file_patterns=config.exclude_file_patterns,
        num=upstream.history_length,
    )
    downstream_hashes = downstream.repo.log_hashes(
        revision_range=downstream.head_sha,
        subtree=downstream.subtree,
        exclude_file_patterns=config.exclude_file_patterns,
        num=downstream.history_length,
    )

    counter = 0
    for rev in downstream_hashes:
        logger.info("Checking downstream hash: %s", rev)
        commit_message = downstream.repo.get_commit_message(rev)
        origin_revid = gerrit.get_origin_rev_id(commit_message)
        if rev in upstream_hashes or (
            origin_revid and origin_revid in upstream_hashes
        ):
            return (
                origin_revid or rev,
                rev,
                upstream_hashes.index(origin_revid or rev),
            )

    return_counter = 0
    return_rev = None
    if pending_changes:
        for rev in pending_changes:
            if rev in upstream_hashes:
                counter = upstream_hashes.index(rev)
                if counter < return_counter:
                    return_counter = counter
                    return_rev = rev
        if return_counter and return_rev:
            return rev, rev, counter

    raise ValueError(
        f"Downstream ({downstream}) has no GitOrigin-RevId commits, and "
        f"upstream ({upstream}) and downstream share no common history."
    )


def find_last_merged_rev(
    config: copybot_argparser.CopybotConfig,
    upstream: copybot_argparser.UpstreamConfig,
    downstream: copybot_argparser.DownstreamConfig,
    pending_changes: dict[str, gerrit.GerritClInfo] | None = None,
) -> tuple[str, str, int]:
    """Find the last merged revision in a Git repo.

    Args:
        config: The parsed command line arguments.
        upstream: Configuration for upstream location.
        downstream: Configuration for downstream location.
        pending_changes: Changes pending in downstream repo.

    Returns:
        Three values,
            1. A commit hash of the last merged revision by CopyBot, or the
                first common commit hash in both logs.
            2. The downstream commit hash
            3. The number of CLs which are eligible to be downstreamed.

    Raises:
        ValueError: No common history could be found.
    """
    upstream_hashes = upstream.repo.log_hashes(
        revision_range=upstream.head_sha,
        subtree=upstream.subtree,
        exclude_file_patterns=config.exclude_file_patterns,
        num=upstream.history_length,
    )
    upstream_change_ids = fetch_upstream_change_ids(
        upstream.repo, upstream_hashes, upstream.history_limit
    )
    downstream_hashes = downstream.repo.log_hashes(
        revision_range=downstream.head_sha,
        subtree=downstream.subtree,
        exclude_file_patterns=config.exclude_file_patterns,
        num=downstream.history_length,
    )

    include_change_id = (
        are_repos_related(upstream, downstream) and not config.ignore_change_id
    )

    for rev in downstream_hashes:
        commit_message = downstream.repo.get_commit_message(rev)
        origin_revid = gerrit.get_origin_rev_id(commit_message)
        change_id = gerrit.get_change_id(commit_message)

        if (
            rev in upstream_hashes
            or origin_revid
            or (change_id and include_change_id)
        ):
            if origin_revid in upstream_hashes or rev in upstream_hashes:
                counter = upstream_hashes.index(origin_revid or rev)
            elif include_change_id and change_id in upstream_change_ids:
                origin_revid = upstream_change_ids[change_id]
                counter = upstream_hashes.index(origin_revid or rev)
            else:
                continue
            return origin_revid or rev, rev, counter
    return_counter = 0
    return_rev = None
    if pending_changes:
        for rev in pending_changes:
            if rev in upstream_hashes:
                counter = upstream_hashes.index(rev)
                if counter < return_counter:
                    return_counter = counter
                    return_rev = rev
        if return_counter and return_rev:
            return rev, rev, counter

    raise ValueError(
        f"Downstream ({downstream}) has no GitOrigin-RevId commits, and "
        f"upstream ({upstream}) and downstream share no common history."
    )


def get_downstreamed_list(
    config: copybot_argparser.CopybotConfig,
    downstream: copybot_argparser.DownstreamConfig,
    upstream_change_ids: dict[str, str],
    include_change_id: bool = False,
) -> list[str]:
    """Find the last merged revision in a Git repo.

    Args:
        config: The parsed command line arguments.
        downstream: Configuration for downstream location.
        upstream_change_ids: dictionary of upstream Change-Id's and their
            associated upstream commit hash.
        include_change_id: Bool specifying whether or not to
            consider Change-Ids

    Returns:
        The set of upstream commit hashes that have already been downstreamed.
    """
    downstream_hashes = downstream.repo.log_hashes(
        revision_range=downstream.head_sha,
        subtree=downstream.subtree,
        exclude_file_patterns=config.exclude_file_patterns,
        num=downstream.history_length,
    )
    downstreamed_revs = list(downstream_hashes)

    for counter, rev in enumerate(downstream_hashes):
        if counter > downstream.history_limit > 0:
            break

        commit_message = downstream.repo.get_commit_message(rev)
        origin_revid = gerrit.get_origin_rev_id(commit_message)
        change_id = gerrit.get_change_id(commit_message)

        if origin_revid:
            downstreamed_revs.append(origin_revid)
        if change_id and include_change_id and change_id in upstream_change_ids:
            downstreamed_revs.append(upstream_change_ids[change_id])
    return downstreamed_revs


def is_copybot_job_skipped(
    config: copybot_argparser.CopybotConfig,
    rev: str,
) -> bool:
    commit_message = config.upstream.repo.get_commit_message(rev)
    (
        pseudoheaders,
        commit_message,
    ) = gerrit.Pseudoheaders.from_commit_message(commit_message)
    job_name = pseudoheaders.get("Copybot-Job-Name")
    skipped = bool(config.skip_job_names and job_name in config.skip_job_names)

    if skipped:
        logger.info(
            "Skip %s due to Copybot-Job-Name: %s",
            rev,
            job_name,
        )
    return skipped


def find_commits_to_copy(
    config: copybot_argparser.CopybotConfig,
    upstream: copybot_argparser.UpstreamConfig,
    downstream: copybot_argparser.DownstreamConfig,
    pending_changes: dict[str, gerrit.GerritClInfo] | None = None,
    abandoned_changes: dict[str, gerrit.GerritClInfo] | None = None,
    include_change_id: bool = False,
) -> tuple[
    list[str],
    dict[str, list[str]],
    dict[str, list[str]],
    list[str],
    bool,
    list[str],
]:
    """Find the commits to copy to downstream.

    Args:
        config: The parsed command line arguments.
        upstream: Configuration for upstream location.
        downstream: Configuration for downstream location.
        pending_changes: Changes pending in downstream repo.
        abandoned_changes: Changes abandoned in downstream repo.
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
        * A list of CL's which have touched owners files

    Raises:
        ValueError: If the provided last merged commit hash does not
           exist in upstream commit history.
    """
    commits_to_copy: list[str] = []
    commit_files_map = {}
    skipped_files_map = {}
    owners_cls = []
    copybot_skip_cls = []

    upstream_hashes = upstream.repo.log_hashes(
        revision_range=upstream.head_sha,
        subtree=upstream.subtree,
        exclude_file_patterns=config.exclude_file_patterns,
        num=upstream.history_length,
    )
    upstream_change_ids = fetch_upstream_change_ids(
        upstream.repo, upstream_hashes, upstream.history_limit
    )
    downstreamed_revs = get_downstreamed_list(
        config=config,
        downstream=downstream,
        upstream_change_ids=upstream_change_ids,
        include_change_id=include_change_id,
    )

    counter = 0
    skip_cq_from_parse_logic = False
    reverse_search = upstream.history_limit == 0 and config.first_unmerged
    if reverse_search:
        upstream_hashes.reverse()
        first_index = upstream_hashes.index(upstream.history_starts_with)
        del upstream_hashes[:first_index]

    for rev in upstream_hashes:
        # Early exit if limit reached to avoid inadvertent continuation
        if counter > upstream.history_limit > 0:
            logger.info("Hit upstream limit of %s", upstream.history_limit)
            break

        if 0 < downstream.limit < len(commits_to_copy) and reverse_search:
            logger.warning(
                "Limiting commits to copy from %s to %s",
                len(commits_to_copy),
                downstream.limit,
            )
            commits_to_copy.reverse()
            break
        if is_copybot_job_skipped(config, rev):
            continue

        if config.skip_author_emails:
            author_email = upstream.repo.get_author_email(rev=rev)
            if author_email in config.skip_author_emails:
                logger.info(
                    "Skip %s due to author %s",
                    rev,
                    author_email,
                )
                continue

        # Increment counter after filtering Copybot-Job-Name CLs to treat
        # them as if they don't belong to the target repo.
        counter += 1

        commit_subject = upstream.repo.get_subject(rev=rev)
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

        if config.enable_kernel_cl_dispatcher:
            if not kernel_cl_dispatch.should_rev_be_dispatched_to_location(
                upstream, downstream, rev
            ):
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

        commit_files = upstream.repo.commit_file_list(rev)
        filtered_commit_files = []

        for path in commit_files:
            file_name = os.path.basename(path)
            if not any(
                re.fullmatch(p, path) for p in config.filter_file_patterns or []
            ):
                if file_name == "OWNERS" or file_name.startswith("OWNERS."):
                    logger.info(
                        "OWNERS file was touched in %s",
                        rev,
                    )
                    owners_cls.append(rev)
                filtered_commit_files.append(path)

        if not filtered_commit_files:
            if not upstream.repo.is_merge_commit(rev):
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

        if downstream.include_paths:
            filtered_commit_files = []
            for path in commit_files:
                filtered_path = pathlib.Path(path)
                if upstream.subtree:
                    filtered_path = pathlib.Path(path).relative_to(
                        upstream.subtree
                    )
                if not any(
                    re.fullmatch(p, str(filtered_path))
                    for p in downstream.include_paths or []
                ):
                    filtered_commit_files.append(path)
                    break

            if not filtered_commit_files:
                logger.info(
                    "Skip commit %s due to empty file list after filtering "
                    "include paths(before filtering was %r)",
                    rev,
                    commit_files,
                )
                continue
        if pending_changes and rev in pending_changes:
            skip_cq_from_parse_logic = True
        commits_to_copy.append(rev)

    return (
        commits_to_copy,
        commit_files_map,
        skipped_files_map,
        copybot_skip_cls,
        skip_cq_from_parse_logic,
        owners_cls,
    )


def rewrite_commit_message(
    upstream_rev: str,
    upstream: copybot_argparser.UpstreamConfig,
    downstream: copybot_argparser.DownstreamConfig,
    change_id: str,
    skipped_files=(),
    sign_off: bool = False,
    additional_pseudoheaders: Iterable[str] = (),
    ignore_change_id: bool = False,
) -> tuple[str, str]:
    """Reword the commit at HEAD with appropriate metadata.

    Args:
        upstream_rev: The upstream commit hash corresponding to this commit.
        upstream: Configuration for upstream location.
        downstream: Configuration for downstream location.
        change_id: The Change-Id to add to the commit.
        skipped_files: The list of files skipped.
        sign_off: True if Signed-off-by should be added to the commit message.
        additional_pseudoheaders: Psuedoheaders to be added to the commit
            message.
        ignore_change_id: If true, will override all other change-id behavior.
            change_id's will not be maintained across cherry-picks.

    Returns:
        * Reworded commit message
        * Updated author
    """
    commit_message = downstream.repo.get_commit_message()
    if downstream.prepend_subject:
        commit_message = downstream.prepend_subject + commit_message
    if downstream.insert_into_msg:
        tmp_commit_msg = commit_message.splitlines()
        for line, msg in sorted(
            downstream.insert_into_msg.items(), reverse=True
        ):
            tmp_commit_msg.insert(line, msg)
        commit_message = "\n".join(tmp_commit_msg)

    if are_repos_related(upstream, downstream) and not ignore_change_id:
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
    orig_author = upstream.repo.get_author_email(rev=upstream_rev)
    author, sym, domain = orig_author.rpartition("@")
    orig_author_name = upstream.repo.get_author_name(rev=upstream_rev)
    updated_author = (
        orig_author_name + "<" + author + sym + domain + "-copybot-pick" + ">"
    )
    downstream.repo.reword(
        commit_message, sign_off=sign_off, update_author=updated_author
    )
    return commit_message, updated_author


def get_push_refspec(
    config: copybot_argparser.CopybotConfig,
    downstream: copybot_argparser.DownstreamConfig,
    skip_cq: bool,
) -> str:
    """Generate a push refspec for Gerrit.

    Args:
        config: The parsed command line arguments.
        downstream: Configuration for downstream location.
        skip_cq: Whether the copied CL stack should not be submitted to CQ.

    Returns:
        A push refspec as a string.
    """
    push_options = ["ready"]

    def _add_push_option(key, value):
        for option in value.split(","):
            push_options.append(f"{key}={option}")

    for label in downstream.labels:
        if skip_cq and (label in ["Bot-Commit+1", "Commit-Queue+2"]):
            logger.info("Skipping CQ")
            continue
        _add_push_option("l", label)

    for cc in downstream.ccs:
        _add_push_option("cc", cc)

    for reviewer in downstream.reviewers:
        _add_push_option("r", reviewer)

    for hashtag in [config.topic, *downstream.hashtags]:
        _add_push_option("t", hashtag)

    return f"HEAD:refs/for/{downstream.branch}%{','.join(push_options)}"


def is_server_gob(url: str) -> re.Match[str] | None:
    """Check if the server is a Google-controlled Git-on-Borg host."""
    return re.fullmatch(
        r"https://(chromium|chrome-internal|android|partner-android)"
        r"(?:-review)?\.googlesource\.com/(.*)",
        url,
    )


def fetch_repo_head_sha(
    repo: gerrit.GitRepoInterface, url: str, branch: str, subtree: str = ""
) -> str:
    """Fetch HEAD sha in the repository of a given remote and branch."""
    try:
        return repo.fetch(url, branch, subtree)
    except subprocess.CalledProcessError as e:
        raise gerrit.FetchError(
            f"Failed to fetch branch {branch} from {url}"
        ) from e


def fetch_history_length(
    target: copybot_argparser.TargetConfig,
    location: str,
) -> int:
    """Fetch the history length from where it starts to HEAD."""
    if target.history_starts_with:
        history_length = (
            target.repo.get_cl_count(
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
    config: copybot_argparser.CopybotConfig,
    downstream: copybot_argparser.DownstreamConfig,
    pending_changes: dict[str, gerrit.GerritClInfo],
) -> None:
    """Verify history and adjust limits if there are more CLs downstream."""

    num_cls_to_downstream = 0
    last_related_downstream_rev = ""
    last_related_rev = ""

    try:
        if config.first_unmerged:
            (
                last_related_rev,
                last_related_downstream_rev,
                num_cls_to_downstream,
            ) = find_first_unmerged_rev(
                config,
                config.upstream,
                downstream,
                pending_changes=pending_changes,
            )
        else:
            (
                last_related_rev,
                last_related_downstream_rev,
                num_cls_to_downstream,
            ) = find_last_merged_rev(
                config,
                config.upstream,
                downstream,
                pending_changes=pending_changes,
            )
    except ValueError:
        if (
            config.upstream.history_starts_with
            and downstream.history_starts_with
        ):
            logger.warning(
                "Could not find relationship in repository histories,"
                " starting from config 'history_starts_with"
            )
            upstream_hashes = config.upstream.repo.log_hashes(
                revision_range=config.upstream.head_sha,
                subtree=config.upstream.subtree,
                exclude_file_patterns=config.exclude_file_patterns,
                num=config.upstream.history_length,
            )
            last_related_rev = config.upstream.history_starts_with
            last_related_downstream_rev = downstream.history_starts_with
            num_cls_to_downstream = upstream_hashes.index(
                config.upstream.history_starts_with
            )
        else:
            raise

    logger.info("Last related revision: %s", last_related_rev)
    if last_related_rev in pending_changes:
        logger.info("Last related revision from pending changes!")
    else:
        config.upstream.history_starts_with = last_related_rev
        downstream.history_starts_with = last_related_downstream_rev

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
        downstream.history_limit = downstream.history_length


def find_pending_change_at_bottom_of_stack(
    copybot_skip_cls: list[str],
    commits_to_copy: list[str],
    pending_changes: dict[str, gerrit.GerritClInfo],
    config: copybot_argparser.CopybotConfig,
    downstream: copybot_argparser.DownstreamConfig,
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
        config: Copybot configuration object
        downstream: Configuration for downstream location.

    Returns:
        * Pending revision at the beginning of the stack
        * CL count of pending changes
    """
    pending_rev = None
    cl_count = 0
    downstream_hashes = list(
        downstream.repo.log_hashes(
            revision_range=downstream.head_sha,
            subtree=downstream.subtree,
            exclude_file_patterns=config.exclude_file_patterns,
            num=downstream.history_length,
        )
    )
    if not copybot_skip_cls:
        for rev in reversed(commits_to_copy):
            if rev not in pending_changes and rev not in downstream_hashes:
                logging.info("Breaking on %s", rev)
                break
            if any(
                tag in (REBASE_TAG, REWORD_TAG)
                for tag in pending_changes[rev].hashtags
            ):
                logging.info("Breaking due to rebase/reword tag on %s", rev)
                break
            pending_rev = rev
            cl_count += 1
    return pending_rev, cl_count


def checkout_downstream_repo(
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
        downstream.repo.fetch(
            downstream.url,
            pending_changes[str(pending_rev)].current_ref,
        )
        downstream.repo.checkout("FETCH_HEAD")
    else:
        assert downstream.head_sha is not None
        downstream.repo.checkout(downstream.head_sha)


def push_changes_to_downstream(
    config: copybot_argparser.CopybotConfig,
    downstream: copybot_argparser.DownstreamConfig,
    skip_cq: bool,
    gerrit_inst: gerrit.GerritInterface | None = None,
    pending_changes: dict[str, gerrit.GerritClInfo] | None = None,
) -> None:
    """Push changes to downstream location."""
    push_refspec = get_push_refspec(config, downstream, skip_cq)
    if not pending_changes:
        pending_changes = {}
    if not config.dry_run and not downstream.is_local:
        try:
            downstream.repo.push(
                downstream.url,
                push_refspec,
                options=downstream.push_options,
            )
            for change in pending_changes.values():
                if REBASE_TAG in change.hashtags:
                    if gerrit_inst:
                        gerrit_inst.adjust_hashtags(
                            change.change_id, remove_hashtags=[REBASE_TAG]
                        )
        except subprocess.CalledProcessError as e:
            raise gerrit.PushError(f"Failed to push to {downstream.url}") from e
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
    config: copybot_argparser.CopybotConfig,
    downstream: copybot_argparser.DownstreamConfig,
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
        downstream.repo.fetch(downstream.url, pending_changes[rev].current_ref)
        try:
            downstream.repo.cherry_pick(rev="FETCH_HEAD", allow_conflict=True)
        except gerrit.EmptyCommitError:
            return True
    else:
        try:
            downstream.repo.cherry_pick(
                filtered_rev or rev,
                patch_dir=patch_dir,
                upstream_subtree=config.upstream.subtree,
                downstream_subtree=downstream.subtree,
                include_paths=downstream.include_paths,
                exclude_paths=config.exclude_file_patterns,
                allow_conflict=True,
            )
        except gerrit.EmptyCommitError:
            return True
    if not pending_change or reword_pending_change:
        change_id = (
            pending_changes.get(rev) or gerrit.GerritClInfo("", "", "")
        ).change_id
        rewrite_commit_message(
            upstream_rev=rev,
            upstream=config.upstream,
            downstream=downstream,
            change_id=change_id or gerrit.generate_change_id(),
            skipped_files=skipped_files_map[rev],
            sign_off=config.add_signed_off_by,
            additional_pseudoheaders=[
                *config.add_pseudoheaders,
                "Commit: false",
            ],
            ignore_change_id=config.ignore_change_id,
        )
    return False


def cherry_pick_commits_to_downstream(
    config: copybot_argparser.CopybotConfig,
    downstream: copybot_argparser.DownstreamConfig,
    patch_dir: str | "os.PathLike[str]",
    skipped_files_map: dict[str, list[str]],
    commit_files_map: dict[str, list[str]],
    commits_to_copy: list[str],
    updated_commits_to_copy: list[str],
    pending_changes: dict[str, gerrit.GerritClInfo],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Cherry pick commits to downstream.

    Args:
        config: Copybot configuration object
        downstream: Configuration for downstream location.
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
        * A list of applied commits.
    """
    conflicted_revs = []
    empty_revs = []
    skipped_revs = []
    applied_cls = []
    # Create directory if it doesn't exist
    downstream_path = pathlib.Path(downstream.repo.git_dir) / downstream.subtree
    if downstream.subtree and not downstream_path.is_file():
        os.makedirs(
            downstream_path,
            exist_ok=True,
        )
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
            filtered_rev = downstream.repo.filter_commit(
                rev=rev, patch_dir=patch_dir, files=commit_files_map[rev]
            )
        try:
            if pending_change:
                logger.warning(
                    "Stopping at revision %s due to copybot-preserve tag",
                    rev,
                )
                skipped_revs.extend(list(reversed(commits_to_copy))[i:])
                break
            else:
                downstream.repo.cherry_pick(
                    filtered_rev or rev,
                    patch_dir=patch_dir,
                    upstream_subtree=config.upstream.subtree,
                    downstream_subtree=downstream.subtree,
                    include_paths=downstream.include_paths,
                    exclude_paths=config.exclude_file_patterns,
                )
        except gerrit.EmptyCommitError:
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
                    config,
                    downstream,
                    patch_dir,
                    skipped_files_map,
                    pending_changes,
                    pending_change,
                    reword_pending_change,
                    filtered_rev,
                    rev,
                )
                if was_commit_empty:
                    logger.warning("Applied empty commit")
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
                upstream_rev=rev,
                upstream=config.upstream,
                downstream=downstream,
                change_id=change_id or gerrit.generate_change_id(),
                skipped_files=skipped_files_map[rev],
                sign_off=config.add_signed_off_by,
                additional_pseudoheaders=config.add_pseudoheaders,
                ignore_change_id=config.ignore_change_id,
            )
        current_change = downstream.repo.log(num=1, fmt="%H")
        logger.info("Revision %s cherry-picked as %s", rev, current_change)
        applied_cls.append(rev)
    return conflicted_revs, empty_revs, skipped_revs, applied_cls


def log_empty_commits(
    repo: gerrit.GitRepoInterface, empty_revs: list[str]
) -> None:
    """Log warning commits that were not applied as they were empty."""
    emptylist = [repo.log(rev, fmt="%H %s", num=1) for rev in empty_revs]
    if emptylist:
        logger.warning(
            "The following commits were applied but they were empty:"
        )
        for rev in emptylist:
            logger.warning("- %s", rev)


def log_unapplied_merge_conflicted_commits(
    repo: gerrit.GitRepoInterface, skipped_revs: list[str]
) -> None:
    """Log error commits that were not applied due to merge conflict."""
    revlist = [repo.log(rev, fmt="%H %s", num=1) for rev in skipped_revs]
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
        repo.log(rev, fmt="%H %s", num=1) for rev in conflicted_revs
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
    log_empty_commits(repo, empty_revs)
    log_unapplied_merge_conflicted_commits(repo, skipped_revs)
    log_unapplied_commits_with_conflicts(repo, conflicted_revs)


def fetch_upstream_target_head_from_remote(
    config: copybot_argparser.CopybotConfig,
) -> None:
    """Git fetch copybot upstream sources."""
    # Fetch upstream
    config.upstream.repo.add_remote(
        config.upstream.url, config.upstream.remote_name
    )
    config.upstream.head_sha = fetch_repo_head_sha(
        config.upstream.repo,
        config.upstream.remote_name,
        config.upstream.branch,
        config.upstream.subtree,
    )


def fetch_downstream_target_head_from_remote(
    config: copybot_argparser.CopybotConfig,
    downstream: copybot_argparser.DownstreamConfig,
) -> None:
    """Fetch config targets head based on the given remote url & branch."""
    # Fetch downstream
    downstream.repo.add_remote(downstream.url, downstream.remote_name)
    downstream.head_sha = fetch_repo_head_sha(
        downstream.repo,
        downstream.remote_name,
        downstream.branch,
        downstream.subtree,
    )
    # Fetch upstream contents in downstream repository
    downstream.repo.add_remote(
        str(config.upstream.repo.git_dir), config.upstream.remote_name
    )
    fetch_repo_head_sha(
        downstream.repo,
        config.upstream.remote_name,
        f"{config.upstream.remote_name}/{config.upstream.branch}",
    )


def upload_cl(
    config: copybot_argparser.CopybotConfig,
    url: str,
    commit_msg: str,
    paths: list[Union[str, "os.PathLike[str]"]],
    hashtags: list[str],
    config_repo: gerrit.GitRepo | None,
):
    if config_repo is None:
        config_repo = gerrit.GitRepo(pathlib.Path(__file__).resolve().parent)
    try:
        config_repo.add(paths)
        config_repo.commit(commit_msg)
    except subprocess.CalledProcessError as e:
        logger.warning("Could not commit target files at: %s", paths)
        raise gerrit.MergeConflictError() from e

    push_changes_to_downstream(
        config,
        copybot_argparser.DownstreamConfig(
            labels=["Verified+1", "Bot-Commit+1", "Commit-Queue+2"],
            reviewers=[],
            ccs=[],
            push_options=["uploadvalidator~skip", "nokeycheck"],
            hashtags=hashtags,
            prepend_subject="",
            insert_into_msg={},
            keep_pseudoheaders=[],
            limit=0,
            history_limit=0,
            include_paths=[],
            history_starts_with="",
            url=url,
            branch="main",
            subtree="",
            is_local=False,
            head_sha=None,
            history_length=0,
            repo=config_repo,
            remote_name="copybot",
            cl_dispatcher_history_starts_with=(""),
        ),
        False,
    )


def upload_updated_config(
    config: copybot_argparser.CopybotConfig,
    downstream: copybot_argparser.DownstreamConfig | None = None,
    config_repo: gerrit.GitRepo | None = None,
) -> None:
    try:
        upload_cl(
            config=config,
            url="https://chromium.googlesource.com/copybot",
            commit_msg=(
                "copybot: Update Config Files\n\n"
                "Auto generated CL by copybot.\n"
                "Update up/downstream history starts with hashes\n\n"
                "BUG=None\nTEST=CQ"
            ),
            paths=[config.config_file_path],
            hashtags=["copybot-config-update"],
            config_repo=config_repo,
        )
    except gerrit.MergeConflictError as e:
        logging.warning("Could not update config: %s", e)
        commits = [config.upstream.history_starts_with]
        if downstream:
            commits.append(downstream.history_starts_with)
        raise gerrit.MergeConflictsError(commits=commits)


def run_copybot(
    GerritCls: type[gerrit.GerritInterface],
    config: copybot_argparser.CopybotConfig,
    patch_dir: str | "os.PathLike[str]",
) -> None:
    """Run copybot.

    Args:
        GerritCls: A class implementing GerritInterface interface.
        config: The parsed command line arguments.
        patch_dir: A temporary directory to use for storing patch files.
    """
    pending_changes: dict[str, gerrit.GerritClInfo] = {}
    abandoned_changes: dict[str, gerrit.GerritClInfo] = {}
    gerrit_inst: gerrit.GerritInterface | None = None

    fetch_upstream_target_head_from_remote(config)
    config.upstream.history_length = fetch_history_length(
        config.upstream, "Upstream"
    )

    for downstream in config.downstreams:
        logger.info("Processing downstream %s", str(downstream))

        if (m := is_server_gob(str(downstream.url))) is not None:
            downstream_gob_host = m.group(1)
            downstream_project = m.group(2)

            gerrit_inst = GerritCls(
                f"{downstream_gob_host}-review.googlesource.com"
            )
            pending_changes, abandoned_changes = (
                gerrit_inst.find_pending_changes(
                    project=downstream_project,
                    branch=downstream.branch,
                    hashtags=[config.topic],
                    subtree=downstream.subtree,
                    exclude_paths=config.exclude_file_patterns,
                )
            )
            logger.info(
                "Found %s pending and %s abandoned changes already on Gerrit",
                len(pending_changes),
                len(abandoned_changes),
            )

        fetch_downstream_target_head_from_remote(config, downstream)

        downstream.history_length = fetch_history_length(
            downstream, "Downstream"
        )

        verify_repos_share_history_to_adjust_limits(
            config, downstream, pending_changes
        )

        commit_files_map: dict[str, list[str]] = {}
        skipped_files_map: dict[str, list[str]] = {}

        (
            commits_to_copy,
            commit_files_map,
            skipped_files_map,
            copybot_skip_cls,
            skip_cq_from_parse_logic,
            owners_cls,
        ) = find_commits_to_copy(
            config,
            config.upstream,
            downstream,
            pending_changes=pending_changes,
            abandoned_changes=abandoned_changes,
        )

        if not commits_to_copy:
            # Nothing to copy, proceed to the next downstream
            continue

        if not config.filter_changes:
            downstream.subtree = ""
            config.upstream.subtree = ""

        if 0 < downstream.limit < len(commits_to_copy):
            logger.warning(
                "Limiting commits to copy from %s to %s",
                len(commits_to_copy),
                downstream.limit,
            )
            commits_to_copy = commits_to_copy[-downstream.limit :]

        pending_rev, cl_count = find_pending_change_at_bottom_of_stack(
            copybot_skip_cls=copybot_skip_cls,
            commits_to_copy=commits_to_copy,
            pending_changes=pending_changes,
            config=config,
            downstream=downstream,
        )

        checkout_downstream_repo(
            downstream,
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
            applied_cls,
        ) = cherry_pick_commits_to_downstream(
            config,
            downstream,
            patch_dir,
            skipped_files_map,
            commit_files_map,
            commits_to_copy,
            updated_commits_to_copy,
            pending_changes,
        )
        skip_cq = True
        if downstream.repo.rev_parse() == downstream.head_sha:
            logger.info("Nothing to push!")
        else:
            skip_cq = (
                any(conflicted_revs)
                or skip_cq_from_parse_logic
                or any(skipped_revs)
                or any(cl in owners_cls for cl in applied_cls)
            )
            push_changes_to_downstream(
                config, downstream, skip_cq, gerrit_inst, pending_changes
            )

        if config.config_file_path and not skip_cq:
            update_config_args = [
                "--config",
                config.config_file_path,
                "--generate-config",
                config.config_file_path,
                "--upstream-history-starts-with",
                config.upstream.history_starts_with,
                "--downstream-history-starts-with",
                downstream.history_starts_with,
                "--upstream-history-limit",
                str(config.upstream.history_limit),
                "--downstream-history-limit",
                str(downstream.history_limit),
            ]
            if config.dry_run:
                logger.info(
                    "Would have called update configs with %s",
                    update_config_args,
                )
            elif any(
                "copybot-skip" in cl.hashtags for cl in pending_changes.values()
            ):
                logging.info(
                    "Skipping up/down stream history origins update due to"
                    " pending CL with copybot-skip hashtag"
                )
            else:
                try:
                    copybot_argparser.generate_config(update_config_args)
                    upload_updated_config(config, downstream)
                except gerrit.MergeConflictsError as e:
                    logger.exception(
                        "Could not update up/down stream history origins %s", e
                    )
        else:
            logging.info(
                "Skipping up/down stream history origins update due to:"
            )
            if any(conflicted_revs):
                logging.info("Conflicted Revs")
            if skip_cq_from_parse_logic:
                logging.info("Parsing logic")
            if any(skipped_revs):
                logging.info("Skipped Revs")

        log_unapplied_commits(
            config.upstream.repo,
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


@contextlib.contextmanager
def get_git_root_dir(dev_mode_git_dir: pathlib.Path | None):
    if dev_mode_git_dir:
        dev_mode_git_dir.mkdir(parents=True, exist_ok=True)
        yield dev_mode_git_dir
    else:
        with tempfile.TemporaryDirectory(".copybot") as git_root_dir:
            yield git_root_dir


def create_luci_config(path: pathlib.Path):
    group_configs = []
    luci_jobs = []
    config = configparser.ConfigParser()
    group_config_file = path / "group_config.ini"
    found_files = config.read(group_config_file)
    manual_jobs = []
    if found_files:
        manual_jobs = ast.literal_eval(
            config.get("copybot", "manual_trigger", fallback="[]")
        )
        print(manual_jobs)
    manual_luci_jobs = []
    for fs_entry in os.listdir(path):
        if os.path.isfile(path / fs_entry):
            if fs_entry != "group_config.ini":
                if pathlib.Path(fs_entry).stem in manual_jobs:
                    logger.info("Adding %s as a manual job", fs_entry)
                    manual_luci_jobs.append(fs_entry)
                else:
                    logger.info("Adding %s", fs_entry)
                    group_configs.append(fs_entry)
        else:
            logger.info("Checking dir %s", fs_entry)
            luci_jobs.extend(create_luci_config(path / fs_entry))
    logger.debug("Checking %s", group_config_file)
    if not found_files:
        return luci_jobs
    logger.info("Read config from %s: %s", group_config_file, config)
    group_configs.sort()
    manual_luci_jobs.sort()
    notify_list = ast.literal_eval(config.get("copybot", "notify"))
    notify_list.sort()
    luci_jobs.append(
        CopybotJob(
            notify_email=notify_list,
            group_name=path.name,
            offset_hour_of_day=config.getint("copybot", "offset"),
            interval=config.getint("copybot", "interval"),
            timeout=config.getint("copybot", "timeout"),
            config_file=group_configs,
        )
    )
    if manual_luci_jobs:
        luci_jobs.append(
            CopybotJob(
                notify_email=notify_list,
                group_name=path.name,
                offset_hour_of_day=0,
                interval=0,
                timeout=config.getint("copybot", "timeout"),
                config_file=manual_luci_jobs,
            )
        )
    return luci_jobs


def create_luci_configs(
    config_dir: pathlib.Path = pathlib.Path(__file__).resolve().parent
    / "config",
):
    luci_cfgs = create_luci_config(config_dir)
    copybot_proto = CopybotJobs(copybot_jobs=luci_cfgs)
    copybot_proto.copybot_jobs.sort(key=lambda item: item.group_name)
    file_path = (
        pathlib.Path(__file__).resolve().parent.parent
        / "config"
        / "misc_builders"
        / "copybot_jobs.txtpb"
    )
    with open(
        file_path,
        "w",
        encoding="utf-8",
    ) as file_handle:
        file_handle.write(text_format.MessageToString(copybot_proto))

    # COMMIT AND UPLOAD


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s: %(message)s",
        level=logging.INFO,
    )
    logger.info("-- Starting CopyBot service --")

    parser = copybot_argparser.create_arg_parser()
    opts = parser.parse_args(argv)

    with (
        get_git_root_dir(opts.dev_mode_git_dir) as git_root_dir,
        tempfile.TemporaryDirectory("_patches") as patch_dir,
    ):
        config = copybot_argparser.parse_copybot_config(
            git_root_dir, argv, opts=opts
        )
        err = None
        try:
            if config.generate_config:
                copybot_argparser.generate_config(argv)
                upload_updated_config(config)
            elif config.gen_luci_jobs:
                create_luci_configs()
                logger.info(
                    "LUCI CFG has been generated at infra/config/misc_builders"
                )
                config_path = (
                    pathlib.Path(__file__).resolve().parent.parent / "config"
                )
                subprocess.run(
                    ["./regenerate_configs.py"],
                    check=True,
                    cwd=config_path,
                )
                try:
                    upload_cl(
                        config=config,
                        url=(
                            "https://chrome-internal.googlesource.com/"
                            "chromeos/infra/config"
                        ),
                        commit_msg=(
                            "copybot: Update builder config\n\n"
                            "Auto generated CL by copybot.\n"
                            "Update LUCI configs from config files\n\n"
                            "BUG=None\nTEST=./regenerate_configs.py"
                        ),
                        paths=[
                            "generated/*",
                            "luci/*",
                            "misc_builders/copybot_jobs.txtpb",
                        ],
                        hashtags=opts.hashtags,
                        config_repo=gerrit.GitRepo(config_path),
                    )
                except gerrit.MergeConflictError:
                    raise NothingToDo("All LUCI Jobs up to date")
            else:
                run_copybot(gerrit.Gerrit, config, patch_dir)
        except NothingToDo as e:
            logger.info("%s. Nothing to do!", str(e))
        except Exception as e:
            err = e
            raise
        finally:
            if config.json_out:
                write_json_error(config.json_out, err)

    logger.info("-- CopyBot finished successfully --")


if __name__ == "__main__":
    main()
