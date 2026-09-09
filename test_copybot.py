# Copyright 2022 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unit tests for copybot.py module."""

import io
import json
import os
import pathlib
import shutil
import tempfile
from typing import Dict, Final, List, Tuple, Union
from unittest import mock

import copybot
import copybot_argparser
import gerrit
import kernel_cl_dispatch
import pytest


CHANGE_ID: Final[str] = "I657462dfcec2969cc00d2804922b71587094c87h"
REVISION: Final[str] = "deadbeef"
REMOTE_NAME: Final[str] = "origin"

PENDING_CHANGES = {
    REVISION: gerrit.GerritClInfo(change_id=CHANGE_ID, hashtags="", ref="REF")
}


def cons_default_upstream_config() -> copybot_argparser.UpstreamConfig:
    return copybot_argparser.UpstreamConfig(
        history_limit=250,
        history_starts_with="",
        url="https://chromium.googlesource.com/chromiumos/a",
        branch="main",
        subtree="",
        head_sha=REVISION,
        history_length=0,
        repo=GitRepoMock(),
        remote_name="upstream",
    )


def cons_default_downstream_config(
    remote_name: str = "downstream",
    repo: gerrit.GitRepoInterface | None = None,
    url: str = "https://chromium.googlesource.com/chromiumos/b",
    history_starts_with: str = REVISION,
    cl_dispatcher_history_starts_with: str = "219d54332a09e",
    is_local: bool = False,
) -> copybot_argparser.DownstreamConfig:
    return copybot_argparser.DownstreamConfig(
        history_limit=250,
        history_starts_with=history_starts_with,
        url=url,
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
        remove_subject_prefix="",
        insert_into_msg={},
        keep_pseudoheaders=[],
        limit=200,
        include_paths=[],
        is_local=is_local,
        repo=repo or GitRepoMock(),
        remote_name=remote_name,
        cl_dispatcher_history_starts_with=cl_dispatcher_history_starts_with,
    )


def cons_default_copybot_config() -> copybot_argparser.CopybotConfig:
    upstream_config = cons_default_upstream_config()
    downstream_config = cons_default_downstream_config()
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
        downstreams=[downstream_config],
        upstream=upstream_config,
        generate_config=False,
        config_file_path="test_path.ini",
        enable_kernel_cl_dispatcher=False,
        first_unmerged=False,
        add_pseudoheaders=[],
        ignore_change_id=False,
        gen_luci_jobs=False,
        commit_message_formatting=None,
        first_parent=True,
    )
    return copybot_config


def create_commit(
    path: pathlib.Path,
    repo: gerrit.GitRepo,
    filename_to_create: str,
    commit_msg: str | None = None,
) -> tuple[str, str]:
    commit_msg = commit_msg or f"CHROMIUM: Add {filename_to_create}"

    (path / filename_to_create).write_text(filename_to_create)
    repo.add([filename_to_create])
    repo.commit(message=commit_msg)
    commit_hash = repo.rev_parse()
    return commit_msg, commit_hash


class GitRepoMock:
    """GitRepo mock for testing purposes."""

    # Mocked methods to allow for call assertions
    add = None
    commit = None
    first_parent: bool = True

    def __init__(
        self,
        git_dir: Union[str, "os.PathLike[str]"] = "",
        first_parent: bool = True,
    ) -> None:
        self.git_dir = pathlib.Path(git_dir)
        self.first_parent = first_parent

        self.add = mock.Mock()
        self.commit = mock.Mock()

    def rev_parse(self, rev: str = "HEAD") -> str:
        del rev
        return REVISION

    def fetch(self, *unused_args, **unused_kwargs) -> str:
        return "fetched_commit_sha"

    def checkout(self, ref: str, *unused_args: list[str]) -> None:
        del ref

    def log(self, *unused_args, **unused_kwargs) -> str:
        return "log_result"

    def log_raw(self, *unused_args) -> str:
        return "raw_log_result"

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
        return ["file.c"]

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

    def reset_hard(self) -> None:
        pass

    def cherry_pick_abort(self) -> None:
        pass

    def get_cl_count(
        self,
        *unused_args,
        **unused_kwargs,
    ) -> int:
        return 1

    def add_remote(
        self,
        url: str,
        name: str,
    ) -> None:
        del url
        del name

    def is_merge_commit(
        self,
        rev: str,
    ) -> bool:
        del rev
        return False


class GerritMock:
    """Gerrit mock for testing purposes."""

    def __init__(self, hostname: str) -> None:
        self.hostname = hostname

    def find_pending_changes(
        self, *unused_args, **unused_kwargs
    ) -> Tuple[Dict[str, gerrit.GerritClInfo], Dict[str, gerrit.GerritClInfo]]:
        return PENDING_CHANGES, {}

    def adjust_hashtags(self, *unused_args, **unused_kwargs) -> None:
        return


