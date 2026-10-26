# SPDX-License-Identifier: Apache-2.0
"""All three namespaces the ported templates reverse (``pages``, ``docs_hub``, ``blog``), mounted at the root: each
page's path in ``pages.routes.PAGES`` is its full path."""
from django.urls import include, path

urlpatterns = [
    path("", include("pages.urls")),
    path("", include("pages.docs_urls")),
    path("", include("pages.blog_urls")),
]
