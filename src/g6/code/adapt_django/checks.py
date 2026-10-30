# SPDX-License-Identifier: Apache-2.0
"""Both Django commands execute only inside a fresh sandbox."""
from g6.code._build import Check
from g6.code.adapt_django.schema import identifier

_SCRIPT = """
try:
    import django
except ImportError:
    raise CheckerUnavailable(CHECKER_NOT_INSTALLED,
                             'Django is not installed; pip install "g6[django] @ git+https://github.com/basicisabc1994/g6solver" in the sandbox interpreter.')
from django.core.management import execute_from_command_line
import sys
os.chdir(root)
sys.path.insert(0, str(root))
if not (root / "manage.py").is_file():
    raise ValueError("supply manage.py and the project's settings as files")
os.environ["DJANGO_SETTINGS_MODULE"] = _G6_SETTINGS_VALUE
sys.argv = ARGV
evidence["command"] = " ".join(sys.argv)
evidence["django_version"] = django.get_version()
execute_from_command_line(sys.argv)
"""


def checks_for(*, settings_module="project.settings"):
    for part in settings_module.split("."):
        identifier(part)
    def script(argv):
        return _SCRIPT.replace("ARGV", repr(argv)).replace("_G6_SETTINGS_VALUE", repr(settings_module))
    return [
        Check("django_check", script(["manage.py", "check"]), "G6_E_DJANGO_CHECK"),
        Check("django_migrations", script(["manage.py", "makemigrations", "--check", "--dry-run"]),
              "G6_E_DJANGO_MIGRATIONS"),
    ]
