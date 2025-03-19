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
REVISION: Final[str] = "deadbeef"

PENDING_CHANGES = {
    REVISION: gerrit.GerritClInfo(change_id=CHANGE_ID, hashtags="", ref="REF")
}


def get_default_copybot_config() -> copybot_argparser.CopybotConfig:
    upstream_config = copybot_argparser.UpstreamConfig(
        history_limit=250,
        history_starts_with="",
        url="https://chromium.googlesource.com/chromiumos/a",
        branch="main",
        subtree="",
        head_sha=REVISION,
        history_length=0,
    )
    downstream_config = copybot_argparser.DownstreamConfig(
        history_limit=250,
        history_starts_with=REVISION,
        url="https://chromium.googlesource.com/chromiumos/b",
        branch="main",
        subtree="",
        head_sha=REVISION,
        history_length=0,
        labels=["gerrit_label"],
        reviewers=[],
        ccs=["guy.fieri@example.com"],
        push_options=[],
        hashtags=["copybot_tag"],
        prepend_subject="",
        insert_into_msg={},
        keep_pseudoheaders=[],
        limit=200,
        include_paths=[],
        add_pseudoheaders=[],
        is_local=False,
    )
    copybot_config = copybot_argparser.CopybotConfig(
        topic="copybot",
        json_out=pathlib.Path("json_out"),
        dry_run=False,
        filter_file_patterns=[],
        exclude_file_patterns=[],
        exclude_method="DROP",
        merge_conflict_behavior=gerrit.MergeConflictBehavior.SKIP,
        add_signed_off_by=False,
        filter_changes=True,
        skip_job_names=[],
        skip_author_emails=[],
        downstream=downstream_config,
        upstream=upstream_config,
    )
    return copybot_config


class GitRepoMock:
    """GitRepo mock for testing purposes."""

    def __init__(self, git_dir: Union[str, "os.PathLike[str]"] = "") -> None:
        self.git_dir = git_dir

    def rev_parse(self, rev: str = "HEAD") -> str:
        del rev
        return REVISION

    def fetch(self, *unused_args, **unused_kwargs) -> str:
        return "fetched_commit_sha"

    def checkout(self, ref: str) -> None:
        del ref

    def log(self, *unused_args, **unused_kwargs) -> str:
        return "log_result"

    def log_hashes(
        self,
        *unused_args,
        **unused_kwargs,
    ) -> List[str]:
        return [
            REVISION,
        ]

    def get_commit_message(self, rev: str = "HEAD") -> str:
        del rev
        return f"""Commit message

Change-Id: {CHANGE_ID}
        """

    def get_author_email(self, rev: str = "HEAD") -> str:
        del rev
        return "example@gmail.com"

    def get_author_name(self, rev: str = "HEAD") -> str:
        del rev
        return "Alan Turing"

    def get_subject(self, rev: str = "HEAD") -> str:
        del rev
        return "Commit subject"

    def commit_file_list(self, rev: str = "HEAD") -> List[str]:
        del rev
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
        return 1


class GerritMock:
    """Gerrit mock for testing purposes."""

    def __init__(self, hostname: str) -> None:
        self.hostname = hostname

    def find_pending_changes(
        self, *unused_args, **unused_kwargs
    ) -> Tuple[Dict[str, gerrit.GerritClInfo], Dict[str, gerrit.GerritClInfo]]:
        return PENDING_CHANGES, {}


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


@pytest.fixture(name="copybot_config")
def copybot_config_fixture():
    return get_default_copybot_config()


def test_run_copybot__smoke_test(copybot_config) -> None:
    with (
        tempfile.TemporaryDirectory(".copybot") as git_dir,
        tempfile.TemporaryDirectory("_patches") as patch_dir,
    ):
        with pytest.raises(copybot.NothingToDo):
            copybot.run_copybot(
                GitRepoMock,
                GerritMock,
                copybot_config,
                git_dir,
                patch_dir,
            )


def test_parse_insert_into_msg():
    msg = ["1:Android Bringup: See http://go/android-fw-sync"]
    assert copybot_argparser.parse_insert_into_msg(msg) == {
        1: "Android Bringup: See http://go/android-fw-sync",
    }


