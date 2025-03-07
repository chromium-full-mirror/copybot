#!/bin/bash
# Copyright 2022 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# Show commands being run.
set -x

# Exit if any command exits non-zero.
set -e

# cd to the directory containing this script.
cd "$(dirname "$(realpath -e "${BASH_SOURCE[0]}")")"

# Run pytest.
./pytest_wrapper -v

# Run mypy.
../../chromite/scripts/mypy .

# Check for formatting issues (also reports during pre-upload).
cros lint .
cros format --check .
../../chromite/scripts/black --check .
