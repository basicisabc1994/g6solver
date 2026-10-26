#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""The g6solver.com website: ``python manage.py runserver 127.0.0.1:8000`` to preview, ``build_static`` to export."""
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "g6site.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
