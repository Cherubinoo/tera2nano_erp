"""Which record types can hold attachments, and which permission codes guard them.

A module registers its record types in its AppConfig.ready():

    from apps.attachments import registry
    registry.register("employee", view="hr.view", manage="hr.manage", label="Employee")

Anyone holding `view` may list and download the attachments of that record type; `manage` is needed to add or remove one.
"""
TARGETS = {}


def register(entity_type, *, view, manage, label=""):
    TARGETS[entity_type] = {"view": view, "manage": manage, "label": label or entity_type.replace("_", " ").title()}


def target(entity_type):
    return TARGETS.get(entity_type)
