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

import json
import logging
import os
import pathlib
import re
import subprocess
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import copybot_argparser
import gerrit


logger = logging.getLogger(__name__)


def find_last_merged_rev(
    repo: gerrit.GitRepo,
    upstream_rev: str,
    downstream_rev: str,
    upstream_subtree: str = "",
    downstream_subtree: str = "",
    exclude_file_patterns: Iterable[str | "os.PathLike[str]"] = (),
    include_change_id: bool = False,
    upstream_history_length: int = 0,
    downstream_history_length: int = 0,
    pending_changes: Optional[Dict[str, gerrit.GerritClInfo]] = None,
) -> Tuple[str, int]:
    """Find the last merged revision in a Git repo.

    Args:
        repo: The GitRepo.
        upstream_rev: The commit hash of the upstream HEAD.
        downstream_rev: The commit hash of the downstream HEAD.
        upstream_subtree: The subtree of interest of the upstream repo.
        downstream_subtree: The subtree of interest of the downstream repo.
        exclude_file_patterns: List of paths to be excluded.
        include_change_id: Bool specifying whether or not to
            consider Change-Ids
        upstream_history_length: Number of CLs to consider as a part of the
            upstream history.
        downstream_history_length: Number of CLs to consider as a part of the
            downstream history.
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
        revision_range=upstream_rev,
        subtree=upstream_subtree,
        exclude_file_patterns=exclude_file_patterns,
        num=upstream_history_length,
    )
    downstream_hashes = repo.log_hashes(
        revision_range=downstream_rev,
        subtree=downstream_subtree,
        exclude_file_patterns=exclude_file_patterns,
        num=downstream_history_length,
    )

    upstream_change_ids = {}
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
    repo: gerrit.GitRepo,
    downstream_rev: str,
    downstream_subtree: str = "",
    exclude_file_patterns: Iterable[str | "os.PathLike[str]"] = (),
    limit: int = 0,
    upstream_change_ids: Optional[Dict[str, str]] = None,
    downstream_history_length: int = 0,
) -> List[str]:
    """Find the last merged revision in a Git repo.

    Args:
        repo: The GitRepo.
        downstream_rev: The commit hash of the downstream HEAD.
        downstream_subtree: The subtree of interest of the downstream repo.
        exclude_file_patterns: List of paths to be excluded.
        limit: The maximum number of CLs in the history to check.
        upstream_change_ids: Dictionary of upstream Change-Id's and their
            associated upstream commit hash.
        downstream_history_length: Number of CLs to consider as a part of the
            downstream history.

    Returns:
        The set of upstream commit hashes that have already been downstreamed.
    """
    downstream_hashes = repo.log_hashes(
        revision_range=downstream_rev,
        subtree=downstream_subtree,
        exclude_file_patterns=exclude_file_patterns,
        num=downstream_history_length,
    )
    downstreamed_revs = list(downstream_hashes)

    for counter, rev in enumerate(downstream_hashes):
        if counter > limit and limit != 0:
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
    repo: gerrit.GitRepo,
    upstream_rev: str,
    upstream_subtree: str,
    downstream_rev: str,
    downstream_subtree: str,
    include_paths: List[Union[str, "os.PathLike[str]"]],
    upstream_limit: int = 0,
    downstream_limit: int = 0,
    exclude_file_patterns: Iterable[str | "os.PathLike[str]"] = (),
    filter_file_patterns: Optional[List[re.Pattern[Any]]] = None,
    pending_changes: Optional[Dict[str, gerrit.GerritClInfo]] = None,
    abandoned_changes: Optional[Dict[str, gerrit.GerritClInfo]] = None,
    skip_copybot_job_names: Iterable[str] = (),
    skip_author_emails: Iterable[str] = (),
    include_change_id: bool = False,
    upstream_history_length: int = 0,
    downstream_history_length: int = 0,
) -> Tuple[
    List[str], Dict[str, List[str]], Dict[str, List[str]], List[str], bool
]:
    """Find the commits to copy to downstream.

    Args:
        repo: The GitRepo.
        upstream_rev: The commit hash of the upstream HEAD.
        upstream_subtree: The subtree of interest of the upstream repo.
        downstream_rev: The commit hash of the downstream HEAD.
        downstream_subtree: The subtree of interest of the downstream repo.
        include_paths: The paths to include from the upstream relative to
            the downstream subtree(Only valid with downstream subtree)
        upstream_limit: The maximum number of CLs in the upstream history to
            check.
        downstream_limit: The maximum number of CLs in the downstream history
            to check.
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
        upstream_history_length: Number of CLs to consider as a part of the
            upstream history.
        downstream_history_length: Number of CLs to consider as a part of the
            downstream history.

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
        revision_range=upstream_rev,
        subtree=upstream_subtree,
        exclude_file_patterns=exclude_file_patterns,
        num=upstream_history_length,
    )
    if include_change_id:
        for counter, rev in enumerate(upstream_hashes):
            if counter > upstream_limit and upstream_limit != 0:
                break
            change_id = gerrit.get_change_id(repo.get_commit_message(rev))
            if change_id:
                upstream_change_ids[change_id] = rev
    downstreamed_revs = get_downstreamed_list(
        repo=repo,
        downstream_rev=downstream_rev,
        downstream_subtree=downstream_subtree,
        exclude_file_patterns=exclude_file_patterns,
        limit=downstream_limit,
        upstream_change_ids=upstream_change_ids,
        downstream_history_length=downstream_history_length,
    )

    counter = 0
    pending_to_submit = False
    for rev in upstream_hashes:
        # Early exit if limit reached to avoid inadvertent continuation
        if counter > upstream_limit and upstream_limit != 0:
            logger.info("Hit upstream limit of %s", upstream_limit)
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

        if downstream_subtree and include_paths:
            commit_files = repo.commit_file_list(rev)
            filtered_commit_files = []
            for path in commit_files:
                filtered_path = pathlib.Path(path).relative_to(upstream_subtree)
                if filtered_path in include_paths:
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
    repo: gerrit.GitRepo,
    upstream_rev: str,
    change_id: str,
    skipped_files=(),
    prepend_subject: str = "",
    insert_into_msg: Optional[Dict[int, str]] = None,
    sign_off: bool = False,
    keep_pseudoheaders: Iterable[str] = (),
    additional_pseudoheaders: Iterable[str] = (),
) -> None:
    """Reword the commit at HEAD with appropriate metadata.

    Args:
        repo: The GitRepo to operate on.
        upstream_rev: The upstream commit hash corresponding to this commit.
        change_id: The Change-Id to add to the commit.
        skipped_files: The list of files skipped.
        prepend_subject: A string to prepend the subject line with.
        insert_into_msg: A Dict(line, message) of messages to add to the
            commit msg.
        sign_off: True if Signed-off-by should be added to the commit message.
        keep_pseudoheaders: Pseudoheaders which should not be prefixed.
        additional_pseudoheaders: Psuedoheaders to be added to the commit
            message.
    """
    commit_message = repo.get_commit_message()
    if prepend_subject:
        commit_message = prepend_subject + commit_message
    if insert_into_msg:
        tmp_commit_msg = commit_message.splitlines()
        for line, msg in sorted(insert_into_msg.items(), reverse=True):
            tmp_commit_msg.insert(line, msg)
        commit_message = "\n".join(tmp_commit_msg)
    pseudoheaders, commit_message = gerrit.Pseudoheaders.from_commit_message(
        commit_message
    )
    pseudoheaders = pseudoheaders.prefix(keep=keep_pseudoheaders)

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
        or not keep_pseudoheaders
        or "Change-Id" not in keep_pseudoheaders
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


