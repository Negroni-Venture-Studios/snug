from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "snug.audit"
    label = "audit"
    verbose_name = "Audit log"
