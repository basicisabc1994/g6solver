# SPDX-License-Identifier: Apache-2.0
from datetime import datetime, timezone

def archive_day(value):
    return datetime.fromisoformat(value).date()
