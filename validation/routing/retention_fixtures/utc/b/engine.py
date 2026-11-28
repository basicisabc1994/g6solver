# SPDX-License-Identifier: Apache-2.0
import datetime

def archive_day(value):
    return datetime.datetime.fromisoformat(value).date()
