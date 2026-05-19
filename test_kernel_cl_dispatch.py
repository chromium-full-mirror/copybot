# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# Accessing private members is fine in testing
# pylint: disable=protected-access

"""Unit tests for kernel_cl_dispatch.py module."""

import logging
import tempfile
from unittest import mock

import copybot
import gerrit
import kernel_cl_dispatch
import pytest
import test_copybot


@pytest.fixture(name="upstream_config")
def upstream_config_fixture():
    config = test_copybot.cons_default_upstream_config()
    config.repo = mock.MagicMock()
    return config


def test_unravel_branches_tags_single() -> None:
    tags = ["chromeos-5.4"]
    expected = ["chromeos-5.4"]
    assert list(kernel_cl_dispatch._unravel_branches_tags(tags)) == expected


def test_unravel_branches_tags_group() -> None:
    tags = ["chromeos-all"]
    expected = kernel_cl_dispatch.CHROMEOS_BRANCHES_TAGS
    assert sorted(
        list(kernel_cl_dispatch._unravel_branches_tags(tags))
    ) == sorted(expected)


def test_unravel_branches_tags_mixed() -> None:
    tags = ["chromeos-5.4", "android-desktop-all", "chromeos-6.12"]
    expected = (
        ["chromeos-5.4"]
        + kernel_cl_dispatch.ANDROID_DESKTOP_BRANCHES_TAGS
        + ["chromeos-6.12"]
    )
    assert sorted(
        list(kernel_cl_dispatch._unravel_branches_tags(tags))
    ) == sorted(expected)


def test_unravel_branches_tags_nested_group() -> None:
    kernel_cl_dispatch.GROUPS_MAPPING["nested-all"] = ["chromeos-all"]
    tags = ["nested-all"]
    expected = kernel_cl_dispatch.CHROMEOS_BRANCHES_TAGS
    assert sorted(
        list(kernel_cl_dispatch._unravel_branches_tags(tags))
    ) == sorted(expected)
    del kernel_cl_dispatch.GROUPS_MAPPING["nested-all"]  # Clean up


def test_parse_kernel_dispatching_tags_single_branches() -> None:
    commit_message = "Subject: Test commit\n\nBranches: chromeos-5.4\n"
    actual_branches, actual_fixes = (
        kernel_cl_dispatch._parse_kernel_dispatching_tags(commit_message)
    )
    assert actual_branches == {"chromeos-5.4"}
    assert actual_fixes is None


def test_parse_kernel_dispatching_tags_multiple_branches() -> None:
    commit_message = (
        "Subject: Test commit\n\nBranches: chromeos-5.4,   chromeos-6.1 \n"
    )
    actual_branches, actual_fixes = (
        kernel_cl_dispatch._parse_kernel_dispatching_tags(commit_message)
    )
    assert actual_branches == {"chromeos-5.4", "chromeos-6.1"}
    assert actual_fixes is None


def test_parse_kernel_dispatching_tags_mixed_branches() -> None:
    commit_message = (
        "Subject: Test commit\n\nBranches: chromeos-5.4, android-desktop-all\n"
    )
    expected_branches = {"chromeos-5.4"} | set(
        kernel_cl_dispatch.ANDROID_DESKTOP_BRANCHES_TAGS
    )
    actual_branches, actual_fixes = (
        kernel_cl_dispatch._parse_kernel_dispatching_tags(commit_message)
    )
    assert actual_branches == expected_branches
    assert actual_fixes is None


def test_parse_kernel_dispatching_tags_branches_with_fixes() -> None:
    commit_message = """
    Subject: Test commit

Branches: chromeos-5.4
Fixes: 86e5d3e6b77f ("CHROMIUM: Very important change")
    """
    actual_branches, actual_fixes = (
        kernel_cl_dispatch._parse_kernel_dispatching_tags(commit_message)
    )
    assert actual_branches == {"chromeos-5.4"}
    assert actual_fixes == "CHROMIUM: Very important change"


