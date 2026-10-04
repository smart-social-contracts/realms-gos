# Extensions

Realms comes with a powerful extension system that allows you to add custom functionality to your governance system.

## Built-in Extensions

Realms includes several built-in extensions:

- **Admin Dashboard** - Realm management & configuration
- **Citizen Dashboard** - Member-facing interface
- **Vault** - Treasury & ICRC-1 token management
- **Justice System** - Legal & dispute resolution
- **Land Registry** - Property management
- **Voting** - Proposals & governance

## Building Extensions

Extensions in Realms follow a modular architecture that allows you to:

- Add new entities and data models
- Create custom API endpoints
- Build frontend components
- Override existing entity methods
- Schedule automated tasks

### Extension Structure

```
my-extension/
├── backend/
│   ├── __init__.py
│   └── entry.py        # Entry points
├── frontend/
│   ├── lib/extensions/
│   └── routes/
├── manifest.json       # Metadata
└── requirements.txt    # Dependencies
```

### Creating an Extension

1. **Create the directory structure**:
```bash
mkdir -p extensions/my_extension/backend
mkdir -p extensions/my_extension/frontend
```

2. **Add a manifest.json**:
```json
{
  "name": "my_extension",
  "version": "1.0.0",
  "description": "My custom extension",
  "author": "Your Name",
  "entry_points": {
    "backend": "backend.entry",
    "frontend": "frontend/routes"
  }
}
```

3. **Implement backend logic** in `backend/entry.py`:
```python
from ggg import Entity, query_method, update_method

class MyEntity(Entity):
    name: str
    value: int

@query_method
def get_my_data():
    return {"status": "ok"}

@update_method
def set_my_data(data: dict):
    # Process data
    return {"success": True}
```

4. **Create frontend components** in `frontend/lib/extensions/my_extension/`:
```svelte
<script>
  // Your Svelte component
</script>

<div>
  <h1>My Extension</h1>
</div>
```

5. **Install the extension**:
```bash
realms extension install-from-source
```

## Extension Architecture

Extensions in Realms have several key features:

### No Inter-Canister Overhead
Extensions run in the same canister as the core system, eliminating the overhead of inter-canister calls.

### Atomic Operations
Extensions can access shared memory and perform atomic operations with the core system.

### Method Overrides
Extensions can override entity methods to customize behavior. See the [Method Override System](../reference/METHOD_OVERRIDE_SYSTEM) guide.

### CLI-First Development
The Realms CLI provides commands for creating, testing, and deploying extensions.

## Learn More

- **[Extension Architecture](../reference/EXTENSION_ARCHITECTURE)** - Detailed architecture guide
- **[Method Override System](../reference/METHOD_OVERRIDE_SYSTEM)** - Override entity methods
- **[Extension Examples](https://github.com/smart-social-contracts/realms-gos/tree/main/extensions)** - Browse built-in extensions

## Extension Development Workflow

```bash
# List available extensions
realms extension list

# Create a new extension
realms extension create my_extension

# Install from source
realms extension install-from-source

# Test your extension
basilisk-toolkit exec -f test_my_extension.py

# Deploy with your realm
realms realm deploy
```
