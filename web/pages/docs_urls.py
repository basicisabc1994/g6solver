# SPDX-License-Identifier: Apache-2.0
from django.urls import path

from pages.routes import PAGES
from pages.views import view_for

app_name = "docs_hub"
urlpatterns = [path(p.path, view_for(p), name=p.name) for p in PAGES if p.namespace == app_name]