def test_parse_kernel_dispatching_tags_no_branches() -> None:
    commit_message = "Subject: Test commit\n\n"
    actual_branches, actual_fixes = (
        kernel_cl_dispatch._parse_kernel_dispatching_tags(commit_message)
    )
    assert actual_branches == set(), actual_fixes == ""


def test_parse_kernel_dispatching_tags_no_branches_tag_does_not_log_error(
    caplog,
) -> None:
    """Verify that parsing a commit without Branches tag does not log error."""
    commit_message = "Subject: A standard commit message."
    with caplog.at_level(logging.ERROR):
        branches_tags, _ = kernel_cl_dispatch._parse_kernel_dispatching_tags(
            commit_message
        )

    assert not branches_tags
    assert "Unsupported Branches tag value" not in caplog.text


def test_parse_kernel_dispatching_tags_unknown_tag() -> None:
    commit_message = "Subject: Test commit\n\nBranches: unknown-tag"
    actual_branches, actual_fixes = (
        kernel_cl_dispatch._parse_kernel_dispatching_tags(commit_message)
    )
    assert actual_branches == set(), actual_fixes == ""


def test_select_kernel_cl_dispatching_locations_no_dispatching(
    upstream_config,
) -> None:
    upstream_config.repo.get_commit_message.return_value = (
        "Subject: Test commit\n\nBranches: N/A\n"
    )
    downstream_configs = [
        test_copybot.cons_default_downstream_config(remote_name="chromeos-5.4"),
        test_copybot.cons_default_downstream_config(
            remote_name="android-mainline-desktop-core"
        ),
    ]
    result = kernel_cl_dispatch.select_kernel_cl_dispatching_locations(
        upstream_config, downstream_configs, "test_rev"
    )
    assert result == []


def test_select_kernel_cl_dispatching_locations_single_match(
    upstream_config,
) -> None:
    upstream_config.repo.get_commit_message.return_value = (
        "Subject: Test commit\n\nBranches: chromeos-5.4\n"
    )
    downstream_configs = [
        test_copybot.cons_default_downstream_config(remote_name="chromeos-5.4"),
        test_copybot.cons_default_downstream_config(
            remote_name="android-mainline-desktop-core"
        ),
    ]
    result = kernel_cl_dispatch.select_kernel_cl_dispatching_locations(
        upstream_config, downstream_configs, "test_rev"
    )
    assert result == downstream_configs[:1]


def test_select_kernel_cl_dispatching_locations_multiple_matches(
    upstream_config,
) -> None:
    upstream_config.repo.get_commit_message.return_value = """
    Subject: Test commit

Branches: chromeos-5.4, android-mainline-desktop-core
    """
    downstream_configs = [
        test_copybot.cons_default_downstream_config(remote_name="chromeos-5.4"),
        test_copybot.cons_default_downstream_config(
            remote_name="android-mainline-desktop-core"
        ),
        test_copybot.cons_default_downstream_config(remote_name="chromeos-6.1"),
    ]
    result = kernel_cl_dispatch.select_kernel_cl_dispatching_locations(
        upstream_config, downstream_configs, "test_rev"
    )
    assert result == downstream_configs[:2]


def test_select_kernel_cl_dispatching_locations__unsupported_downstream_remote(
    upstream_config,
) -> None:
    upstream_config.repo.get_commit_message.return_value = (
        "Subject: Test commit\n\nBranches: chromeos-all\n"
    )
    downstream_configs = [
        test_copybot.cons_default_downstream_config(
            remote_name="chromeos-3.18"
        ),
    ]
    with pytest.raises(ValueError):
        kernel_cl_dispatch.select_kernel_cl_dispatching_locations(
            upstream_config, downstream_configs, "test_rev"
        )


