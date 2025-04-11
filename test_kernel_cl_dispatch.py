# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# Accessing private members is fine in testing
# pylint: disable=protected-access

"""Unit tests for kernel_cl_dispatch.py module."""

from unittest import mock

import kernel_cl_dispatch
import pytest
import test_copybot


@pytest.fixture(name="upstream_config")
def upstream_config_fixture():
    config = test_copybot.cons_default_upstream_config()
    config.repo = mock.MagicMock()
    return config


def test_unravel_stable_tags_single() -> None:
    tags = ["chromeos-5.4"]
    expected = ["chromeos-5.4"]
    assert list(kernel_cl_dispatch._unravel_stable_tags(tags)) == expected


def test_unravel_stable_tags_group() -> None:
    tags = ["chromeos-all"]
    expected = kernel_cl_dispatch.CHROMEOS_STABLE_TAGS
    assert sorted(
        list(kernel_cl_dispatch._unravel_stable_tags(tags))
    ) == sorted(expected)


def test_unravel_stable_tags_mixed() -> None:
    tags = ["chromeos-5.4", "android-desktop-all", "chromeos-6.12"]
    expected = (
        ["chromeos-5.4"]
        + kernel_cl_dispatch.ANDROID_DESKTOP_STABLE_TAGS
        + ["chromeos-6.12"]
    )
    assert sorted(
        list(kernel_cl_dispatch._unravel_stable_tags(tags))
    ) == sorted(expected)


def test_unravel_stable_tags_nested_group() -> None:
    kernel_cl_dispatch.GROUPS_MAPPING["nested-all"] = ["chromeos-all"]
    tags = ["nested-all"]
    expected = kernel_cl_dispatch.CHROMEOS_STABLE_TAGS
    assert sorted(
        list(kernel_cl_dispatch._unravel_stable_tags(tags))
    ) == sorted(expected)
    del kernel_cl_dispatch.GROUPS_MAPPING["nested-all"]  # Clean up


def test_parse_kernel_dispatching_tags_single_stable() -> None:
    commit_message = "Subject: Test commit\n\nSTABLE=chromeos-5.4\n"
    actual_stable, actual_fixes = (
        kernel_cl_dispatch._parse_kernel_dispatching_tags(commit_message)
    )
    assert actual_stable == {"chromeos-5.4"}
    assert actual_fixes is None


def test_parse_kernel_dispatching_tags_multiple_stable() -> None:
    commit_message = (
        "Subject: Test commit\n\nSTABLE=chromeos-5.4,   chromeos-6.1 \n"
    )
    actual_stable, actual_fixes = (
        kernel_cl_dispatch._parse_kernel_dispatching_tags(commit_message)
    )
    assert actual_stable == {"chromeos-5.4", "chromeos-6.1"}
    assert actual_fixes is None


def test_parse_kernel_dispatching_tags_mixed_stable() -> None:
    commit_message = (
        "Subject: Test commit\n\nSTABLE=chromeos-5.4, android-desktop-all\n"
    )
    expected_stable = {"chromeos-5.4"} | set(
        kernel_cl_dispatch.ANDROID_DESKTOP_STABLE_TAGS
    )
    actual_stable, actual_fixes = (
        kernel_cl_dispatch._parse_kernel_dispatching_tags(commit_message)
    )
    assert actual_stable == expected_stable
    assert actual_fixes is None


def test_parse_kernel_dispatching_tags_stable_with_fixes() -> None:
    commit_message = """
    Subject: Test commit

STABLE=chromeos-5.4
FIXES=86e5d3e6b77f CHROMIUM: Very important change
    """
    actual_stable, actual_fixes = (
        kernel_cl_dispatch._parse_kernel_dispatching_tags(commit_message)
    )
    assert actual_stable == {"chromeos-5.4"}
    assert actual_fixes == "CHROMIUM: Very important change"


def test_parse_kernel_dispatching_tags_no_stable() -> None:
    commit_message = "Subject: Test commit\n\n"
    actual_stable, actual_fixes = (
        kernel_cl_dispatch._parse_kernel_dispatching_tags(commit_message)
    )
    assert actual_stable == set(), actual_fixes == ""


def test_parse_kernel_dispatching_tags_unknown_tag() -> None:
    commit_message = "Subject: Test commit\n\nSTABLE=unknown-tag"
    actual_stable, actual_fixes = (
        kernel_cl_dispatch._parse_kernel_dispatching_tags(commit_message)
    )
    assert actual_stable == set(), actual_fixes == ""


def test_select_kernel_cl_dispatching_locations_no_dispatching(
    upstream_config,
) -> None:
    upstream_config.repo.get_commit_message.return_value = (
        "Subject: Test commit\n\nSTABLE=N/A\n"
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
        "Subject: Test commit\n\nSTABLE=chromeos-5.4\n"
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

STABLE=chromeos-5.4, android-mainline-desktop-core
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
        "Subject: Test commit\n\nSTABLE=chromeos-all\n"
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

STABLE=chromeos-all
FIXES=86e5d3e6b77f CHROMIUM: Very important change
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

STABLE=chromeos-all
FIXES=86e5d3e6b77f CHROMIUM: Very important change
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
        "Subject: Test commit\n\nSTABLE=unknown-tag\n"
    )
    downstream_configs = [
        test_copybot.cons_default_downstream_config(remote_name="chromeos-5.4"),
    ]
    result = kernel_cl_dispatch.select_kernel_cl_dispatching_locations(
        upstream_config, downstream_configs, "test_rev"
    )
    assert result == []


def test_select_kernel_cl_dispatching_locations_empty_stable_tag(
    upstream_config,
) -> None:
    upstream_config.repo.get_commit_message.return_value = (
        "Subject: Test commit\n\nSTABLE=\n"
    )
    downstream_configs = [
        test_copybot.cons_default_downstream_config(remote_name="chromeos-5.4"),
    ]
    result = kernel_cl_dispatch.select_kernel_cl_dispatching_locations(
        upstream_config, downstream_configs, "test_rev"
    )
    assert result == []
