# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""copybot downstreaming config argparser.

Used for generating a common config to use across different downstream projects.
"""

import argparse
import pathlib

import gerrit


def generate_copybot_arg_parser() -> argparse.Namespace:
    """The entry point to the program."""
    parser = argparse.ArgumentParser(description="CopyBot")
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
        "FILTER: Filter the exclude-file-pattern matching files out of the CLs.",
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
        "upstream",
        help="Upstream Git URL, optionally with a branch and subtree separated"
        " by colons",
    )
    parser.add_argument(
        "downstream",
        help="Downstream Git URL, optionally with a branch and subtree"
        "separated by colons",
    )

    return parser
