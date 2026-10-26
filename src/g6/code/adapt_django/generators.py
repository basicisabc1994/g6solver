# SPDX-License-Identifier: Apache-2.0
"""Seven string generators retained from the archive, with names validated first."""
from g6.code._build import Artefact, Provenance
from g6.code.adapt_django.schema import DjangoSpec, FieldSpec

HEADER = "# SPDX-License-Identifier: Apache-2.0\n"


def field_args(field: FieldSpec) -> str:
    args = []
    if field.field_type in {"ForeignKey", "OneToOneField", "ManyToManyField"}:
        args.append(repr(field.related_model))
        if field.field_type != "ManyToManyField":
            args.append("on_delete=models." + field.on_delete)
    if field.field_type in {"CharField", "EmailField", "URLField"}:
        args.append(f"max_length={field.max_length}")
    if field.field_type == "DecimalField":
        args.extend([f"max_digits={field.max_digits}", f"decimal_places={field.decimal_places}"])
    for key in ("null", "blank", "unique"):
        value = getattr(field, key)
        if key == "null" and field.on_delete == "SET_NULL" and field.field_type in {"ForeignKey", "OneToOneField"}:
            value = True
        if value:
            args.append(f"{key}=True")
    # False, zero and empty string defaults must survive.
    if "default" in field.model_fields_set:
        args.append(f"default={field.default!r}")
    for key in ("choices", "help_text", "verbose_name"):
        if getattr(field, key):
            args.append(f"{key}={getattr(field, key)!r}")
    return ", ".join(args)


def models(spec: DjangoSpec) -> str:
    lines = ["from django.db import models", ""]
    for model in spec.models:
        lines.append(f"class {model.name}(models.Model):")
        lines.extend(f"    {f.name} = models.{f.field_type}({field_args(f)})" for f in model.fields)
        if not model.fields:
            lines.append("    pass")
        if model.str_field:
            lines.extend(["    def __str__(self):", f"        return str(self.{model.str_field})"])
        if model.meta_ordering or model.abstract:
            lines.extend(["    class Meta:", f"        ordering = {model.meta_ordering!r}",
                          f"        abstract = {model.abstract!r}"])
        lines.append("")
    return "\n".join(lines)


def views(spec: DjangoSpec) -> str:
    lines = ["from django.views import generic", "from django.urls import reverse_lazy"]
    if any(view.login_required for view in spec.views):
        lines.append("from django.contrib.auth.mixins import LoginRequiredMixin")
    names = sorted({v.model_name for v in spec.views})
    if names:
        lines.append(f"from {spec.app_name}.models import " + ", ".join(names))
    for view in spec.views:
        cls = view.view_type.title() + "View"
        bases = ("LoginRequiredMixin, " if view.login_required else "") + "generic." + cls
        lines.extend(["", f"class {view.name}({bases}):", f"    model = {view.model_name}"])
        if view.template_name:
            lines.append(f"    template_name = {view.template_name!r}")
        if view.view_type in {"create", "update"}:
            lines.append(f"    fields = {view.fields!r}")
        if view.view_type == "list":
            lines.append(f"    paginate_by = {view.paginate_by}")
            if view.filter_fields:
                lines.extend(["    def get_queryset(self):", "        qs = super().get_queryset()",
                              "        q = self.request.GET.get('q')", "        if q:"])
                lines.extend(f"            qs = qs.filter({name}__icontains=q)" for name in view.filter_fields)
                lines.append("        return qs")
        if view.view_type in {"create", "update", "delete"}:
            lines.append(f"    success_url = reverse_lazy({(view.model_name.lower() + '-list')!r})")
    return "\n".join(lines)


def urls(spec: DjangoSpec) -> str:
    lines = ["from django.urls import path", f"from {spec.app_name} import views", "", "urlpatterns = ["]
    suffixes = {"list": "", "detail": "<int:pk>/", "create": "create/",
                "update": "<int:pk>/edit/", "delete": "<int:pk>/delete/"}
    for view in spec.views:
        name = view.model_name.lower()
        path = name + "/" + suffixes[view.view_type]
        lines.append(f"    path({path!r}, views.{view.name}.as_view(), name={(name + '-' + view.view_type)!r}),")
    return "\n".join([*lines, "]"])


def _imports(spec: DjangoSpec) -> list[str]:
    return [f"from {spec.app_name}.models import " + ", ".join(m.name for m in spec.models)] if spec.models else []