@mock.patch("gerrit.GitRepo", GitRepoMock)
def test_parse_copybot_config_from_file(tmp_path):
    """Tests parsing a config from a file."""
    argv = ["--config", "tests/test_config.ini"]
    config = copybot_argparser.parse_copybot_config(tmp_path, argv)

    assert config.topic == "copybot-downstream"
    assert config.merge_conflict_behavior == gerrit.MergeConflictBehavior.STOP
    assert len(config.downstreams) == 1
    downstream = config.downstreams[0]
    assert (
        downstream.url
        == "https://chromium.googlesource.com/chromiumos/downstream"
    )
    assert downstream.branch == "main"
    assert downstream.subtree == "subtree"
    assert downstream.labels == [
        "Verified+1",
        "Bot-Commit+1",
        "Commit-Queue+2",
    ]
    assert downstream.reviewers == ["example@gmail.com"]
    assert downstream.push_options == [
        "uploadvalidator~skip",
        "nokeycheck",
    ]
    assert downstream.keep_pseudoheaders == ["Cq-Depend"]
    assert downstream.history_limit == 1000
    assert downstream.history_starts_with == "ebebebeb"

    upstream = config.upstream
    assert (
        upstream.url == "https://chromium.googlesource.com/chromiumos/upstream"
    )
    assert upstream.branch == "main"
    assert upstream.subtree == ""
    assert upstream.history_starts_with == "deadbeef"


@mock.patch("gerrit.GitRepo", GitRepoMock)
def test_parsing_all_commited_config_files():
    """Smoke-test parsing all config files that are in the repository."""
    config_dir = pathlib.Path(__file__).parent / "config"
    for config_file in config_dir.glob("**/*.ini"):
        if "config_manager" in str(config_file) or "group_config" in str(
            config_file
        ):
            # skip meta config files
            continue

        with tempfile.TemporaryDirectory() as tmp_dir:
            argv = ["--config", str(config_file)]
            try:
                copybot_argparser.parse_copybot_config(
                    pathlib.Path(tmp_dir), argv
                )
            except Exception as e:
                raise ValueError(
                    f"Could not parse config file {config_file}"
                ) from e


@mock.patch("gerrit.GitRepo", GitRepoMock)
def test_parse_copybot_config_from_file__downstreams(tmp_path):
    """Tests parsing a config from a file."""
    argv = ["--config", "tests/test_config_downstreams.ini"]
    config = copybot_argparser.parse_copybot_config(tmp_path, argv)

    assert config.topic == "multiple-downstreams"
    assert config.downstreams == [
        copybot_argparser.DownstreamConfig(
            history_limit=1000,
            history_starts_with="ebebebeb",
            url="https://chromium.googlesource.com/chromiumos/downstream1",
            branch="main1",
            subtree="subtree1",
            head_sha=None,
            history_length=0,
            repo=config.downstreams[0].repo,
            remote_name="first",
            labels=["Verified+1", "Bot-Commit+1", "Commit-Queue+2"],
            reviewers=["example@gmail.com"],
            ccs=[],
            push_options=["uploadvalidator~skip", "nokeycheck"],
            hashtags=[],
            prepend_subject="",
            remove_subject_prefix="",
            insert_into_msg={},
            keep_pseudoheaders=["Cq-Depend"],
            limit=200,
            include_paths=[],
            is_local=False,
            cl_dispatcher_history_starts_with="",
        ),
        copybot_argparser.DownstreamConfig(
            history_limit=500,
            history_starts_with="customhash123",
            url="https://android.googlesource.com/chromiumos/downstream2",
            branch="main2",
            subtree="subtree2",
            head_sha=None,
            history_length=0,
            repo=config.downstreams[1].repo,
            remote_name="second",
            labels=["Verified+1", "Bot-Commit+1", "Commit-Queue+2"],
            reviewers=["example@gmail.com"],
            ccs=[],
            push_options=["uploadvalidator~skip", "nokeycheck"],
            hashtags=[],
            prepend_subject="",
            remove_subject_prefix="",
            insert_into_msg={},
            keep_pseudoheaders=["Cq-Depend"],
            limit=200,
            include_paths=[],
            is_local=False,
            cl_dispatcher_history_starts_with="dispatcherhash123",
        ),
    ]


@mock.patch("gerrit.GitRepo", GitRepoMock)
def test_parse_copybot_config_from_file__downstreams_as_variable(tmp_path):
    """Tests parsing a config from a file."""
    argv = ["--config", "config/kernel/cl-dispatcher-staging.ini"]
    config = copybot_argparser.parse_copybot_config(tmp_path, argv)

    expected_downstreams = {
        d.remote_name: {"url": f"{d.url}:{d.branch}:{d.subtree}"}
        for d in config.downstreams
    }

    assert (
        expected_downstreams
        == kernel_cl_dispatch.KERNEL_CL_DISPATCHER_DOWNSTREAMS
    )


