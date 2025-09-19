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
    repo.add(filename_to_create)
    repo.commit(message=commit_msg)
    commit_hash = repo.rev_parse()
    return commit_msg, commit_hash


class GitRepoMock:
    """GitRepo mock for testing purposes."""

    def __init__(self, git_dir: Union[str, "os.PathLike[str]"] = "") -> None:
        self.git_dir = pathlib.Path(git_dir)

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
        with pytest.raises(
            copybot.NothingToDo, match=r"All found changes are pending"
        ):
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
    ) == ([REVISION], {REVISION: ["file.c"]}, {REVISION: []}, [], True)


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

    CONFIG_FILE_PATH = "./config/coreboot-main-copybot-downstream.ini"

    def setup_method(self):
        """Set up for test cases, create a temporary directory"""
        # pylint: disable=attribute-defined-outside-init
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = os.path.join(self.temp_dir, "test_config.cfg")

    def teardown_method(self):
        """Tear down after test cases, remove the temporary directory"""
        if self.temp_dir:
            for file_name in os.listdir(self.temp_dir):
                os.remove(os.path.join(self.temp_dir, file_name))
            os.rmdir(self.temp_dir)

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
            "[copybot]\nlabel = [copybot-downstream]\n"
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
            "[copybot]\nlabel = [copybot-downstream]\n"
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
        upstream_repo.add(".")
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
