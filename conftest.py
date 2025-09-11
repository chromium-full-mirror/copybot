# Copyright 2022 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Pytest config."""

import pathlib
import site

import pytest


@pytest.fixture(name="copybot_config")
def copybot_config_fixture():
    import test_copybot

    return test_copybot.cons_default_copybot_config()


site.addsitedir(str(pathlib.Path(__file__).parent))
