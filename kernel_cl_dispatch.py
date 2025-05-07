# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""ChromeOS Kernel CL Dispatcher library.

A library adding support for dispatching kernel bug fixes from ChromeOS, to:
- Other ChromeOS kernel versions
- Android Desktop kernel
based on tags in the commit message.

Existence and validity of tags is checked already by a linter.

ChromiumOS kernel commits have a new tag in the commit message: STABLE. The tag
lists all kernel branches that the commit should be backported to. In case it's
a feature, or when no such branches exist, the STABLE is set to N/A.

If STABLE tag is non-empty (!= N/A), an additional FIXES tag is required. It
points to the SHA & title of the commit being fixed by the patch. For the STABLE
tag set to a group, the FIXES tag would determine whether the commit is
cherry-picked to a given branch.

Using the information held by the new tags, changes to the ChromiumOS and
Android kernel branches (for example android-mainline-desktop) specified by the
STABLE tag will be opened automatically.

See: go/kernel-cl-dispatch for more details.
"""

from collections.abc import Iterable, Iterator
import logging
from typing import Literal

import copybot_argparser
import gerrit


logger = logging.getLogger(__name__)

STABLE_TAG: Literal["STABLE"] = "STABLE"
FIXES_TAG: Literal["FIXES"] = "FIXES"
FixesTagT = str | None

CHROMEOS_STABLE_TAGS: list[str] = [
    # All deployed versions (go/cros-kernel-versions)
    "chromeos-5.4",
    "chromeos-5.10",
    "chromeos-5.15",
    "chromeos-6.1",
    "chromeos-6.6",
    "chromeos-6.12",
]

ANDROID_DESKTOP_STABLE_TAGS: list[str] = [
    "android-mainline-desktop-core",
    "android-mainline-desktop-vendor",
    "android15-6.6-desktop-core",
    "android15-6.6-desktop-vendor",
    "android16-6.12-desktop-core",
    "android16-6.12-desktop-vendor",
]

# STABLE tag steering kernel CL dispatching may not only point directly to a
# branch, but also can specify a group as a target. We define the semantics
# of groups and supported values here:
GROUPS_MAPPING = {
    "all": CHROMEOS_STABLE_TAGS + ANDROID_DESKTOP_STABLE_TAGS,
    "chromeos-all": CHROMEOS_STABLE_TAGS,
    "android-desktop-all": ANDROID_DESKTOP_STABLE_TAGS,
}

NO_DISPATCHING_NEEDED_TAGS = {
    "n/a",
    "N/A",
}

SUPPORTED_STABLE_TAG_VALUES: set[str] = {
    *NO_DISPATCHING_NEEDED_TAGS,
    *CHROMEOS_STABLE_TAGS,
    *ANDROID_DESKTOP_STABLE_TAGS,
    *GROUPS_MAPPING.keys(),
}


def _unravel_stable_tags(stable_tags: Iterable[str]) -> Iterator[str]:
    """Unravel & flatten group mappings in stable tags into branches."""
    for stable_tag in stable_tags:
        if stable_tag in GROUPS_MAPPING:
            yield from _unravel_stable_tags(GROUPS_MAPPING[stable_tag])
        elif stable_tag in SUPPORTED_STABLE_TAG_VALUES:
            yield stable_tag
        else:
            logger.error("Unsupported STABLE tag value: %s", stable_tag)


def _parse_kernel_dispatching_tags(
    commit_message: str,
) -> tuple[set[str], FixesTagT]:
    """Parse STABLE and FIXES tag from commit message."""
    (
        pseudoheaders,
        commit_message,
    ) = gerrit.Pseudoheaders.from_commit_message(commit_message, separator="=")
    stable_tags = set(
        _unravel_stable_tags(
            [tag.strip() for tag in pseudoheaders.get(STABLE_TAG).split(",")]
        )
    )
    fixes_tag = pseudoheaders.get(FIXES_TAG).strip()
    if fixes_tag:
        _, fixes_commit_message = fixes_tag.split(" ", 1)
    else:
        fixes_commit_message = None
    return stable_tags, fixes_commit_message


def _validate_remote_names_match_stable_values(
    downstreams: list[copybot_argparser.DownstreamConfig],
) -> None:
    """Ensure that downstream's remote names are in supported stable tags."""
    invalid_downstreams = [
        downstream
        for downstream in downstreams
        if downstream.remote_name not in SUPPORTED_STABLE_TAG_VALUES
    ]
    if invalid_downstreams:
        raise ValueError(
            "Downstream remote names in Kernel CL Dispatching use case should "
            "match supported stable tag values. Invalid remote names: "
            f"{invalid_downstreams}"
        )


def _location_contains_fixed_commit(
    fixes_tag: str,
    downstream: copybot_argparser.DownstreamConfig,
) -> bool:
    """Return whether a location contains the patch mentioned by FIXES tag."""
    revision_range = f"{downstream.cl_dispatcher_history_starts_with}..HEAD"
    grep_results = downstream.repo.log_raw(
        "--format=%H",
        "--ancestry-path",
        revision_range,
        "--grep",
        f"{fixes_tag}$",
    )
    return bool(grep_results)


def select_kernel_cl_dispatching_locations(
    upstream: copybot_argparser.UpstreamConfig,
    all_downstream_locations: list[copybot_argparser.DownstreamConfig],
    rev: str,
) -> list[copybot_argparser.DownstreamConfig]:
    """Select locations that are meant as a target for CL dispatching."""
    _validate_remote_names_match_stable_values(all_downstream_locations)

    commit_message = upstream.repo.get_commit_message(rev)
    stable_tags, fixes_tag = _parse_kernel_dispatching_tags(commit_message)

    if stable_tags & NO_DISPATCHING_NEEDED_TAGS:
        # N/A tag was set, nothing to do
        return []

    dispatching_locations = [
        downstream
        for downstream in all_downstream_locations
        if downstream.remote_name in stable_tags
        and (
            fixes_tag is None
            or _location_contains_fixed_commit(fixes_tag, downstream)
        )
    ]
    if dispatching_locations:
        logger.info(
            "[Kernel CL Dispatcher] Dispatching commit %s from upstream=%s "
            "to the following downstream locations:\n - %s",
            rev,
            upstream,
            "\n - ".join([str(x) for x in dispatching_locations]),
        )

    return dispatching_locations
