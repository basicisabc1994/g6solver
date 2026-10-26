# SPDX-License-Identifier: Apache-2.0
"""Settings for the website. It is built to static files: no database, no accounts, no sessions, no analytics."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]

# Nothing is signed (no sessions, forms or cookies), so this is a fixed local value, not a secret.
SECRET_KEY = "g6-static-site-nothing-is-signed"
DEBUG = os.environ.get("G6_SITE_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]

INSTALLED_APPS = ["django.contrib.staticfiles", "pages"]
MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
ROOT_URLCONF = "g6site.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "pages.context.site",
                "pages.kinetic.kinetic",
            ],
        },
    },
]

DATABASES = {}
USE_TZ = True
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

SITE_URL = "https://g6solver.com"
