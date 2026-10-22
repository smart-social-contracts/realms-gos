# Demo Mundus - Multi-Realm Configuration

This folder contains the configuration for a complete demo "mundus" (multi-realm ecosystem) with multiple interconnected realms and a central registry.

## Structure

```
examples/demo/
├── manifest.json          # Top-level manifest defining realms and registries
├── realm1/               # First demo realm
│   ├── manifest.json     # Realm-specific configuration
│   ├── init.py           # Post-deployment script
│   ├── README.md         # Realm documentation
│   └── *.py             # Codex files (tax, benefits, governance, etc.)
├── realm2/               # Second demo realm
│   └── [same structure as realm1]
├── realm3/               # Third demo realm
│   └── [same structure as realm1]
└── registry/             # Central registry
    └── manifest.json     # Registry configuration
```

## Top-Level Manifest

The `manifest.json` in this folder defines:
- **realms**: List of realm subdirectories to deploy
- **registries**: List of registry subdirectories to deploy
- **name**: Name of the overall mundus

```json
{
  "realms": ["realm1", "realm2", "realm3"],
  "registries": ["registry"],
  "name": "Demo Mundus"
}
```

## Usage

When running `realms realm create`, the command reads this manifest and generates:

### Output Structure
```
{output_dir}/
├── realms/
│   ├── realm1/
│   │   ├── manifest.json
│   │   ├── realm_data.json
│   │   ├── scripts/
│   │   └── *.py (codex files)
│   ├── realm2/
│   │   └── [same structure]
│   └── realm3/
│       └── [same structure]
└── registries/
    └── registry/
        └── manifest.json
```

### Command Example
```bash
# Generate all realms and registry
realms realm create --random --members 50 --output-dir ./my-mundus

# This will create:
# - 3 realms (realm1, realm2, realm3) each with 50 members
# - 1 registry
# - All configured according to examples/demo/
```

## Realm Configuration

Each realm subfolder contains:

### Configuration Files
- **manifest.json**: Realm name and entity method overrides
- **init.py**: Post-deployment script that applies manifest settings
- **README.md**: Documentation specific to that realm

### Codex Files
- **tax_collection.py**: Progressive tax system
- **social_benefits.py**: Social benefits distribution
- **governance_automation.py**: Democratic governance
- **user_registration_hook.py**: Custom registration hooks

## Registry Configuration

The registry acts as a central coordinator for the mundus, managing:
- Cross-realm identity verification
- Shared resources and standards
- Inter-realm communication

## Customization

To customize the demo mundus:

1. **Add/Remove Realms**: Edit `manifest.json` and create corresponding folders
2. **Modify Realm Settings**: Edit individual realm manifests and codex files
3. **Change Data Generation**: Adjust command-line parameters (--members, --organizations, etc.)
4. **Configure Registry**: Edit `registry/manifest.json`

## Single Source of Truth

This folder serves as the canonical demo configuration. Any changes made here will automatically propagate to all newly created mundus instances via the `realms realm create` command.