@mock.patch("copybot.push_changes_to_downstream")
def test_upload_updated_config(mock_push, copybot_config):
    """Test that upload_updated_config correctly commits the config file."""
    copybot_config.config_file_path = "path/to/my_config.ini"

    # Define the expected commit message
    expected_commit_msg = (
        "copybot: Update Config Files\n\n"
        "Auto generated CL by copybot.\n"
        "Update up/downstream history starts with hashes\n\n"
        "BUG=None\nTEST=CQ"
    )

    mock_repo = GitRepoMock()
    copybot.upload_updated_config(copybot_config, config_repo=mock_repo)

    mock_repo.add.assert_called_once_with(["path/to/my_config.ini"])
    mock_repo.commit.assert_called_once()
    actual_commit_msg = mock_repo.commit.call_args[0][0]
    assert actual_commit_msg.startswith(expected_commit_msg)
    assert "\nChange-Id: I" in actual_commit_msg

    # Check that the push was called with correct arguments
    mock_push.assert_called_once()
    called_args = mock_push.call_args[0]
    assert len(called_args) == 3

    called_config = called_args[0]
    called_downstream_config = called_args[1]
    called_skip_cq = called_args[2]

    assert called_config == copybot_config
    assert isinstance(
        called_downstream_config, copybot_argparser.DownstreamConfig
    )
    assert (
        called_downstream_config.url
        == "https://chromium.googlesource.com/copybot"
    )
    assert called_downstream_config.branch == "main"
    assert called_skip_cq is False


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


class TestBackoffDecorator:
    """Tests for backoff decorator."""

    def test_success(self):
        """Test backoff decorator when function succeeds immediately."""
        mock_func = mock.Mock(return_value="success")
        decorated_func = gerrit.backoff(delay=0.1, retries=3)(mock_func)

        result = decorated_func()

        assert result == "success"
        mock_func.assert_called_once()

    def test_non_http_error(self):
        """Test backoff decorator when function raises non-HTTPError."""
        mock_func = mock.Mock(side_effect=ValueError("oops"))
        decorated_func = gerrit.backoff(delay=0.1, retries=3)(mock_func)

        with pytest.raises(ValueError):
            decorated_func()

        mock_func.assert_called_once()

    @mock.patch("time.sleep")
    def test_retry_then_success(self, mock_sleep):
        """Test backoff decorator when function fails then succeeds."""
        http_error = gerrit.requests.exceptions.HTTPError("HTTP Error")
        mock_func = mock.Mock(side_effect=[http_error, http_error, "success"])
        decorated_func = gerrit.backoff(delay=1, retries=3)(mock_func)

        result = decorated_func()

        assert result == "success"
        assert mock_func.call_count == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_has_calls([mock.call(1), mock.call(2)])

    @mock.patch("time.sleep")
    def test_max_retries_reached(self, mock_sleep):
        """Test backoff decorator when function repeatedly raises HTTPError."""
        http_error = gerrit.requests.exceptions.HTTPError("HTTP Error")
        mock_func = mock.Mock(side_effect=http_error)
        decorated_func = gerrit.backoff(delay=1, retries=3)(mock_func)

        with pytest.raises(gerrit.requests.exceptions.HTTPError):
            decorated_func()

        assert mock_func.call_count == 3
        assert mock_sleep.call_count == 2


@pytest.mark.parametrize(
    ["exception", "expected", "warnings"],
    [
        (None, {}, ""),
        (Exception(), {"failure_reason": "FAILURE_UNKNOWN"}, ""),
        (
            gerrit.MergeConflictsError(commits=["deadbeef", "deadd00d"]),
            {
                "failure_reason": "FAILURE_MERGE_CONFLICTS",
                "merge_conflicts": [
                    {"hash": "deadbeef"},
                    {"hash": "deadd00d"},
                ],
            },
            "",
        ),
        (
            None,
            {
                "summary_markdown": "WARNING: gerrit limit 200",
            },
            "WARNING: gerrit limit 200",
        ),
    ],
)
def test_write_json_error(tmp_path, exception, expected, warnings):
    err_out = tmp_path / "err.json"
    copybot.write_json_error(err_out, exception, io.StringIO(warnings))
    assert json.loads(err_out.read_text()) == expected


@mock.patch("gerrit.GitRepo", GitRepoMock)
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


def test_run_copybot__smoke_test(copybot_config) -> None:
    with (tempfile.TemporaryDirectory("_patches") as patch_dir,):
        copybot.run_copybot(
            GerritMock,
            copybot_config,
            patch_dir,
        )


def test_parse_insert_into_msg():
    msg = ["1:Android Bringup: See http://go/android-fw-sync"]
    assert copybot_argparser.parse_insert_into_msg(msg) == {
        1: "Android Bringup: See http://go/android-fw-sync\n",
    }


def test_are_repos_related(copybot_config) -> None:
    assert copybot.are_repos_related(
        copybot_config.upstream, copybot_config.downstreams[0]
    )


def test_get_downstreamed_list(copybot_config) -> None:
    downstreamed_revs = copybot.get_downstreamed_list(
        copybot_config,
        copybot_config.downstreams[0],
        upstream_change_ids={},
    )
    assert downstreamed_revs == [REVISION]


def test_fetch_upstream_change_ids() -> None:
    assert copybot.fetch_upstream_change_ids(GitRepoMock(), [REVISION]) == {
        CHANGE_ID: REVISION
    }


def test_is_copybot_job_skipped(copybot_config) -> None:
    assert not copybot.is_copybot_job_skipped(copybot_config, REVISION)