def test_select_kernel_cl_dispatching_locations_group_match(
    upstream_config,
) -> None:
    upstream_config.repo.get_commit_message.return_value = """
    Subject: Test commit

Branches: chromeos-all
Fixes: 86e5d3e6b77f ("CHROMIUM: Very important change")
    """
    downstream_configs = [
        test_copybot.cons_default_downstream_config(
            remote_name="chromeos-5.15"
        ),
        test_copybot.cons_default_downstream_config(remote_name="chromeos-6.1"),
        test_copybot.cons_default_downstream_config(
            remote_name="android-mainline-desktop-core"
        ),
    ]
    result = kernel_cl_dispatch.select_kernel_cl_dispatching_locations(
        upstream_config, downstream_configs, "test_rev"
    )
    assert result == downstream_configs[:2]


def test_select_kernel_cl_dispatching_locations__fixes_not_present(
    upstream_config,
) -> None:
    upstream_config.repo.get_commit_message.return_value = """
    Subject: Test commit

Branches: chromeos-all
Fixes: 86e5d3e6b77f ("CHROMIUM: Very important change")
    """
    downstream_config = test_copybot.cons_default_downstream_config(
        remote_name="chromeos-5.4"
    )
    downstream_config.repo = mock.MagicMock()
    downstream_config.repo.log_raw.return_value = ""
    result = kernel_cl_dispatch.select_kernel_cl_dispatching_locations(
        upstream_config, [downstream_config], "test_rev"
    )
    assert result == []


def test_select_kernel_cl_dispatching_should_skip_unknown_tag(
    upstream_config,
) -> None:
    upstream_config.repo.get_commit_message.return_value = (
        "Subject: Test commit\n\nBranches: unknown-tag\n"
    )
    downstream_configs = [
        test_copybot.cons_default_downstream_config(remote_name="chromeos-5.4"),
    ]
    result = kernel_cl_dispatch.select_kernel_cl_dispatching_locations(
        upstream_config, downstream_configs, "test_rev"
    )
    assert result == []


def test_select_kernel_cl_dispatching_locations_empty_branches_tag(
    upstream_config,
) -> None:
    upstream_config.repo.get_commit_message.return_value = (
        "Subject: Test commit\n\nBranches: \n"
    )
    downstream_configs = [
        test_copybot.cons_default_downstream_config(remote_name="chromeos-5.4"),
    ]
    result = kernel_cl_dispatch.select_kernel_cl_dispatching_locations(
        upstream_config, downstream_configs, "test_rev"
    )
    assert result == []


def test_location_contains_fixed_commit_no_history_start() -> None:
    downstream_config = test_copybot.cons_default_downstream_config(
        remote_name="chromeos-5.4"
    )
    downstream_config.repo = mock.MagicMock()
    downstream_config.cl_dispatcher_history_starts_with = ""

    # Mock grep results to return a value
    downstream_config.repo.log_raw.return_value = "some_hash"

    result = kernel_cl_dispatch._location_contains_fixed_commit(
        "fixes_tag_value", downstream_config
    )

    assert result is True
    downstream_config.repo.log_raw.assert_called_once_with(
        "chromeos-5.4/main",
        "--format=%s",
        "--grep",
        "fixes_tag_value$",
    )


