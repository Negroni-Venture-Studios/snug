from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "snug.core"
    label = "snug_core"
    verbose_name = "Snug core"

    def ready(self) -> None:
        from snug.core import checks  # noqa: F401 - registers Snug's system checks
