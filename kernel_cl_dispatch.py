# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""ChromeOS Kernel CL Dispatcher library.

A library adding support for dispatching kernel bug fixes from ChromeOS, to:
- Other ChromeOS kernel versions
- Android Desktop kernel
based on git trailers appended to the commit message.

Existence and validity of the trailers is checked already by a linter.

ChromiumOS kernel commits have a new trailer in the commit message: Branches.
It lists all kernel branches that the commit should be backported to. In case
it's a feature, or when no such branches exist, the Branches tag is set to N/A.

If Branches tag is non-empty (!= N/A), an additional Fixes trailer is required.
Similarly to its upstream counterpart, it points to the SHA & title of the
commit being fixed by the patch. For the Branches tag set to a group, the Fixes
tag would determine whether the commit is cherry-picked to a given branch.

Using the information held by the new tags, changes to the ChromiumOS and
Android kernel branches (for example android-mainline-desktop) specified by the
Branches tag will be opened automatically.

See: go/kernel-cl-dispatch for more details.
"""

from collections.abc import Iterable, Iterator
import logging
from typing import Literal

import copybot_argparser
import gerrit


logger = logging.getLogger(__name__)

BRANCHES_TAG: Literal["Branches"] = "Branches"
FIXES_TAG: Literal["Fixes"] = "Fixes"
FixesTagT = str | None

CHROMEOS_BRANCHES_TAGS: list[str] = [
    # All deployed versions (go/cros-kernel-versions)
    "chromeos-5.4",
    "chromeos-5.10",
    "chromeos-5.15",
    "chromeos-6.1",
    "chromeos-6.6",
    "chromeos-6.12",
]

ANDROID_DESKTOP_BRANCHES_TAGS: list[str] = [
    "android-mainline-desktop-core",
    "android-mainline-desktop-vendor",
    "android15-6.6-desktop-core",
    "android15-6.6-desktop-vendor",
    "android16-6.12-desktop-core",
    "android16-6.12-desktop-vendor",
]

# Branches tag that controls kernel CL dispatching may not only point directly
# to a branch, but also can specify a group as a target. We define the semantics
# of groups and supported values here:
GROUPS_MAPPING = {
    "all": CHROMEOS_BRANCHES_TAGS + ANDROID_DESKTOP_BRANCHES_TAGS,
    "chromeos-all": CHROMEOS_BRANCHES_TAGS,
    "android-desktop-all": ANDROID_DESKTOP_BRANCHES_TAGS,
}

NO_DISPATCHING_NEEDED_TAGS = {
    "n/a",
    "N/A",
}

SUPPORTED_BRANCHES_TAG_VALUES: set[str] = {
    *NO_DISPATCHING_NEEDED_TAGS,
    *CHROMEOS_BRANCHES_TAGS,
    *ANDROID_DESKTOP_BRANCHES_TAGS,
    *GROUPS_MAPPING.keys(),
}


def _unravel_branches_tags(branches_tags: Iterable[str]) -> Iterator[str]:
    """Unravel & flatten group mappings in stable tags into branches."""
    for branches_tag in branches_tags:
        if branches_tag in GROUPS_MAPPING:
            yield from _unravel_branches_tags(GROUPS_MAPPING[branches_tag])
        elif branches_tag in SUPPORTED_BRANCHES_TAG_VALUES:
            yield branches_tag
        else:
            logger.error("Unsupported Branches tag value: %s", branches_tag)


def _parse_kernel_dispatching_tags(
    commit_message: str,
) -> tuple[set[str], FixesTagT]:
    """Parse Branches and Fixes git trailers from commit message.

    The expected format for:
    * Branches - a comma-separated list of target branches and groups,
    * Fixes - SHA followed by commit message in parentheses and quotes,
    for example: 86e5d3e6b77f ("CHROMIUM: Bug fix")
    """
    (
        pseudoheaders,
        commit_message,
    ) = gerrit.Pseudoheaders.from_commit_message(commit_message, separator=":")

    branches_tag_value = pseudoheaders.get(BRANCHES_TAG)
    branches_tags = (
        set(
            _unravel_branches_tags(
                [tag.strip() for tag in branches_tag_value.split(",")]
            )
        )
        if branches_tag_value
        else set()
    )

    fixes_tag = pseudoheaders.get(FIXES_TAG).strip()
    try:
        fixes_commit_message = fixes_tag.split('"')[1]
    except (IndexError, AttributeError):
        logger.warning("Unsupported format or empty Fixes tag: %s", fixes_tag)
        fixes_commit_message = None
    return branches_tags, fixes_commit_message


def _validate_remote_name_match_stable_values(
    downstream: copybot_argparser.DownstreamConfig,
) -> None:
    """Ensure that downstream's remote names are in supported stable tags."""
    if downstream.remote_name not in SUPPORTED_BRANCHES_TAG_VALUES:
        raise ValueError(
            "Downstream remote names in Kernel CL Dispatching use case should "
            "match supported Branches tag values. Invalid remote name on: "
            f"{downstream}"
        )


def _location_contains_fixed_commit(
    fixes_tag: str,
    downstream: copybot_argparser.DownstreamConfig,
) -> bool:
    """Return whether a location contains the patch mentioned by Fixes tag."""
    if downstream.cl_dispatcher_history_starts_with:
        revision_range = f"{downstream.cl_dispatcher_history_starts_with}..HEAD"
        grep_results = downstream.repo.log_raw(
            "--format=%s",
            "--ancestry-path",
            revision_range,
            "--grep",
            f"{fixes_tag}$",
        )
    else:
        grep_results = downstream.repo.log_raw(
            f"{downstream.remote_name}/{downstream.branch}",
            "--first-parent",
            "--format=%s",
            "--grep",
            f"{fixes_tag}$",
        )
    if not grep_results:
        logger.info(
            '[Kernel CL Dispatcher] Could not find in commit "%s" in %s',
            fixes_tag,
            downstream,
        )
    return bool(grep_results)


def should_rev_be_dispatched_to_location(
    upstream: copybot_argparser.UpstreamConfig,
    downstream: copybot_argparser.DownstreamConfig,
    upstream_rev: str,
) -> bool:
    """Return if a patch should be dispatched to a given downstream."""
    _validate_remote_name_match_stable_values(downstream)

    commit_message = upstream.repo.get_commit_message(upstream_rev)
    branches_tags, fixes_tag = _parse_kernel_dispatching_tags(commit_message)

    if branches_tags & NO_DISPATCHING_NEEDED_TAGS:
        # N/A tag was set, nothing to do
        return False

    is_eligible = downstream.remote_name in branches_tags and (
        fixes_tag is None
        or _location_contains_fixed_commit(fixes_tag, downstream)
    )
    if is_eligible:
        logger.info(
            "[Kernel CL Dispatcher] Dispatching commit %s from upstream=%s "
            "to the following downstream location: %s",
            upstream_rev,
            upstream,
            downstream,
        )
    return is_eligible


def select_kernel_cl_dispatching_locations(
    upstream: copybot_argparser.UpstreamConfig,
    all_downstream_locations: list[copybot_argparser.DownstreamConfig],
    upstream_rev: str,
) -> list[copybot_argparser.DownstreamConfig]:
    """Select locations that are meant as a target for CL dispatching."""

    return [
        downstream
        for downstream in all_downstream_locations
        if should_rev_be_dispatched_to_location(
            upstream, downstream, upstream_rev
        )
    ]