def test_find_commits_to_copy(copybot_config):
    assert copybot.find_commits_to_copy(
        copybot_config,
        copybot_config.upstream,
        copybot_config.downstreams[0],
        PENDING_CHANGES,
    ) == ([REVISION], {REVISION: ["file.c"]}, {REVISION: []}, [], True, [])


def test_get_downstreamed_list__mapped_changed_id(copybot_config) -> None:
    downstreamed_revs = copybot.get_downstreamed_list(
        copybot_config,
        copybot_config.downstreams[0],
        upstream_change_ids={CHANGE_ID: "deadc0de"},
        include_change_id=True,
    )
    assert downstreamed_revs == [REVISION, "deadc0de"]


def test_rewrite_commit_message(copybot_config) -> None:
    reworded_message, updated_author = copybot.rewrite_commit_message(
        REVISION,
        copybot_config.upstream,
        copybot_config.downstreams[0],
        change_id=CHANGE_ID,
    )
    expected_commit_message = f"""Commit message

Change-Id: {CHANGE_ID}
GitOrigin-RevId: {REVISION}
"""
    expected_author = "Alan Turing<example@gmail.com-copybot-pick>"

    assert reworded_message == expected_commit_message
    assert updated_author == expected_author


def test_rewrite_commit_message_with_placeholders(copybot_config) -> None:
    copybot_config.downstreams[0].insert_into_msg = {
        1: "Cherry-pick of {UPSTREAM_HASH}",
    }
    copybot_config.downstreams[0].prepend_subject = (
        "[Subject Prefix Authored by {UPSTREAM_AUTHOR_NAME}] "
    )

    reworded_message, updated_author = copybot.rewrite_commit_message(
        REVISION,
        copybot_config.upstream,
        copybot_config.downstreams[0],
        change_id=CHANGE_ID,
        additional_pseudoheaders=[
            "Upstream-Author: {UPSTREAM_AUTHOR_NAME} <{UPSTREAM_AUTHOR_EMAIL}>"
        ],
    )

    expected_commit_message = (
        f"[Subject Prefix Authored by Alan Turing] Commit message\n"
        f"Cherry-pick of {REVISION}\n"
        "\n"
        f"Change-Id: {CHANGE_ID}\n"
        f"GitOrigin-RevId: {REVISION}\n"
        "Upstream-Author: Alan Turing <example@gmail.com>\n"
    )
    expected_author = "Alan Turing<example@gmail.com-copybot-pick>"

    assert reworded_message == expected_commit_message
    assert updated_author == expected_author


def test_rewrite_commit_message_with_negative_index(copybot_config) -> None:
    copybot_config.downstreams[0].insert_into_msg = {
        -1: "Appended at EOF with {UPSTREAM_HASH}",
    }

    reworded_message, updated_author = copybot.rewrite_commit_message(
        REVISION,
        copybot_config.upstream,
        copybot_config.downstreams[0],
        change_id=CHANGE_ID,
    )

    expected_commit_message = f"""Commit message

Appended at EOF with {REVISION}

Change-Id: {CHANGE_ID}
GitOrigin-RevId: {REVISION}
"""
    expected_author = "Alan Turing<example@gmail.com-copybot-pick>"

    assert reworded_message == expected_commit_message
    assert updated_author == expected_author


def test_rewrite_commit_message_remove_prefix(copybot_config) -> None:
    copybot_config.downstreams[0].remove_subject_prefix = "Commit "
    reworded_message, updated_author = copybot.rewrite_commit_message(
        REVISION,
        copybot_config.upstream,
        copybot_config.downstreams[0],
        change_id=CHANGE_ID,
    )
    expected_commit_message = f"""message

Change-Id: {CHANGE_ID}
GitOrigin-RevId: {REVISION}
"""
    expected_author = "Alan Turing<example@gmail.com-copybot-pick>"

    assert reworded_message == expected_commit_message
    assert updated_author == expected_author


def test_rewrite_commit_message_remove_prefix_and_prepend(
    copybot_config,
) -> None:
    copybot_config.downstreams[0].remove_subject_prefix = "Commit "
    copybot_config.downstreams[0].prepend_subject = "[PREFIX] "
    reworded_message, updated_author = copybot.rewrite_commit_message(
        REVISION,
        copybot_config.upstream,
        copybot_config.downstreams[0],
        change_id=CHANGE_ID,
    )
    expected_commit_message = f"""[PREFIX] message

Change-Id: {CHANGE_ID}
GitOrigin-RevId: {REVISION}
"""
    expected_author = "Alan Turing<example@gmail.com-copybot-pick>"

    assert reworded_message == expected_commit_message
    assert updated_author == expected_author