def run_copybot(
    config: copybot_argparser.CopybotConfig,
    git_dir: Union[str, "os.PathLike[str]"],
    patch_dir: Union[str, "os.PathLike[str]"],
) -> None:
    """Run copybot.

    Args:
        config: The parsed command line arguments.
        git_dir: A temporary or local directory to use for Git operations.
        patch_dir: A temporary directory to use for storing patch files.
    """
    drop_paths = []
    if (
        gerrit.ExclusionBehavior[config.exclude_method]
        == gerrit.ExclusionBehavior.DROP
    ):
        drop_paths = config.exclude_file_patterns
    filter_file_patterns = [
        re.compile(str(pattern)) for pattern in config.exclude_file_patterns
    ]

    insert_into_msg = {}
    for msg in config.downstream.insert_into_msg:
        index, _, msg = msg.partition(":")
        insert_into_msg[int(index)] = msg

    if config.downstream.is_local:
        git_dir = config.downstream.url

    keep_pseudoheaders = list(config.downstream.keep_pseudoheaders)
    related_repo = False
    if config.upstream.url == config.downstream.url or (
        is_server_gob(str(config.downstream.url))
        and is_server_gob(str(config.upstream.url))
    ):
        related_repo = True
        if "Change-Id" not in keep_pseudoheaders:
            keep_pseudoheaders.append("Change-Id")
    merge_conflict_behavior = gerrit.MergeConflictBehavior[
        config.merge_conflict_behavior
    ]
    pending_changes: Dict[str, gerrit.GerritClInfo] = {}
    abandoned_changes: Dict[str, gerrit.GerritClInfo] = {}
    gerrit_inst: Optional[gerrit.Gerrit] = None
    if (m := is_server_gob(str(config.downstream.url))) is not None:
        downstream_gob_host = m.group(1)
        downstream_project = m.group(2)

        gerrit_inst = gerrit.Gerrit(
            f"{downstream_gob_host}-review.googlesource.com"
        )
        pending_changes, abandoned_changes = gerrit_inst.find_pending_changes(
            project=downstream_project,
            branch=config.downstream.branch,
            hashtags=[config.topic],
            subtree=config.downstream.subtree,
            exclude_paths=drop_paths,
        )
        logger.info(
            "Found %s pending and %s abandoned changes already on Gerrit",
            len(pending_changes),
            len(abandoned_changes),
        )
    repo = gerrit.GitRepo.init(git_dir)
    try:
        upstream_rev = repo.fetch(config.upstream.url, config.upstream.branch)
    except subprocess.CalledProcessError as e:
        raise gerrit.UpstreamFetchError(
            f"Failed to fetch branch {config.upstream.branch} from "
            f"{config.upstream.url}"
        ) from e

    try:
        downstream_rev = repo.fetch(
            config.downstream.url, config.downstream.branch
        )
    except subprocess.CalledProcessError as e:
        raise gerrit.DownstreamFetchError(
            f"Failed to fetch branch {config.downstream.branch} from "
            f"{config.downstream.url}"
        ) from e
    upstream_history_length = 0
    downstream_history_length = 0
    if config.upstream.history_starts_with:
        upstream_history_length = (
            repo.get_cl_count(
                config.upstream.history_starts_with,
                upstream_rev,
                config.upstream.subtree,
            )
            + 1
        )
        logger.info("Upstream history length: %d", upstream_history_length)
    if config.downstream.history_starts_with:
        downstream_history_length = (
            repo.get_cl_count(
                config.downstream.history_starts_with,
                downstream_rev,
                config.downstream.subtree,
            )
            + 1
        )
        logger.info("Downstream history length: %d", downstream_history_length)

    # Verify that the two repositories share a history
    num_cls_to_downstream = 0
    last_related_rev = ""

    last_related_rev, num_cls_to_downstream = find_last_merged_rev(
        repo,
        upstream_rev,
        downstream_rev,
        config.upstream.subtree,
        config.downstream.subtree,
        drop_paths,
        related_repo,
        pending_changes=pending_changes,
        upstream_history_length=upstream_history_length,
        downstream_history_length=downstream_history_length,
    )
    if last_related_rev in pending_changes:
        logger.info("Last related revision from pending changes!")
    logger.info("Last related revision: %s", last_related_rev)

    logger.info("Found: %s new changes to downstream", num_cls_to_downstream)

    pending_modifications = False
    for _, pending_cl in pending_changes.items():
        if (
            "copybot-reword" in pending_cl.hashtags
            or "copybot-preserve" not in pending_cl.hashtags
        ):
            pending_modifications = True
            break
    if not num_cls_to_downstream and not pending_modifications:
        # No CLs to downstream, and no modifications to pending CLs
        logger.info("Nothing to do!")
        return

    num_cls_to_downstream += len(pending_changes)

    if (
        num_cls_to_downstream > config.upstream.history_limit
        and config.upstream.history_limit != 0
    ):
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
        config.downstream.history_limit = downstream_history_length

    commit_files_map: Dict[str, List[str]] = {}
    skipped_files_map: Dict[str, List[str]] = {}

    (
        commits_to_copy,
        commit_files_map,
        skipped_files_map,
        copybot_skip_cls,
        pending_to_submit,
    ) = find_commits_to_copy(
        repo,
        upstream_rev,
        config.upstream.subtree,
        downstream_rev,
        config.downstream.subtree,
        include_paths=config.downstream.include_paths,
        upstream_limit=config.upstream.history_limit,
        downstream_limit=config.downstream.history_limit,
        exclude_file_patterns=drop_paths,
        filter_file_patterns=filter_file_patterns,
        pending_changes=pending_changes,
        abandoned_changes=abandoned_changes,
        skip_copybot_job_names=config.skip_job_name,
        skip_author_emails=config.skip_author_email,
        upstream_history_length=upstream_history_length,
        downstream_history_length=downstream_history_length,
    )

    if not commits_to_copy:
        logger.info("Nothing to do!")
        return

    conflicted_revs = []
    empty_revs = []
    skipped_revs = []

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

    # Determine if there is a pending change at the beginning of the stack.
    #  If so, find the CL at the top of the pending stack.
    #  If not, or if the copybot-rebase hashtag is used, revert to the
    #    original default behavior of starting with ToT HEAD of the downstream
    #    and cherry-picking from upstream.
    #  * Note - this operates on the assumption that there is a single stack
    #    of CLs.  CLs in separate stacks beneath the lowest pending change are
    #    effectively ignored.  Additionally, any pending copybot-skip changes
    #    will cause the entire stack to be cherry-picked.
    pending_rev = None
    cl_count = 0
    if not copybot_skip_cls:
        for rev in reversed(commits_to_copy):
            if rev not in pending_changes:
                break
            if any(
                (
                    tag in ("copybot-rebase", "copybot-reword")
                    for tag in pending_changes[rev].hashtags
                )
            ):
                break
            pending_rev = rev
            cl_count += 1
    if cl_count > 0:
        logger.info(
            "Found %d pending changes at the bottom of the stack.", cl_count
        )
        if cl_count == len(commits_to_copy):
            logger.info("All found changes are pending.  Nothing to do!")
            return
        logger.info("Checking out the top change: %s.", pending_rev)
        repo.fetch(
            config.downstream.url,
            pending_changes[str(pending_rev)].current_ref,
        )
        repo.checkout("FETCH_HEAD")
    else:
        repo.checkout(downstream_rev)
        cl_count = 0
    updated_commits_to_copy = commits_to_copy[
        : (len(commits_to_copy) - cl_count)
    ]
    for i, rev in enumerate(reversed(updated_commits_to_copy)):
        logger.info(
            "(%s/%s) Cherry-pick %s",
            i + 1,
            len(updated_commits_to_copy),
            rev,
        )
        pending_change = False
        reword_pending_change = False
        if rev in pending_changes:
            if "copybot-preserve" in pending_changes[rev].hashtags:
                logger.info(
                    "Preserving pending change due to copybot-preserve hashtag."
                )
                pending_change = True
            if "copybot-reword" in pending_changes[rev].hashtags:
                reword_pending_change = True
                logger.info(
                    "Rewording commit message due to copybot-reword hashtag."
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
                    exclude_paths=drop_paths,
                )
        except gerrit.EmptyCommitError:
            logger.warning("Skip cherry-pick due to empty commit")
            empty_revs.append(rev)
            continue
        except gerrit.MergeConflictError as e:
            logger.error("Merge conflict cherry-picking %s!", rev)
            if merge_conflict_behavior is gerrit.MergeConflictBehavior.SKIP:
                logger.warning("Skipping %s", rev)
                skipped_revs.append(rev)
                continue
            elif merge_conflict_behavior is gerrit.MergeConflictBehavior.STOP:
                logger.warning("Stopping at revision %s", rev)
                skipped_revs.extend(list(reversed(commits_to_copy))[i:])
                break
            elif (
                merge_conflict_behavior
                is gerrit.MergeConflictBehavior.ALLOW_CONFLICT
            ):
                logger.warning("Committing %s with conflicts", rev)
                if pending_change:
                    repo.fetch(
                        config.downstream.url, pending_changes[rev].current_ref
                    )
                    try:
                        repo.cherry_pick(rev="FETCH_HEAD", allow_conflict=True)
                    except gerrit.EmptyCommitError:
                        logger.warning("Skip cherry-pick due to empty commit")
                        empty_revs.append(rev)
                        continue
                else:
                    try:
                        repo.cherry_pick(
                            filtered_rev or rev,
                            patch_dir=patch_dir,
                            upstream_subtree=config.upstream.subtree,
                            downstream_subtree=config.upstream.subtree,
                            include_paths=config.downstream.include_paths,
                            exclude_paths=drop_paths,
                            allow_conflict=True,
                        )
                    except gerrit.EmptyCommitError:
                        logger.warning("Skip cherry-pick due to empty commit")
                        empty_revs.append(rev)
                        continue
                if not pending_change or reword_pending_change:
                    change_id = (
                        pending_changes.get(rev)
                        or gerrit.GerritClInfo("", "", "")
                    ).change_id
                    rewrite_commit_message(
                        repo,
                        upstream_rev=rev,
                        change_id=change_id or gerrit.generate_change_id(),
                        skipped_files=skipped_files_map[rev],
                        prepend_subject=config.downstream.prepend_subject,
                        insert_into_msg=insert_into_msg,
                        sign_off=config.add_signed_off_by,
                        keep_pseudoheaders=keep_pseudoheaders,
                        additional_pseudoheaders=[
                            *config.downstream.add_pseudoheaders,
                            "Commit: false",
                        ],
                    )
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
                change_id=change_id or gerrit.generate_change_id(),
                skipped_files=skipped_files_map[rev],
                prepend_subject=config.downstream.prepend_subject,
                insert_into_msg=insert_into_msg,
                sign_off=config.add_signed_off_by,
                keep_pseudoheaders=keep_pseudoheaders,
                additional_pseudoheaders=config.downstream.add_pseudoheaders,
            )
        current_change = repo.log(num=1, fmt="%H").stdout.strip()
        logger.info("Revision %s cherry-picked as %s", rev, current_change)

    if repo.rev_parse() == downstream_rev:
        logger.info("Nothing to push!")
    else:
        skip_cq = any(conflicted_revs) or pending_to_submit
        push_refspec = get_push_refspec(
            config, config.downstream.branch, skip_cq
        )
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

    emptylist = [
        repo.log(rev, fmt="%H %s", num=1).stdout.strip() for rev in empty_revs
    ]
    if emptylist:
        logger.warning(
            "The following commits were not applied because they were empty:"
        )
        for rev in emptylist:
            logger.warning("- %s", rev)

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

    conflictedlist = [
        repo.log(rev, fmt="%H %s", num=1).stdout.strip()
        for rev in conflicted_revs
    ]
    if conflictedlist:
        logger.error("The following commits were uploaded with conflicts:")
        for rev in conflictedlist:
            logger.error("- %s", rev)
        raise gerrit.MergeConflictsError(commits=conflicted_revs)


def write_json_error(path: pathlib.Path, err: Exception | None) -> None:
    """Write out the JSON-serialized protobuf from an exception.

    Args:
        path: The Path to write to.
        err: The exception to serialize.
    """
    err_json: Dict[str, Any] = {}
    if err:
        if isinstance(err, gerrit.CopybotFatalError):
            err_json["failure_reason"] = err.enum_name
            if err.commits:
                err_json["merge_conflicts"] = [{"hash": x} for x in err.commits]
        else:
            err_json["failure_reason"] = gerrit.CopybotFatalError.enum_name
    logger.debug("JSON response: %s", err_json)
    path.write_text(json.dumps(err_json))


def main(argv: Optional[List[str]] = None) -> None:
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
        with tempfile.TemporaryDirectory(
            ".copybot"
        ) as git_dir, tempfile.TemporaryDirectory("_patches") as patch_dir:
            run_copybot(config, git_dir, patch_dir)
    except Exception as e:
        err = e
        raise
    finally:
        if config.json_out:
            write_json_error(config.json_out, err)


if __name__ == "__main__":
    main()
