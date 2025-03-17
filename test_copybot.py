# Copyright 2022 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unit tests for copybot.py module."""

import json
import os
import pathlib
import tempfile
from typing import Dict, Final, List, Tuple, Union
from unittest import mock

import copybot
import copybot_argparser
import gerrit
import pytest


CHANGE_ID: Final[str] = "I657462dfcec2969cc00d2804922b71587094c87h"


def get_default_copybot_config() -> copybot_argparser.CopybotConfig:
    upstream_config = copybot_argparser.UpstreamConfig(
        history_limit=250,
        history_starts_with="",
        url="https://chromium.googlesource.com/chromiumos/a",
        branch="main",
        subtree="",
        head_sha=None,
        history_length=0,
    )
    downstream_config = copybot_argparser.DownstreamConfig(
        history_limit=0,
        history_starts_with="",
        url="https://chromium.googlesource.com/chromiumos/b",
        branch="main",
        subtree="",
        head_sha=None,
        history_length=0,
        labels=[],
        reviewers=[],
        ccs=[],
        push_options=[],
        hashtags=[],
        prepend_subject="",
        insert_into_msg={},
        keep_pseudoheaders=[],
        limit=200,
        include_paths=[],
        add_pseudoheaders=[],
        is_local=True,
    )
    copybot_config = copybot_argparser.CopybotConfig(
        topic="copybot",
        json_out=pathlib.Path("json_out"),
        dry_run=False,
        filter_file_patterns=[],
        drop_paths=[],
        exclude_method="DROP",
        merge_conflict_behavior=gerrit.MergeConflictBehavior.SKIP,
        add_signed_off_by=False,
        filter_changes=True,
        skip_job_name=[],
        skip_author_email=[],
        downstream=downstream_config,
        upstream=upstream_config,
    )
    return copybot_config


class GitRepoMock:
    """GitRepo mock for testing purposes."""

    def __init__(self, git_dir: Union[str, "os.PathLike[str]"]) -> None:
        self.git_dir = git_dir

    def rev_parse(self, unused_rev: str = "HEAD") -> str:
        return "commit_sha"

    def fetch(self, *unused_args, **unused_kwargs) -> str:
        return "fetched_commit_sha"

    def checkout(self, unused_ref: str) -> None:
        pass

    def log(self, *unused_args, **unused_kwargs) -> str:
        return "log_result"

    def log_hashes(
        self,
        *unused_args,
        **unused_kwargs,
    ) -> List[str]:
        return [
            "commit_sha",
        ]

    def get_commit_message(self, unused_rev: str = "HEAD") -> str:
        return f"""Commit message

Change-Id: {CHANGE_ID}
        """

    def get_author_email(self, unused_rev: str = "HEAD") -> str:
        return "example@gmail.com"

    def get_author_name(self, unused_rev: str = "HEAD") -> str:
        return "Alan Turing"

    def get_subject(
        self, rev: str = "HEAD"  # pylint: disable=unused-argument
    ) -> str:
        return "Commit subject"

    def commit_file_list(self, unused_rev: str = "HEAD") -> List[str]:
        return []

    def reword(self, *unused_args, **unused_kwargs) -> str:
        return "reworded_commit_sha"

    def filter_commit(self, *unused_args, **unused_kwargs):
        return "filter_commit_sha"

    def cherry_pick(
        self,
        *unused_args,
        **unused_kwargs,
    ) -> None:
        pass

    def push(self, *unused_args, **unused_kwargs) -> None:
        pass

    def get_cl_count(
        self,
        *unused_args,
        **unused_kwargs,
    ) -> int:
        return 0


class GerritMock:
    """Gerrit mock for testing purposes."""

    def __init__(self, hostname: str) -> None:
        self.hostname = hostname

    def find_pending_changes(
        self, *unused_args, **unused_kwargs
    ) -> Tuple[Dict[str, gerrit.GerritClInfo], Dict[str, gerrit.GerritClInfo]]:
        cl_info = gerrit.GerritClInfo(change_id=CHANGE_ID, hashtags="", ref="")
        return ({"commit_sha": cl_info}, {})


def test_prefix_pseudoheaders():
    """Test the .prefix() method of Pseudoheaders."""
    pseudoheaders = gerrit.Pseudoheaders(
        [
            ("Signed-off-by", "Alyssa P. Hacker <aphacker@example.org>"),
            ("CQ-DEPEND", "chromium:1234,chrome-internal:5678"),
        ]
    )

    new_pseudoheaders = pseudoheaders.prefix(keep=["Cq-Depend"])
    commit_message = new_pseudoheaders.add_to_commit_message("Some commit msg")
    assert (
        commit_message
        == """Some commit msg

Original-Signed-off-by: Alyssa P. Hacker <aphacker@example.org>
CQ-DEPEND: chromium:1234,chrome-internal:5678
"""
    )


@pytest.mark.parametrize(
    ["exception", "expected"],
    [
        (None, {}),
        (Exception(), {"failure_reason": "FAILURE_UNKNOWN"}),
        (
            gerrit.MergeConflictsError(commits=["deadbeef", "deadd00d"]),
            {
                "failure_reason": "FAILURE_MERGE_CONFLICTS",
                "merge_conflicts": [
                    {"hash": "deadbeef"},
                    {"hash": "deadd00d"},
                ],
            },
        ),
    ],
)
def test_write_json_error(tmp_path, exception, expected):
    err_out = tmp_path / "err.json"
    copybot.write_json_error(err_out, exception)
    assert json.loads(err_out.read_text()) == expected


def test_main_raise_error(tmp_path):
    err_out = tmp_path / "err.json"
    with mock.patch(
        "copybot.run_copybot", side_effect=gerrit.PushError("failed to push")
    ):
        with pytest.raises(gerrit.PushError):
            copybot.main(
                argv=[
                    "--json-out",
                    str(err_out),
                    "--upstream-url",
                    "upstream",
                    "--downstream-url",
                    "downstream",
                ]
            )
    assert json.loads(err_out.read_text()) == {
        "failure_reason": "FAILURE_DOWNSTREAM_PUSH_ERROR",
    }


def test_run_copybot__smoke_test():
    config = get_default_copybot_config()
    with (
        tempfile.TemporaryDirectory(".copybot") as git_dir,
        tempfile.TemporaryDirectory("_patches") as patch_dir,
    ):
        with pytest.raises(copybot.NothingToDo):
            copybot.run_copybot(
                GitRepoMock,
                GerritMock,
                config,
                git_dir,
                patch_dir,
            )
