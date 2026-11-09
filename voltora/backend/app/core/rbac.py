"""Canonical permission catalog and built-in roles.

Permissions are the only authority for staff actions; navigation, AI tools and plugin
capabilities all reference these keys. Owners hold the wildcard "*".
"""
from __future__ import annotations

PERMISSIONS: dict[str, str] = {
    # Commerce
    "catalog.read": "View products, collections and pricing",
    "catalog.write": "Create and edit products, variants, collections",
    "inventory.read": "View stock levels and movements",
    "inventory.adjust": "Adjust stock, transfer and receive inventory",
    "orders.read": "View orders",
    "orders.write": "Confirm, hold, cancel and annotate orders",
    "orders.refund": "Issue refunds (money movement)",
    "payments.read": "View payments, transactions and reconciliation",
    "payments.manage": "Configure payment providers and credentials",
    "shipping.read": "View shipments and courier status",
    "shipping.manage": "Book shipments, configure couriers and zones",
    "customers.read": "View customer profiles (masked contact data)",
    "customers.pii": "See full customer contact details",
    "customers.write": "Edit customers, notes and tags",
    "marketing.read": "View discounts and campaigns",
    "marketing.write": "Create discounts and campaigns",
    "support.read": "View support conversations",
    "support.write": "Reply to customers",
    "analytics.read": "View revenue and business analytics",
    # Content / design
    "content.read": "View pages, themes and drafts",
    "content.write": "Edit pages, global sections and themes as drafts",
    "content.publish": "Publish and roll back storefront releases",
    # Platform
    "ai.use": "Use AI assistants",
    "ai.manage": "Configure AI providers, agents and knowledge",
    "ai.approve": "Approve AI-proposed actions",
    "plugins.read": "View installed extensions",
    "plugins.manage": "Install, update and remove extensions",
    "automation.manage": "Create and edit automation workflows",
    "developer.manage": "Manage API keys and webhooks",
    "staff.read": "View staff and roles",
    "staff.manage": "Invite staff, edit roles and grants",
    "settings.read": "View store settings",
    "settings.write": "Change store settings",
    "security.manage": "Security policies, sessions and secrets",
    "system.read": "View health, logs and jobs",
    "system.manage": "Run maintenance, retry jobs, manage backups",
    "audit.read": "Read the audit trail",
}

ALL = "*"

BUILTIN_ROLES: dict[str, dict] = {
    "owner": {"name": "Owner", "description": "Full control of the store.", "permissions": [ALL]},
    "store_admin": {
        "name": "Store admin",
        "description": "Everything except security policy and ownership.",
        "permissions": [p for p in PERMISSIONS if p not in ("security.manage",)],
    },
    "store_manager": {
        "name": "Store manager",
        "description": "Daily operations across catalog, orders, customers and marketing.",
        "permissions": [
            "catalog.read", "catalog.write", "inventory.read", "inventory.adjust", "orders.read", "orders.write",
            "payments.read", "shipping.read", "shipping.manage", "customers.read", "customers.write", "customers.pii",
            "marketing.read", "marketing.write", "support.read", "support.write", "analytics.read", "content.read",
            "content.write", "ai.use", "staff.read", "settings.read",
        ],
    },
    "catalog_staff": {
        "name": "Catalog",
        "description": "Products, collections and stock visibility.",
        "permissions": ["catalog.read", "catalog.write", "inventory.read", "content.read", "ai.use"],
    },
    "fulfillment": {
        "name": "Fulfillment",
        "description": "Pick, pack, ship and receive stock.",
        "permissions": ["orders.read", "inventory.read", "inventory.adjust", "shipping.read", "shipping.manage",
                        "customers.read", "catalog.read"],
    },
    "support_agent": {
        "name": "Support agent",
        "description": "Customer conversations and order lookup.",
        "permissions": ["support.read", "support.write", "orders.read", "customers.read", "catalog.read", "ai.use"],
    },
    "marketer": {
        "name": "Marketer",
        "description": "Discounts, campaigns and content drafts.",
        "permissions": ["marketing.read", "marketing.write", "content.read", "content.write", "catalog.read",
                        "analytics.read", "ai.use"],
    },
    "finance": {
        "name": "Finance",
        "description": "Payments, refunds and reconciliation.",
        "permissions": ["orders.read", "orders.refund", "payments.read", "analytics.read", "customers.read"],
    },
    "developer": {
        "name": "Developer",
        "description": "Extensions, API keys, webhooks and diagnostics.",
        "permissions": ["plugins.read", "plugins.manage", "developer.manage", "system.read", "automation.manage",
                        "content.read", "ai.use"],
    },
    "auditor": {
        "name": "Auditor",
        "description": "Read-only access including the audit trail.",
        "permissions": [p for p in PERMISSIONS if p.endswith(".read")] + ["audit.read"],
    },
}


def has_permission(granted: set[str] | frozenset[str], needed: str) -> bool:
    return ALL in granted or needed in granted


def validate_permissions(perms: list[str]) -> list[str]:
    unknown = [p for p in perms if p != ALL and p not in PERMISSIONS]
    if unknown:
        raise ValueError(f"Unknown permissions: {', '.join(unknown)}")
    return sorted(set(perms))