def admin(spec: DjangoSpec) -> str:
    lines = ["from django.contrib import admin", *_imports(spec)]
    for model in spec.models:
        if model.abstract:
            continue
        names = [f.name for f in model.fields[:5] if f.field_type != "ManyToManyField"]
        lines.extend(["", f"@admin.register({model.name})", f"class {model.name}Admin(admin.ModelAdmin):",
                      f"    list_display = {names or ['__str__']!r}",
                      f"    search_fields = {[f.name for f in model.fields if f.field_type in {'CharField', 'TextField'}]!r}"])
    return "\n".join(lines)


def forms(spec: DjangoSpec) -> str:
    lines = ["from django import forms", *_imports(spec)]
    for model in spec.models:
        if not model.abstract:
            lines.extend(["", f"class {model.name}Form(forms.ModelForm):", "    class Meta:",
                          f"        model = {model.name}", f"        fields = {[f.name for f in model.fields]!r}"])
    return "\n".join(lines)


def serializers(spec: DjangoSpec) -> str:
    lines = ["from rest_framework import serializers", *_imports(spec)]
    for model in spec.models:
        if not model.abstract:
            lines.extend(["", f"class {model.name}Serializer(serializers.ModelSerializer):", "    class Meta:",
                          f"        model = {model.name}",
                          f"        fields = {['id', *[f.name for f in model.fields]]!r}"])
    return "\n".join(lines)


def settings(spec: DjangoSpec) -> str:
    # Include only contrib features used by selected generators and model relations.
    has_admin = "admin" in spec.parts
    login = "views" in spec.parts and any(view.login_required for view in spec.views)
    relations = {field.related_model.split(".")[0] for model in spec.models for field in model.fields}
    auth = has_admin or login or ("models" in spec.parts and "auth" in relations)
    contenttypes = auth or ("models" in spec.parts and "contenttypes" in relations)
    sessions = has_admin or login
    apps = []
    if has_admin:
        apps.append("django.contrib.admin")
    if auth:
        apps.append("django.contrib.auth")
    if contenttypes:
        apps.append("django.contrib.contenttypes")
    if sessions:
        apps.append("django.contrib.sessions")
    if has_admin:
        apps.append("django.contrib.messages")
    apps.append(spec.app_name)
    middleware = []
    if sessions:
        middleware.append("django.contrib.sessions.middleware.SessionMiddleware")
    if "views" in spec.parts or has_admin:
        middleware.append("django.middleware.csrf.CsrfViewMiddleware")
    if sessions:
        middleware.append("django.contrib.auth.middleware.AuthenticationMiddleware")
    if has_admin:
        middleware.append("django.contrib.messages.middleware.MessageMiddleware")
    templates = []
    if "views" in spec.parts or has_admin:
        processors = ["django.template.context_processors.request"]
        if auth:
            processors.append("django.contrib.auth.context_processors.auth")
        if has_admin:
            processors.append("django.contrib.messages.context_processors.messages")
        templates.append({"BACKEND": "django.template.backends.django.DjangoTemplates",
                          "DIRS": [], "APP_DIRS": True, "OPTIONS": {"context_processors": processors}})
    # No environment/project .env read. Refuse until the person configures the secret.
    lines = ["from django.core.exceptions import ImproperlyConfigured",
             "SECRET_KEY = ''  # Supply your deployment's secret through its own configuration.",
             "if not SECRET_KEY:",
             "    raise ImproperlyConfigured('Set SECRET_KEY before running this project')",
             "DEBUG = False", "ALLOWED_HOSTS = []",
             f"INSTALLED_APPS = {apps!r}", f"MIDDLEWARE = {middleware!r}", f"TEMPLATES = {templates!r}"]
    if "urls" in spec.parts:
        lines.append(f"ROOT_URLCONF = {(spec.app_name + '.urls')!r}")
    lines.extend(["DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'",
                  "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}"])
    return "\n".join(lines)


def build_django(raw: dict) -> Artefact:
    spec = DjangoSpec.model_validate(raw)
    generators = {"models": models, "views": views, "urls": urls, "admin": admin,
                  "forms": forms, "serializers": serializers, "settings": settings}
    files = {f"{spec.app_name}/__init__.py": HEADER.encode(),
             f"{spec.project_name}/__init__.py": HEADER.encode(),
             f"{spec.app_name}/migrations/__init__.py": HEADER.encode()}
    for name in spec.parts:
        package = spec.project_name if name == "settings" else spec.app_name
        files[f"{package}/{name}.py"] = (HEADER + generators[name](spec) + "\n").encode()
    return Artefact(files, Provenance("template", template="django/" + ",".join(spec.parts)))
