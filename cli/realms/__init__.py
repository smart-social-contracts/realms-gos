"""
Realms GOS - Governance Operating System SDK and CLI

Usage:
    # SDK (for workstation-side Python scripts/agents)
    from realms import realm, mundus, registry
    
    await realm.create(deploy=True)
    await realm.call("join_realm", '("admin")', folder="...")
    invoices = await realm.db.get("Invoice", folder="...")

    # CLI
    $ realms realm create --deploy
    $ realms realm call join_realm '("admin")' -f .realms/realm_X
"""

from . import realm as realm
from . import mundus as mundus
from . import registry as registry

__version__ = "0.6.4"
__all__ = ["realm", "mundus", "registry", "__version__"]