class TestKernelClDispatcherIntegration:
    """Integration tests for the Kernel CL Dispatcher."""

    @pytest.fixture
    def git_repos_for_dispatcher(self, tmp_path):
        """Sets up git repos for testing the dispatcher's FIXES logic."""
        # Setup Upstream
        upstream_path = tmp_path / "upstream"
        upstream_path.mkdir()
        upstream_repo = gerrit.GitRepo(upstream_path)
        _, common_ancestor_hash = test_copybot.create_commit(
            upstream_path,
            upstream_repo,
            "initial_commit",
        )
        feature_commit_msg, feature_commit_hash = test_copybot.create_commit(
            upstream_path,
            upstream_repo,
            "feature",
        )

        fix_commit_msg = f"""CHROMIUM: Fix for feature

Branches: chromeos-all
Fixes: {feature_commit_hash} ("{feature_commit_msg}")
"""
        _, fix_commit_hash = test_copybot.create_commit(
            upstream_path, upstream_repo, "fix", fix_commit_msg
        )

        # Setup Downstream repo 1 (contains the feature commit)
        downstream1_path = tmp_path / "downstream1_chromeos-6.1"
        downstream1_path.mkdir()
        downstream1_repo = gerrit.GitRepo(downstream1_path)
        downstream1_repo.add_remote(url=str(upstream_path), name="origin")
        downstream1_repo.fetch("origin")
        downstream1_repo.checkout(feature_commit_hash)
        downstream1_repo.checkout("main", "-b")

        # Setup Downstream repo 2 (does NOT have the feature commit)
        downstream2_path = tmp_path / "downstream2_chromeos-5.15"
        downstream2_path.mkdir()
        downstream2_repo = gerrit.GitRepo(downstream2_path)
        downstream2_repo.add_remote(url=str(upstream_path), name="origin")
        downstream2_repo.fetch("origin")
        downstream2_repo.checkout(common_ancestor_hash)
        downstream2_repo.checkout("main", "-b")

        return {
            "upstream_repo": upstream_repo,
            "downstream1_repo": downstream1_repo,
            "downstream2_repo": downstream2_repo,
            "fix_commit_hash": fix_commit_hash,
            "common_ancestor_hash": common_ancestor_hash,
        }

    @mock.patch("copybot.upload_updated_config")
    def test_kernel_cl_dispatcher_e2e_with_fixes_tag(
        self, mock_upload_config, git_repos_for_dispatcher, copybot_config
    ):
        """Tests that a fix is dispatched only to correct downstreams.

        Fixes tag is present.
        """
        repos = git_repos_for_dispatcher
        copybot_config.enable_kernel_cl_dispatcher = True
        copybot_config.dry_run = True

        copybot_config.upstream.repo = repos["upstream_repo"]
        copybot_config.upstream.url = str(repos["upstream_repo"].git_dir)
        copybot_config.upstream.history_starts_with = repos[
            "common_ancestor_hash"
        ]

        # Create first downstream config
        downstream1 = test_copybot.cons_default_downstream_config(
            remote_name="chromeos-6.1",
            repo=repos["downstream1_repo"],
            url=str(repos["downstream1_repo"].git_dir),
            history_starts_with=repos["common_ancestor_hash"],
            cl_dispatcher_history_starts_with=repos["common_ancestor_hash"],
            is_local=True,
        )

        # Create second downstream config
        downstream2 = test_copybot.cons_default_downstream_config(
            remote_name="chromeos-5.15",
            repo=repos["downstream2_repo"],
            url=str(repos["downstream2_repo"].git_dir),
            history_starts_with=repos["common_ancestor_hash"],
            cl_dispatcher_history_starts_with=repos["common_ancestor_hash"],
            is_local=True,
        )

        copybot_config.downstreams = [downstream1, downstream2]

        with tempfile.TemporaryDirectory() as patch_dir:
            copybot.run_copybot(
                test_copybot.GerritMock, copybot_config, patch_dir
            )

        mock_upload_config.assert_not_called()

        # Downstream 1 should have received the fix commit
        log1 = repos["downstream1_repo"].log_hashes()
        assert (
            len(log1) == 3
        ), "Downstream 1 should have common ancestor + original + fix"
        new_commit1_msg = repos["downstream1_repo"].get_commit_message(log1[0])
        assert (
            gerrit.get_origin_rev_id(new_commit1_msg)
            == repos["fix_commit_hash"]
        )

        # Downstream 2 should NOT have received the fix commit
        log2 = repos["downstream2_repo"].log_hashes()
        assert len(log2) == 1, "Downstream 2 should only have common ancestor"
        assert log2[0] == repos["common_ancestor_hash"]

    @pytest.fixture
    def repos_for_branches_tag_test(self, tmp_path):
        """Sets up repos for Branches tag dispatching without a FIXES tag."""
        # Setup Upstream
        upstream_path = tmp_path / "upstream"
        upstream_path.mkdir()
        upstream_repo = gerrit.GitRepo(upstream_path)
        _, common_ancestor_hash = test_copybot.create_commit(
            upstream_path, upstream_repo, "initial_commit"
        )
        upstream_repo.checkout("main", "-B")

        commit_msg = """CHROMIUM: Add new feature for selected kernel versions

Branches: chromeos-6.1, android-mainline-desktop-core
"""
        _, new_commit_hash = test_copybot.create_commit(
            upstream_path, upstream_repo, "new_feature", commit_msg
        )

        # 2. Setup Downstream repos using the init + fetch pattern
        downstream1_path = tmp_path / "d1_chromeos-6.1"
        downstream1_path.mkdir()
        downstream1_repo = gerrit.GitRepo(downstream1_path)

        downstream2_path = tmp_path / "d2_android-desktop-core"
        downstream2_path.mkdir()
        downstream2_repo = gerrit.GitRepo(downstream2_path)

        downstream3_path = tmp_path / "d3_chromeos-5.15"
        downstream3_path.mkdir()
        downstream3_repo = gerrit.GitRepo(downstream3_path)

        for repo in (downstream1_repo, downstream2_repo, downstream3_repo):
            repo.add_remote(url=str(upstream_path), name="origin")
            repo.fetch("origin")
            repo.checkout(common_ancestor_hash)
            repo.checkout("main", "-b")

        return {
            "upstream_repo": upstream_repo,
            "downstream_targets": [downstream1_repo, downstream2_repo],
            "downstream_non_target": downstream3_repo,
            "new_commit_hash": new_commit_hash,
            "common_ancestor_hash": common_ancestor_hash,
        }

    @mock.patch("copybot.upload_updated_config")
    def test_kernel_cl_dispatcher_e2e_with_branches_tag(
        self, mock_upload_config, repos_for_branches_tag_test, copybot_config
    ):
        """Tests that a commit is dispatched based on the Branches tag.

        No FIXES tag is present.
        """
        repos = repos_for_branches_tag_test
        copybot_config.enable_kernel_cl_dispatcher = True
        copybot_config.dry_run = True

        copybot_config.upstream.repo = repos["upstream_repo"]
        copybot_config.upstream.url = str(repos["upstream_repo"].git_dir)
        copybot_config.upstream.history_starts_with = repos[
            "common_ancestor_hash"
        ]

        # Create downstream configs
        downstream1 = test_copybot.cons_default_downstream_config(
            remote_name="chromeos-6.1",
            repo=repos["downstream_targets"][0],
            url=str(repos["downstream_targets"][0].git_dir),
            history_starts_with=repos["common_ancestor_hash"],
            is_local=True,
        )
        downstream2 = test_copybot.cons_default_downstream_config(
            remote_name="android-mainline-desktop-core",
            repo=repos["downstream_targets"][1],
            url=str(repos["downstream_targets"][1].git_dir),
            history_starts_with=repos["common_ancestor_hash"],
            is_local=True,
        )
        downstream3 = test_copybot.cons_default_downstream_config(
            remote_name="chromeos-5.15",
            repo=repos["downstream_non_target"],
            url=str(repos["downstream_non_target"].git_dir),
            history_starts_with=repos["common_ancestor_hash"],
            is_local=True,
        )
        copybot_config.downstreams = [downstream1, downstream2, downstream3]

        with tempfile.TemporaryDirectory() as patch_dir:
            copybot.run_copybot(
                test_copybot.GerritMock, copybot_config, patch_dir
            )

        mock_upload_config.assert_not_called()

        # Downstreams 1 and 2 should have received the new commit
        for target_repo in repos["downstream_targets"]:
            log = target_repo.log_hashes()
            assert (
                len(log) == 2
            ), "Target downstream should have ancestor + new commit"
            new_commit_msg = target_repo.get_commit_message(log[0])
            assert (
                gerrit.get_origin_rev_id(new_commit_msg)
                == repos["new_commit_hash"]
            )

        # Downstream 3 should NOT have received the new commit
        log3 = repos["downstream_non_target"].log_hashes()
        assert (
            len(log3) == 1
        ), "Non-target downstream should only have the ancestor"
        assert log3[0] == repos["common_ancestor_hash"]