def test_are_repos_related(copybot_config) -> None:
    assert copybot.are_repos_related(
        copybot_config.upstream, copybot_config.downstream
    )


def test_get_downstreamed_list(copybot_config) -> None:
    downstreamed_revs = copybot.get_downstreamed_list(
        GitRepoMock(),
        copybot_config,
        copybot_config.downstream,
        upstream_change_ids={},
    )
    assert downstreamed_revs == [REVISION]


def test_get_downstreamed_list__mapped_changed_id(copybot_config) -> None:
    downstreamed_revs = copybot.get_downstreamed_list(
        GitRepoMock(),
        copybot_config,
        copybot_config.downstream,
        upstream_change_ids={CHANGE_ID: "deadc0de"},
    )
    assert downstreamed_revs == [REVISION, "deadc0de"]


def test_rewrite_commit_message(copybot_config) -> None:
    reworded_message, updated_author = copybot.rewrite_commit_message(
        GitRepoMock(),
        REVISION,
        copybot_config.upstream,
        copybot_config.downstream,
        change_id=CHANGE_ID,
    )
    expected_commit_message = f"""Commit message

Change-Id: {CHANGE_ID}
GitOrigin-RevId: {REVISION}
"""
    expected_author = "Alan Turing<example@gmail.com-copybot-pick>"

    assert reworded_message == expected_commit_message
    assert updated_author == expected_author


def test_get_push_refspect(copybot_config) -> None:
    assert copybot.get_push_refspec(
        copybot_config, copybot_config.downstream.branch, skip_cq=False
    ) == (
        "HEAD:refs/for/main%ready,l=gerrit_label,"
        "cc=guy.fieri@example.com,t=copybot,t=copybot_tag"
    )


def test_is_server_gob(copybot_config) -> None:
    assert copybot.is_server_gob(copybot_config.downstream.url)
    assert copybot.is_server_gob(copybot_config.upstream.url)
    assert not copybot.is_server_gob(
        "https://github.com/coq-community/coq-tricks"
    )


def test_fetch_history_length(copybot_config) -> None:
    cl_count = 1
    assert (
        copybot.fetch_history_length(
            GitRepoMock(), copybot_config.downstream, "location"
        )
        == cl_count + 1
    )


def test_find_pending_change_at_bottom_of_stack():
    pending_rev, cl_count = copybot.find_pending_change_at_bottom_of_stack(
        copybot_skip_cls=[],
        commits_to_copy=[REVISION],
        pending_changes=PENDING_CHANGES,
    )
    assert (pending_rev, cl_count) == (REVISION, 1)


def test_checkout_downstream_repo__all_pending(copybot_config) -> None:
    with pytest.raises(copybot.NothingToDo):
        copybot.checkout_downstream_repo(
            GitRepoMock(),
            copybot_config.downstream,
            commits_to_copy=[REVISION],
            pending_changes=PENDING_CHANGES,
            cl_count=1,
            pending_rev=REVISION,
        )


@mock.patch.object(GitRepoMock, "fetch")
def test_checkout_downstream_repo_fetches_from_repo(
    repo_fetch, copybot_config
) -> None:
    copybot.checkout_downstream_repo(
        GitRepoMock(),
        copybot_config.downstream,
        commits_to_copy=[REVISION],
        pending_changes=PENDING_CHANGES,
        cl_count=2,
        pending_rev=REVISION,
    )
    repo_fetch.assert_called_with(copybot_config.downstream.url, "REF")


@mock.patch.object(GitRepoMock, "push")
def test_push_changes_to_downstream(repo_push, copybot_config) -> None:
    copybot.push_changes_to_downstream(
        GitRepoMock(),
        copybot_config,
        copybot_config.downstream,
        skip_cq=False,
    )
    push_refspec = (
        "HEAD:refs/for/main%ready,l=gerrit_label,"
        "cc=guy.fieri@example.com,t=copybot,t=copybot_tag"
    )
    repo_push.assert_called_with(
        copybot_config.downstream.url,
        push_refspec,
        options=copybot_config.downstream.push_options,
    )