def test_rewrite_commit_message_chromeos_to_android(copybot_config) -> None:
    input_message = f"""CHROMIUM: Commit message

BUG=b:123
TEST=did some tests
Change-Id: {CHANGE_ID}
"""

    downstream = copybot_config.downstreams[0]
    downstream.url = "https://arsp.googlesource.com/kernel/common"
    downstream.repo.get_commit_message = mock.Mock(return_value=input_message)
    reworded_message, updated_author = copybot.rewrite_commit_message(
        REVISION,
        copybot_config.upstream,
        downstream,
        change_id=CHANGE_ID,
        commit_message_formatting=(
            copybot_argparser.CommitMessageFormat.CHROMEOS_TO_ANDROID
        ),
    )
    expected_author = "Alan Turing<example@gmail.com-copybot-pick>"
    expected_commit_message = f"""ANDROID: Commit message

Bug: b:123
Test: did some tests
Original-Change-Id: {CHANGE_ID}
GitOrigin-RevId: {REVISION}
Change-Id: {CHANGE_ID}
Signed-off-by: {expected_author}
"""

    assert reworded_message == expected_commit_message
    assert updated_author == expected_author


def test_get_push_refspec(copybot_config) -> None:
    assert copybot.get_push_refspec(
        copybot_config, copybot_config.downstreams[0], skip_cq=False
    ) == (
        "HEAD:refs/for/main%ready,l=gerrit_label,"
        "cc=guy.fieri@example.com,t=copybot,t=copybot_tag"
    )


def test_is_server_gob(copybot_config) -> None:
    assert copybot.is_server_gob(copybot_config.downstreams[0].url)
    assert copybot.is_server_gob(copybot_config.upstream.url)
    assert not copybot.is_server_gob(
        "https://github.com/coq-community/coq-tricks"
    )
    assert copybot.is_server_gob(
        "https://android.googlesource.com/kernel/common"
    )
    assert copybot.is_server_gob(
        "https://partner-android.googlesource.com/kernel/common"
    )
    assert copybot.is_server_gob(
        "https://android-review.googlesource.com/c/kernel/common/+/2000000"
    )
    assert copybot.is_server_gob(
        "https://partner-android-review.googlesource.com"
        "/c/kernel-desktop/private/desktop-google/+/20000"
    )


def test_fetch_history_length(copybot_config) -> None:
    cl_count = 1
    assert (
        copybot.fetch_history_length(copybot_config.downstreams[0], "location")
        == cl_count + 1
    )


def test_find_pending_change_at_bottom_of_stack(copybot_config):
    pending_rev, cl_count = copybot.find_pending_change_at_bottom_of_stack(
        copybot_skip_cls=[],
        commits_to_copy=[REVISION],
        pending_changes=PENDING_CHANGES,
        config=copybot_config,
        downstream=copybot_config.downstreams[0],
    )
    assert (pending_rev, cl_count) == (REVISION, 1)


def test_checkout_downstream_repo__all_pending(copybot_config) -> None:
    with pytest.raises(copybot.NothingToDo):
        copybot.checkout_downstream_repo(
            copybot_config.downstreams[0],
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
        copybot_config.downstreams[0],
        commits_to_copy=[REVISION],
        pending_changes=PENDING_CHANGES,
        cl_count=2,
        pending_rev=REVISION,
    )
    repo_fetch.assert_called_with(copybot_config.downstreams[0].url, "REF")


@mock.patch.object(GitRepoMock, "push")
def test_push_changes_to_downstream(repo_push, copybot_config) -> None:
    copybot.push_changes_to_downstream(
        copybot_config,
        copybot_config.downstreams[0],
        skip_cq=False,
    )
    push_refspec = (
        "HEAD:refs/for/main%ready,l=gerrit_label,"
        "cc=guy.fieri@example.com,t=copybot,t=copybot_tag"
    )
    repo_push.assert_called_with(
        copybot_config.downstreams[0].url,
        push_refspec,
        options=copybot_config.downstreams[0].push_options,
    )


