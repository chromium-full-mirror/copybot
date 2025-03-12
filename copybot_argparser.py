# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""copybot downstreaming config argparser.

Used for generating a common config to use across different downstream projects.
"""

import dataclasses
import os
import pathlib
import re
from typing import Any
import urllib
import urllib.parse

import configargparse  # type: ignore[import] # pylint: disable=import-error
import gerrit


def parse_insert_into_msg(insert_into_msg: list[str]) -> dict[int, str]:
    result = {}
    for msg in insert_into_msg:
        index, _, msg = msg.partition(":")
        result[int(index)] = msg
    return result


def parse_repo_info(repo_string: str) -> tuple[bool, str, str, str]:
    """Parse colon-separated repo info string with URL, branch and subtree."""

    is_local = True
    max_fields = 3
    repo_info: list[Any] = []

    base_string = repo_string
    url_result = urllib.parse.urlparse(repo_string)
    if url_result.netloc and url_result.scheme:
        is_local = False
    for _ in range(max_fields + 1):
        base_string, sep, current_field = base_string.rpartition(":")
        if not sep:
            if is_local:
                repo_info.insert(0, pathlib.Path(current_field))
            else:
                repo_info[0] = url_result.scheme + ":" + repo_info[0]
            break
        else:
            repo_info.insert(0, current_field)

    for _ in range(len(repo_info), max_fields):
        repo_info.append(None)

    repo_url = repo_info[0]
    repo_branch = repo_info[1]
    repo_subtree = repo_info[2]
    if not repo_branch:
        repo_branch = "main"

    return is_local, repo_url, repo_branch, repo_subtree


@dataclasses.dataclass
class TargetConfig:
    """Dataclass for common target configs."""

    # The maximum number of CLs in the upstream history to check.
    history_limit: int
    history_starts_with: str
    url: str
    branch: str
    subtree: str
    # The commit hash of the upstream HEAD.
    head_sha: str | None
    # Number of CLs to consider as a part of the upstream history.
    # 0 means unlimited
    history_length: int


@dataclasses.dataclass
class DownstreamConfig(TargetConfig):
    """Dataclass for downstream repo target config."""

    labels: list[str]
    reviewers: list[str]
    ccs: list[str]
    push_options: list[str]
    hashtags: list[str]
    # A string to prepend the subject line with.
    prepend_subject: str
    # A Dict(line, message) of messages to add to the commit msg.
    insert_into_msg: dict[int, str]
    keep_pseudoheaders: list[str]
    limit: int
    # The maximum number of CLs in the downstream history to check.
    include_paths: list[str | os.PathLike[str]]
    add_pseudoheaders: list[str]
    is_local: bool


@dataclasses.dataclass
class UpstreamConfig(TargetConfig):
    """Dataclass for upstream repo target config."""


@dataclasses.dataclass
class CopybotConfig:
    """Options that change Copybot functionality."""

    topic: str
    json_out: pathlib.Path
    dry_run: bool
    filter_file_patterns: list[re.Pattern]
    drop_paths: list[str | os.PathLike[str]]
    exclude_method: str
    merge_conflict_behavior: gerrit.MergeConflictBehavior
    add_signed_off_by: bool
    filter_changes: bool
    skip_job_name: list[str]
    skip_author_email: list[str]
    downstream: DownstreamConfig
    upstream: UpstreamConfig


def parse_copybot_config(argv: list[str] | None = None) -> CopybotConfig:
    """The entry point to the program."""
    parser = configargparse.ArgumentParser(
        description="CopyBot",
        default_config_files=["config/copybot.conf"],
    )
    parser.add(
        "-c",
        "--config",
        required=False,
        is_config_file=True,
        help="config file path",
    )
    parser.add_argument(
        "--topic",
        help="Topic to set and search in Gerrit",
        default="copybot",
    )
    parser.add_argument(
        "--label",
        help="Label to set in Gerrit (can be passed multiple times)",
        action="append",
        dest="labels",
        default=[],
    )
    parser.add_argument(
        "--re",
        help="Reviewer to set in Gerrit (can be passed multiple times)",
        action="append",
        dest="reviewers",
        default=[],
    )
    parser.add_argument(
        "--cc",
        help="CC to set in Gerrit (can be passed multiple times)",
        action="append",
        dest="ccs",
        default=[],
    )
    parser.add_argument(
        "--push-option",
        help="Add downstream push option (can be passed multiple times)",
        action="append",
        dest="push_options",
        default=[],
    )
    parser.add_argument(
        "--ht",
        help="Hashtag to set in Gerrit (can be passed multiple times)",
        action="append",
        dest="hashtags",
        default=[],
    )
    parser.add_argument(
        "--json-out",
        type=pathlib.Path,
        help="Write JSON result to this file.",
    )
    parser.add_argument(
        "--dry-run",
        help="Don't push",
        action="store_true",
    )
    parser.add_argument(
        "--prepend-subject",
        help="Prepend the subject of commits made with this string",
        default="",
    )
    parser.add_argument(
        "--insert-into-msg",
        help="Insert the text into the line specified.  Line number"
        " comes first and is separated from text by a colon",
        action="append",
        default=[],
    )
    parser.add_argument(
        "--exclude-file-pattern",
        help="Exclude changes to files matched by these path regexes",
        action="append",
        dest="exclude_file_patterns",
        default=[],
    )
    parser.add_argument(
        "--exclude-method",
        help="How to handle exclusions.  DROP: Drop the change."
        "FILTER: Filter the exclude-file-pattern matching files out of"
        " the CLs.",
        default="DROP",
        choices=[behavior.name for behavior in gerrit.ExclusionBehavior],
    )
    parser.add_argument(
        "--merge-conflict-behavior",
        help="How to handle merge conflicts",
        default="SKIP",
        choices=[behavior.name for behavior in gerrit.MergeConflictBehavior],
    )
    parser.add_argument(
        "--add-signed-off-by",
        help="Add Signed-off-by pseudoheader to commit messages",
        action="store_true",
    )
    parser.add_argument(
        "--no-filter-changes",
        help="Filter changes to the up/downstream subtree paths",
        action="store_false",
        dest="filter_changes",
    )
    parser.add_argument(
        "--keep-pseudoheader",
        help="Keep a pseudoheader from being prefixed",
        action="append",
        dest="keep_pseudoheaders",
        default=[],
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum number of CLs to downstream at once.  0 for infinite."
        "Default is 200 to avoid hitting the CL limit in Gerrit.",
    )
    parser.add_argument(
        "--upstream-history-limit",
        type=int,
        default=250,
        help="Maximum number of CLs in upstream history to check.  0 for"
        " infinite.",
    )
    parser.add_argument(
        "--downstream-history-limit",
        type=int,
        default=0,
        help="Maximum number of CLs in upstream history to check.  0 for"
        " infinite.",
    )
    parser.add_argument(
        "--include-downstream",
        action="append",
        default=[],
        help="Downstream include paths (relative to the subtree) separated by"
        " colons.  Note: Only supported with subtrees",
    )
    parser.add_argument(
        "--add-pseudoheader",
        action="append",
        default=[],
        dest="add_pseudoheaders",
        help="gerrit.Pseudoheaders to be added to the commit message",
    )
    parser.add_argument(
        "--skip-job-name",
        action="append",
        default=[],
        help="Skip CLs in upstream copied from the specified job name",
    )
    parser.add_argument(
        "--skip-author-email",
        action="append",
        default=[],
        help="Skip CLs in upstream authored by specified email",
    )
    parser.add_argument(
        "--upstream-history-starts-with",
        help="Commit hash to start comparing history from",
        default="",
    )
    parser.add_argument(
        "--downstream-history-starts-with",
        help="Commit hash to start comparing history from",
        default="",
    )
    parser.add_argument(
        "--upstream-url",
        help="Upstream Git URL, optionally with a branch and subtree separated"
        " by colons",
        default="",
        required=True,
        dest="upstream",
    )
    parser.add_argument(
        "--downstream-url",
        help="Downstream Git URL, optionally with a branch and subtree"
        "separated by colons",
        default="",
        required=True,
        dest="downstream",
    )
    opts = parser.parse_args(argv)
    (
        _,
        upstream_url,
        upstream_branch,
        upstream_subtree,
    ) = parse_repo_info(opts.upstream)
    (
        downstream_is_local,
        downstream_url,
        downstream_branch,
        downstream_subtree,
    ) = parse_repo_info(opts.downstream)

    drop_paths = []
    if (
        gerrit.ExclusionBehavior[opts.exclude_method]
        == gerrit.ExclusionBehavior.DROP
    ):
        drop_paths = opts.exclude_file_patterns

    filter_file_patterns = [
        re.compile(str(pattern)) for pattern in opts.exclude_file_patterns
    ]

    downstream_config = DownstreamConfig(
        labels=opts.labels,
        reviewers=opts.reviewers,
        ccs=opts.ccs,
        push_options=opts.push_options,
        hashtags=opts.hashtags,
        prepend_subject=opts.prepend_subject,
        insert_into_msg=parse_insert_into_msg(opts.insert_into_msg),
        keep_pseudoheaders=list(opts.keep_pseudoheaders),
        limit=opts.limit,
        history_limit=opts.downstream_history_limit,
        include_paths=opts.include_downstream,
        add_pseudoheaders=opts.add_pseudoheaders,
        history_starts_with=opts.downstream_history_starts_with,
        url=downstream_url,
        branch=downstream_branch,
        subtree=downstream_subtree,
        is_local=downstream_is_local,
        head_sha=None,
        history_length=0,
    )
    upstream_config = UpstreamConfig(
        url=upstream_url,
        branch=upstream_branch,
        subtree=upstream_subtree,
        history_limit=opts.upstream_history_limit,
        history_starts_with=opts.upstream_history_starts_with,
        head_sha=None,
        history_length=0,
    )
    copybot_config = CopybotConfig(
        topic=opts.topic,
        json_out=opts.json_out,
        dry_run=opts.dry_run,
        filter_file_patterns=filter_file_patterns,
        drop_paths=drop_paths,
        exclude_method=opts.exclude_method,
        merge_conflict_behavior=gerrit.MergeConflictBehavior[
            opts.merge_conflict_behavior
        ],
        add_signed_off_by=opts.add_signed_off_by,
        filter_changes=opts.filter_changes,
        skip_job_name=opts.skip_job_name,
        skip_author_email=opts.skip_author_email,
        downstream=downstream_config,
        upstream=upstream_config,
    )

    return copybot_config