class TestGenerateConfig:
    """Tests for generate_config function."""

    CONFIG_FILE_PATH = "./config/coreboot/main.ini"

    def setup_method(self):
        """Set up for test cases, create a temporary directory"""
        # pylint: disable=attribute-defined-outside-init
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = os.path.join(self.temp_dir, "test_config.cfg")

    def teardown_method(self):
        """Tear down after test cases, remove the temporary directory"""
        if self.temp_dir:
            shutil.rmtree(self.temp_dir)

    def test_config_string_argument(self) -> None:
        """Test with an invalid string argument."""
        with pytest.raises(SystemExit):
            copybot_argparser.generate_config(
                [
                    "--generate-config",
                    self.config_file,
                    "--some-string",
                    "another_string",
                ]
            )
            assert not os.path.exists(self.config_file)

    def test_config_argument(self) -> None:
        """Test that config argument is not written."""

        with open(self.CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
            expected_content = f.read()

        copybot_argparser.generate_config(
            [
                "--generate-config",
                self.config_file,
                "--config",
                self.CONFIG_FILE_PATH,
            ]
        )
        with open(self.config_file, "r", encoding="utf-8") as f:
            generated_content = f.read()

        assert generated_content == expected_content

    def test_config_command_line_args(self) -> None:
        """Test that the generated config file path is correct."""
        copybot_argparser.generate_config(
            [
                "--generate-config",
                self.config_file,
                "--label",
                "copybot-downstream",
                "--upstream-url",
                "upstream",
                "--downstream-url",
                "downstream",
            ]
        )
        assert os.path.exists(self.config_file)
        with open(self.config_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert content == (
            '[copybot]\nlabel = ["copybot-downstream"]\n'
            'upstream-url = "upstream"\ndownstream-url = "downstream"\n'
        )

    def test_config_default_not_written(self) -> None:
        """Test that the generated config file does not contain defaults."""
        copybot_argparser.generate_config(
            [
                "--generate-config",
                self.config_file,
                "--label",
                "copybot-downstream",
                "--limit",
                "200",
                "--upstream-url",
                "upstream",
                "--downstream-url",
                "downstream",
            ]
        )
        assert os.path.exists(self.config_file)
        with open(self.config_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert content == (
            '[copybot]\nlabel = ["copybot-downstream"]\n'
            'upstream-url = "upstream"\ndownstream-url = "downstream"\n'
        )

    def test_config_file_path(self) -> None:
        """Test that the generated config file path is correct."""
        copybot_argparser.generate_config(
            [
                "--generate-config",
                self.config_file,
                "--upstream-url",
                "upstream",
                "--downstream-url",
                "downstream",
            ]
        )
        assert os.path.exists(self.config_file)
        with open(self.config_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert content == (
            '[copybot]\nupstream-url = "upstream"\n'
            'downstream-url = "downstream"\n'
        )

    def test_config_existing_directory(self) -> None:
        """Test that it works correctly when the directory already exists."""
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
        copybot_argparser.generate_config(
            [
                "--generate-config",
                self.config_file,
                "--upstream-url",
                "upstream",
                "--downstream-url",
                "downstream",
            ]
        )
        with open(self.config_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert content == (
            '[copybot]\nupstream-url = "upstream"\n'
            'downstream-url = "downstream"\n'
        )

    def test_config_without_upstream_url(self) -> None:
        """Test that generate config fails without upstream url."""
        with pytest.raises(SystemExit):
            copybot_argparser.generate_config(
                [
                    "--generate-config",
                    self.config_file,
                    "--downstream-url",
                    "downstream",
                ]
            )

    def test_config_without_downstream_url(self) -> None:
        """Test that generate config fails without downstream url."""
        with pytest.raises(SystemExit):
            copybot_argparser.generate_config(
                [
                    "--generate-config",
                    self.config_file,
                    "--upstream-url",
                    "upstream",
                ]
            )

    def test_config_downstreams(self) -> None:
        """Test that parsing downstream dictionaries behave as expected."""
        config_file_path = "./tests/test_config_downstreams.ini"

        with open(config_file_path, "r", encoding="utf-8") as f:
            expected_content = f.read()

        copybot_argparser.generate_config(
            [
                "--generate-config",
                self.config_file,
                "--config",
                config_file_path,
            ]
        )
        with open(self.config_file, "r", encoding="utf-8") as f:
            generated_content = f.read()

        assert generated_content == expected_content

    def test_update_downstream_history_starts_with(self) -> None:
        """Test that history-starts-with is updated in the config file."""
        test_config_path = "tests/test_config.ini"
        new_hash = "new_hash"

        copybot_argparser.generate_config(
            [
                "--config",
                test_config_path,
                "--generate-config",
                self.config_file,
                "--downstream-history-starts-with",
                new_hash,
            ]
        )

        with open(self.config_file, "r", encoding="utf-8") as f:
            updated_content = f.read()

        assert (
            f'downstream-history-starts-with = "{new_hash}"' in updated_content
        )
        assert (
            'downstream-history-starts-with = "ebebebeb"' not in updated_content
        )
        # Check that other values are still present
        assert 'upstream-history-starts-with = "deadbeef"' in updated_content
        assert 'topic = "copybot-downstream"' in updated_content

    def test_update_multiple_downstream_history_starts_with(self) -> None:
        """Test updating history-starts-with for multiple downstreams."""
        test_config_path = "tests/test_config_downstreams.ini"

        # Parse the initial config
        config = copybot_argparser.parse_copybot_config(
            pathlib.Path(self.temp_dir),
            argv=["--config", test_config_path],
        )

        # Simulate updating the history-starts-with for specific downstreams
        for ds in config.downstreams:
            if ds.remote_name == "first":
                ds.history_starts_with = "new_hash_first"
            elif ds.remote_name == "second":
                ds.history_starts_with = "new_hash_second"
                ds.history_limit = 400

        update_config_args = [
            "--config",
            test_config_path,
            "--generate-config",
            self.config_file,
        ]

        # Re-generate the config with updated downstream objects
        copybot_argparser.generate_config(
            update_config_args, config.downstreams
        )

        with open(self.config_file, "r", encoding="utf-8") as f:
            updated_content = f.read()

        first_downstream = """\
{'first': {'url': \
'https://chromium.googlesource.com/chromiumos/downstream1:main1:subtree1', \
'history-starts-with': 'new_hash_first', \
'history-limit': 1000}\
"""
        second_downstream = """\
'second': {'url': \
'https://android.googlesource.com/chromiumos/downstream2:main2:subtree2', \
'history-starts-with': 'new_hash_second', \
'cl-dispatcher-history-starts-with': 'dispatcherhash123', \
'history-limit': 400}\
"""
        assert first_downstream in updated_content
        assert second_downstream in updated_content

        # Ensure the global vars are still present and unchanged
        assert 'downstream-history-starts-with = "ebebebeb"' in updated_content
        assert 'upstream-history-starts-with = "deadbeef"' in updated_content


class TestCopyBotIntegration:
    """Integration tests for the Copybot."""

    @pytest.fixture
    def git_repos(self, tmp_path):
        """Creates upstream and downstream git repos for integration testing."""
        upstream_path = tmp_path / "upstream"
        downstream_path = tmp_path / "downstream"
        upstream_path.mkdir()
        downstream_path.mkdir()

        # Use the actual git repositories
        upstream_repo = gerrit.GitRepo(upstream_path)
        downstream_repo = gerrit.GitRepo(downstream_path)

        _, common_ancestor_hash = create_commit(
            upstream_path, upstream_repo, "initial_commit"
        )
        commit1_msg, commit1_hash = create_commit(
            upstream_path, upstream_repo, "feature_a"
        )
        commit2_msg, commit2_hash = create_commit(
            upstream_path, upstream_repo, "feature_b"
        )

        # Setup downstream repo to start from the common ancestor
        downstream_repo.add_remote(name=REMOTE_NAME, url=str(upstream_path))
        downstream_repo.fetch(REMOTE_NAME)
        downstream_repo.checkout(common_ancestor_hash)
        downstream_repo.checkout("main", "-b")

        return {
            "upstream_path": upstream_path,
            "downstream_path": downstream_path,
            "upstream_repo": upstream_repo,
            "downstream_repo": downstream_repo,
            "commits_to_copy": [commit1_hash, commit2_hash],
            "commit_messages": [commit1_msg, commit2_msg],
            "common_ancestor_hash": common_ancestor_hash,
        }

    @mock.patch("copybot.upload_updated_config")
    def test_copybot_e2e(self, mock_upload_config, git_repos, copybot_config):
        """Tests a standard run of the copybot script."""
        copybot_config.dry_run = True

        copybot_config.upstream.repo = git_repos["upstream_repo"]
        copybot_config.upstream.url = str(git_repos["upstream_path"])
        copybot_config.upstream.history_starts_with = git_repos[
            "common_ancestor_hash"
        ]

        downstream_config = copybot_config.downstreams[0]
        downstream_config.repo = git_repos["downstream_repo"]
        downstream_config.url = str(git_repos["downstream_path"])
        downstream_config.history_starts_with = git_repos[
            "common_ancestor_hash"
        ]
        downstream_config.is_local = True

        # Run copybot
        with tempfile.TemporaryDirectory("_patches_e2e") as patch_dir:
            copybot.run_copybot(GerritMock, copybot_config, patch_dir)

        # Ensure config is not updated in this test
        mock_upload_config.assert_not_called()

        # Take git log of downstream repo
        log_hashes = git_repos["downstream_repo"].log_hashes(
            revision_range="HEAD"
        )
        log_hashes.reverse()  # Order from oldest to newest

        assert len(log_hashes) == 3, "There should be exactly 3 commits"
        assert (
            log_hashes[0] == git_repos["common_ancestor_hash"]
        ), "The log should start with the initial commit"

        # Check the first copied commit
        msg1 = git_repos["downstream_repo"].get_commit_message(log_hashes[1])
        origin_rev1 = gerrit.get_origin_rev_id(msg1)
        change_id1 = gerrit.get_change_id(msg1)
        assert origin_rev1 == git_repos["commits_to_copy"][0]
        assert change_id1, "A new Change-Id should have been generated"
        assert git_repos["commit_messages"][0].strip() in msg1

        # Check the second copied commit
        msg2 = git_repos["downstream_repo"].get_commit_message(log_hashes[2])
        origin_rev2 = gerrit.get_origin_rev_id(msg2)
        change_id2 = gerrit.get_change_id(msg2)
        assert origin_rev2 == git_repos["commits_to_copy"][1]
        assert change_id2, "A new Change-Id should have been generated"
        assert git_repos["commit_messages"][1].strip() in msg2

        assert change_id1 != change_id2, "Change-Ids should be unique"

    @pytest.fixture
    def git_repos_for_filtering(self, tmp_path):
        """Creates repos for testing file filtering."""
        upstream_path = tmp_path / "upstream"
        downstream_path = tmp_path / "downstream"
        upstream_path.mkdir()
        downstream_path.mkdir()

        upstream_repo = gerrit.GitRepo(upstream_path)
        downstream_repo = gerrit.GitRepo(downstream_path)

        _, common_ancestor_hash = create_commit(
            upstream_path, upstream_repo, "initial_commit"
        )

        # Create a commit that modifies one file to be kept and one file
        # to be filtered out.
        (upstream_path / "src").mkdir()
        (upstream_path / "docs").mkdir()
        (upstream_path / "src" / "feature.c").write_text(
            "int main() { return 0; }"
        )
        (upstream_path / "docs" / "guide.md").write_text("# Documentation")
        upstream_repo.add(["."])
        upstream_repo.commit(message="CHROMIUM: Add new feature with docs")
        commit_hash = upstream_repo.rev_parse()

        # Setup downstream repo
        downstream_repo.add_remote(name=REMOTE_NAME, url=str(upstream_path))
        downstream_repo.fetch(REMOTE_NAME)
        downstream_repo.checkout(common_ancestor_hash)
        downstream_repo.checkout("main", "-b")

        return {
            "upstream_repo": upstream_repo,
            "downstream_repo": downstream_repo,
            "commit_to_copy": commit_hash,
            "common_ancestor_hash": common_ancestor_hash,
        }

    @mock.patch("copybot.upload_updated_config")
    def test_copybot_e2e_with_file_filtering(
        self, mock_upload_config, git_repos_for_filtering, copybot_config
    ):
        """Tests that files can be filtered out of a commit."""
        git_repos = git_repos_for_filtering
        copybot_config.dry_run = True
        copybot_config.filter_file_patterns = [r"docs/.*"]

        copybot_config.upstream.repo = git_repos["upstream_repo"]
        copybot_config.upstream.url = str(git_repos["upstream_repo"].git_dir)
        copybot_config.upstream.history_starts_with = git_repos[
            "common_ancestor_hash"
        ]

        downstream_config = copybot_config.downstreams[0]
        downstream_config.repo = git_repos["downstream_repo"]
        downstream_config.url = str(git_repos["downstream_repo"].git_dir)
        downstream_config.history_starts_with = git_repos[
            "common_ancestor_hash"
        ]
        downstream_config.is_local = True

        # Run copybot
        with tempfile.TemporaryDirectory("_patches_e2e_filter") as patch_dir:
            copybot.run_copybot(GerritMock, copybot_config, patch_dir)

        # Ensure config is not updated in this test
        mock_upload_config.assert_not_called()

        # Take git log of downstream repo
        log_hashes = git_repos["downstream_repo"].log_hashes()
        log_hashes.reverse()

        assert len(log_hashes) == 2, "There should be exactly 2 commits"
        assert (
            log_hashes[0] == git_repos["common_ancestor_hash"]
        ), "The log should start with the initial commit"

        new_commit_hash = log_hashes[1]
        new_msg = git_repos["downstream_repo"].get_commit_message(
            new_commit_hash
        )

        # Verify the commit message includes the skipped file pseudoheader
        assert "CopyBot-Skipped-File: docs/guide.md" in new_msg

        # Verify that only the unfiltered file was part of the new commit
        changed_files = git_repos["downstream_repo"].commit_file_list(
            new_commit_hash
        )
        assert "src/feature.c" in changed_files
        assert "docs/guide.md" not in changed_files
        assert (
            len(changed_files) == 1
        ), "Only one file should have been committed"

        # Verify the origin revision ID is correct
        origin_rev = gerrit.get_origin_rev_id(new_msg)
        assert origin_rev == git_repos["commit_to_copy"]


@mock.patch("gerrit.GitRepo", GitRepoMock)
def test_first_parent_config(tmp_path):
    """Test first-parent flag parsing from CLI and INI."""
    base_args = [
        "--upstream-url",
        "https://example.com/upstream:main:",
        "--downstream-url",
        "https://example.com/downstream:main:",
    ]
    # Default is True
    config = copybot_argparser.parse_copybot_config(tmp_path, base_args)
    assert config.first_parent is True

    # CLI --no-first-parent
    config = copybot_argparser.parse_copybot_config(
        tmp_path, base_args + ["--no-first-parent"]
    )
    assert config.first_parent is False

    # CLI --first-parent
    config = copybot_argparser.parse_copybot_config(
        tmp_path, base_args + ["--first-parent"]
    )
    assert config.first_parent is True

    # INI first-parent = false
    ini_file = tmp_path / "test_fp.ini"
    ini_file.write_text(
        "[copybot]\n"
        "upstream-url = https://example.com/upstream:main:\n"
        "downstream-url = https://example.com/downstream:main:\n"
        "first-parent = false\n"
    )
    config = copybot_argparser.parse_copybot_config(
        tmp_path, ["--config", str(ini_file)]
    )
    assert config.first_parent is False


def test_git_repo_first_parent_commands(tmp_path):
    """Test that GitRepo commands use --first-parent appropriately."""
    repo_true = gerrit.GitRepo(tmp_path, first_parent=True)
    with mock.patch.object(repo_true, "_run_git") as mock_run:
        mock_run.return_value.stdout = "1\n"
        repo_true.log(revision_range="HEAD")
        args = mock_run.call_args[0]
        assert "--first-parent" in args
        assert "--topo-order" not in args

        mock_run.reset_mock()
        repo_true.get_cl_count("revA", "revB")
        args = mock_run.call_args[0]
        assert "--first-parent" in args

    repo_false = gerrit.GitRepo(tmp_path, first_parent=False)
    with mock.patch.object(repo_false, "_run_git") as mock_run:
        mock_run.return_value.stdout = "1\n"
        repo_false.log(revision_range="HEAD")
        args = mock_run.call_args[0]
        assert "--first-parent" not in args
        assert "--topo-order" in args

        mock_run.reset_mock()
        repo_false.get_cl_count("revA", "revB")
        args = mock_run.call_args[0]
        assert "--first-parent" not in args
