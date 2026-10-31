"""Database explorer command for interactive Realm database exploration."""

import importlib
import inspect
import json
import os
import re
import traceback
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import typer
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from rich.console import Console

from ..utils import get_effective_network_and_canister, get_effective_cwd, get_logger

logger = get_logger("db")

console = Console()


class DfxNotFoundError(Exception):
    """Raised when dfx is not installed or not in PATH."""
    pass


def check_dfx_available() -> bool:
    """Check if dfx is available in the system PATH.
    
    Returns:
        True if dfx is available, False otherwise.
    """
    try:
        result = subprocess.run(
            ["dfx", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False
    except Exception:
        return False


def realm_op(
    canister: str,
    name: str,
    args: Optional[Dict[str, Any]] = None,
    network: str = "local",
    identity: Optional[str] = None,
    read: bool = False,
    cwd: Optional[str] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    """Run operation ``name`` on the realm ``canister`` with dfx from ``cwd`` (the realm folder, where dfx
    resolves canister names) and return its reply; a failed call is ``{"ok": False, "error": ...}``."""
    from ..realm_api import op_arg
    from .extension import _parse_candid_string

    cmd = ["dfx", "canister", "call", "--network", network]
    if identity:
        cmd.extend(["--identity", identity])
    if read:
        cmd.append("--query")
    cmd.extend([canister, "read" if read else "run", op_arg(name, args)])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout).strip()}
    return json.loads(_parse_candid_string(result.stdout))


def require_dfx() -> None:
    """Check that dfx is available, exit with user-friendly message if not.
    
    Raises:
        typer.Exit: If dfx is not available.
    """
    if not check_dfx_available():
        console.print("[red]Error: dfx is not installed or not in PATH[/red]")
        raise typer.Exit(1)


ENTITY_DESCRIPTIONS = {
    "users": "System users and their profiles",
    "humans": "Human entities and identity records",
    "organizations": "Organizational entities and structures",
    "mandates": "Governance mandates and authorizations",
    "tasks": "Scheduled and executed tasks",
    "task_schedules": "Task scheduling configurations",
    "transfers": "Asset transfers between entities",
    "trades": "Completed trading transactions",
    "instruments": "Financial and governance instruments",
    "codexes": "Executable code and logic",
    "disputes": "Conflict resolution records",
    "licenses": "Permissions and authorizations",
    "realms": "Realm configurations and metadata",
    "proposals": "Governance proposals and voting",
    "votes": "Individual voting records",
    "treasuries": "Treasury and vault management",
    "user_profiles": "User profile configurations and permissions",
}


@dataclass
class NavigationState:
    """Tracks current navigation state in the database explorer."""

    entity_type: str = ""
    page_num: int = 0
    page_size: int = 10
    current_items: List[Dict] = None
    selected_item: Optional[Dict] = None
    cursor_position: int = 0
    view_mode: str = "entity_list"
    navigation_stack: List[Dict] = None

    def __post_init__(self):
        if self.current_items is None:
            self.current_items = []
        if self.navigation_stack is None:
            self.navigation_stack = []


class CursorDatabaseExplorer:
    """Interactive database explorer for Realm entities."""

    def __init__(self, network: str, canister: str, cwd: Optional[str] = None, identity: Optional[str] = None):
        self.network = network
        self.canister = canister
        self.identity = identity
        self.cwd = cwd  # Working directory for dfx commands
        self.state = NavigationState()
        self.app = None

        # Cache for relationship mappings discovered from GGG models
        self._relationship_cache = None
        self._ggg_classes = self._discover_ggg_classes()
        self._entity_counts = {}

    def call_backend(self, name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Run read operation ``name`` on the realm and return its reply."""
        return realm_op(self.canister, name, args, self.network, self.identity, read=True, cwd=self.cwd, timeout=30)

    def list_entities(
        self, entity_type: str, page_num: int = 0, page_size: int = 10
    ) -> Dict[str, Any]:
        """One page of an entity's rows (``realm_export``), each with its ``_type`` and ``_id``."""
        if "::" in entity_type:
            return {"error": f"{entity_type}: extension entities are not supported by this realm version", "items": []}
        try:
            reply = self.call_backend(
                "realm_export", {"entity": entity_type, "from": page_num * page_size + 1, "count": page_size}
            )
        except Exception as e:
            logger.error(traceback.format_exc())
            return {"error": str(e), "items": []}
        if not reply.get("ok"):
            return {"error": reply.get("error", "Unknown error"), "items": []}
        more = reply.get("next") is not None
        return {
            "items": [{"_type": entity_type, "_id": row.get("id", ""), **row} for row in reply["rows"]],
            "page_num": page_num,
            "page_size": page_size,
            "total_pages": page_num + (2 if more else 1),
            "more": more,
        }

    def all_entities(self, entity_type: str, page_size: int = 100):
        """Every row of ``entity_type``, page by page; raises ``RuntimeError`` on a refused page."""
        page_num = 0
        while True:
            result = self.list_entities(entity_type, page_num, page_size)
            if "error" in result:
                raise RuntimeError(result["error"])
            yield from result["items"]
            if not result["more"]:
                return
            page_num += 1

    def find_entities(self, entity_type: str, params: List[tuple]) -> Dict[str, Any]:
        """Rows of ``entity_type`` whose fields match every ``(field, value)`` in ``params``."""
        try:
            items = [
                item for item in self.all_entities(entity_type)
                if all(v in (str(item.get(k)), json.dumps(item.get(k))) for k, v in params)
            ]
        except RuntimeError as e:
            return {"error": str(e), "items": []}
        return {"items": items}

    def get_entity(self, entity_type: str, entity_id: str) -> Dict[str, Any]:
        """The row of ``entity_type`` whose id is ``entity_id``."""
        try:
            item = next((i for i in self.all_entities(entity_type) if i.get("_id") == entity_id), None)
        except RuntimeError as e:
            return {"error": str(e)}
        return item or {"error": f"Entity '{entity_id}' not found"}

    def create_key_bindings(self):
        """Create key bindings for cursor navigation."""
        kb = KeyBindings()

        @kb.add("up")
        def move_up(event):
            if self.state.cursor_position > 0:
                self.state.cursor_position -= 1
                event.app.invalidate()

        @kb.add("down")
        def move_down(event):
            # Calculate max position based on current view mode
            if self.state.view_mode == "record_detail":
                # In record detail mode, cursor should be bounded by navigable items
                max_pos = len(getattr(self.state, 'navigable_items', [])) - 1
            else:
                # In other modes, use current_items
                max_pos = len(self.state.current_items) - 1
            
            if self.state.cursor_position < max_pos:
                self.state.cursor_position += 1
                event.app.invalidate()

        @kb.add("right")
        def select_item(event):
            self.handle_selection()
            event.app.invalidate()

        @kb.add("enter")
        def drill_into_relationship(event):
            if self.state.view_mode == "record_detail":
                self.handle_relationship_drilling()
                event.app.invalidate()

        @kb.add("left")
        @kb.add("backspace")
        def go_back(event):
            self.handle_back_navigation()
            event.app.invalidate()

        @kb.add("pageup")
        def page_up(event):
            if self.state.page_num > 0:
                self.state.page_num -= 1
                self.refresh_data()
                event.app.invalidate()

        @kb.add("pagedown")
        def page_down(event):
            if (
                hasattr(self.state, "total_pages")
                and self.state.page_num < self.state.total_pages - 1
            ):
                self.state.page_num += 1
                self.refresh_data()
                event.app.invalidate()

        @kb.add("q")
        @kb.add("c-c")
        def quit_app(event):
            event.app.exit()

        return kb

    def handle_selection(self):
        """Handle item selection based on current view mode."""

        logger.debug('handle_selection')

        logger.debug(f'self.state.view_mode = {self.state.view_mode}')

        if self.state.view_mode == "entity_list":
            if self.state.cursor_position < len(self._ggg_classes):
                # Get sorted classes to match the display order
                sorted_classes = sorted(self._ggg_classes, key=lambda cls: cls.__name__)
                selected_entity = sorted_classes[self.state.cursor_position]
                self.state.entity_type = selected_entity
                self.state.view_mode = "record_list"
                self.state.cursor_position = 0
                self.refresh_data()
        elif self.state.view_mode == "record_list":
            if self.state.cursor_position < len(self.state.current_items):
                self.state.selected_item = self.state.current_items[
                    self.state.cursor_position
                ]
                self.state.view_mode = "record_detail"
                self.state.cursor_position = 0
        elif self.state.view_mode == "record_detail":
            self.handle_relationship_drilling()

    def handle_back_navigation(self):
        """Handle back navigation including relationship drilling."""

        logger.debug('handle_back_navigation')
        logger.debug(f'self.state.navigation_stack = {self.state.navigation_stack}')

        if self.state.navigation_stack:
            previous_state = self.state.navigation_stack.pop()
            self.state.entity_type = previous_state["entity_type"]
            self.state.selected_item = previous_state["selected_item"]
            self.state.cursor_position = previous_state["cursor_position"]
            self.state.view_mode = previous_state["view_mode"]
            if "current_items" in previous_state:
                self.state.current_items = previous_state["current_items"]
        else:
            if self.state.view_mode == "record_detail":
                self.state.view_mode = "record_list"
                self.state.selected_item = None
                self.state.cursor_position = 0
            elif self.state.view_mode == "record_list":
                self.state.view_mode = "entity_list"
                self.state.entity_type = ""
                self.state.cursor_position = 0
                self.state.current_items = self._ggg_classes

    def handle_relationship_drilling(self):
        logger.debug('handle_relationship_drilling')

        logger.debug(f'self.state.view_mode = {self.state.view_mode}')

        """Navigate into relationships using cursor keys."""
        if self.state.view_mode != "record_detail" or not self.state.selected_item:
            return

        # Use the new navigable items structure
        if not hasattr(self.state, "navigable_items") or not self.state.navigable_items:
            return

        if self.state.cursor_position >= len(self.state.navigable_items):
            return

        nav_item = self.state.navigable_items[self.state.cursor_position]

        # Only navigate if it's a navigable item
        if nav_item["type"] not in ["relationship_field", "relationship"]:
            return

        # Save current state for back navigation
        self.state.navigation_stack.append(
            {
                "entity_type": self.state.entity_type,
                "selected_item": self.state.selected_item,
                "cursor_position": self.state.cursor_position,
                "view_mode": self.state.view_mode,
                "current_items": self.state.current_items,
            }
        )

        if nav_item["type"] == "relationship_field":
            # Navigate to a single related entity by ID
            related_entity_type = nav_item["related_type"]
            related_id = nav_item["value"]

            logger.debug(f"Drilling into relationship field: {nav_item['key']} -> {related_entity_type} ID {related_id}")

            # Try to fetch the related entity
            try:
                # Find the corresponding class for the entity type
                related_class = None
                for cls in self._ggg_classes:
                    if self._class_name_to_entity_type(cls.__name__) == related_entity_type:
                        related_class = cls
                        break
                
                if related_class:
                    result = self.list_entities(related_class.__name__, 0, 100)
                    if "items" in result and result["items"]:
                        # Find the specific item by ID or alias
                        related_items = [
                            item
                            for item in result["items"]
                            if item.get("_id") == str(related_id)
                            or item.get("name") == str(related_id)  # Check alias/name match
                        ]
                        if related_items:
                            # Navigate directly to record detail view for single item
                            self.state.current_items = related_items
                            self.state.cursor_position = 0
                            if len(related_items) == 1:
                                # Go directly to detail view for single match
                                self.state.selected_item = related_items[0]
                                self.state.view_mode = "record_detail"
                            else:
                                self.state.view_mode = "record_list"
                            self.state.entity_type = related_class
                            return
                        else:
                            logger.debug(f"No item found with ID or alias {related_id} in {related_entity_type}")
                    else:
                        logger.debug(f"No items returned for {related_entity_type}")
                else:
                    logger.debug(f"No class found for entity type {related_entity_type}")
            except Exception as e:
                logger.error(f"Error fetching related entity: {e}")
                logger.error(traceback.format_exc())

            # Fallback: create a placeholder item
            placeholder_item = {
                "_id": str(related_id),
                "_type": related_entity_type.rstrip("s").title(),
                "name": f"Referenced {related_entity_type.rstrip('s').title()}",
                "error": "Could not fetch details"
            }
            self.state.current_items = [placeholder_item]
            self.state.cursor_position = 0
            self.state.view_mode = "record_list"
            # Find the class for this entity type
            for cls in self._ggg_classes:
                if self._class_name_to_entity_type(cls.__name__) == related_entity_type:
                    self.state.entity_type = cls
                    break

        elif nav_item["type"] == "relationship":
            # Navigate to multiple related entities
            rel_items = nav_item["value"]
            logger.debug(f"Drilling into relationship: {nav_item['key']} with {len(rel_items) if isinstance(rel_items, list) else 1} items")
            
            if isinstance(rel_items, list) and rel_items:
                # Determine entity type from the first item
                first_item_type = rel_items[0].get("_type", "Related")
                related_class = None
                for cls in self._ggg_classes:
                    if cls.__name__ == first_item_type or self._class_name_to_entity_type(cls.__name__) == first_item_type.lower():
                        related_class = cls
                        break
                
                if related_class:
                    # Try to fetch full entity details for each related item
                    full_items = []
                    try:
                        result = self.list_entities(related_class.__name__, 0, 100)
                        if "items" in result and result["items"]:
                            # Match related items with full entity data
                            for rel_item in rel_items:
                                rel_id = rel_item.get("_id")
                                if rel_id:
                                    full_item = next(
                                        (item for item in result["items"] if item.get("_id") == str(rel_id)),
                                        rel_item  # Fallback to minimal data if not found
                                    )
                                    full_items.append(full_item)
                                else:
                                    full_items.append(rel_item)
                        else:
                            full_items = rel_items
                    except Exception as e:
                        logger.error(f"Error fetching full entity details: {e}")
                        full_items = rel_items
                    
                    self.state.current_items = full_items
                    self.state.cursor_position = 0
                    self.state.view_mode = "record_list"
                    self.state.entity_type = related_class
                else:
                    # Fallback - use minimal data with generic type
                    self.state.current_items = rel_items
                    self.state.cursor_position = 0
                    self.state.view_mode = "record_list"
                    
                    class GenericType:
                        __name__ = first_item_type
                    self.state.entity_type = GenericType
                    
            elif not isinstance(rel_items, list):
                # Single item wrapped in list
                item_type = rel_items.get("_type", "Related")
                related_class = None
                for cls in self._ggg_classes:
                    if cls.__name__ == item_type:
                        related_class = cls
                        break
                
                if related_class:
                    # Try to fetch full entity details for the single item
                    rel_id = rel_items.get("_id")
                    full_item = rel_items  # Default fallback
                    
                    if rel_id:
                        try:
                            result = self.list_entities(related_class.__name__, 0, 100)
                            if "items" in result and result["items"]:
                                found_item = next(
                                    (item for item in result["items"] if item.get("_id") == str(rel_id)),
                                    None
                                )
                                if found_item:
                                    full_item = found_item
                        except Exception as e:
                            logger.error(f"Error fetching full entity details: {e}")
                    
                    self.state.current_items = [full_item]
                    self.state.cursor_position = 0
                    self.state.view_mode = "record_list"
                    self.state.entity_type = related_class
                else:
                    # Fallback
                    self.state.current_items = [rel_items]
                    self.state.cursor_position = 0
                    self.state.view_mode = "record_list"

    def _class_name_to_entity_type(self, class_name: str) -> str:
        """Convert class name to entity type (e.g., User -> user, TaskSchedule -> task_schedule)."""
        # Convert CamelCase to snake_case without pluralization
        snake_case = re.sub("([A-Z]+)([A-Z][a-z])", r"\1_\2", class_name)
        snake_case = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", snake_case).lower()
        return snake_case

    def _discover_ggg_classes(self):
        """Dynamically discover GGG entity classes."""
        if getattr(self, "_ggg_classes", None) is not None:
            return self._ggg_classes

        classes = []

        # Add the GGG module path to sys.path if not already there
        # Path from cli/realms/cli/commands/db.py -> repo_root/src/realm_backend
        ggg_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "..", "src", "realm_backend"
        )
        ggg_path = os.path.abspath(ggg_path)
        if ggg_path not in sys.path:
            sys.path.insert(0, ggg_path)

        # Also add the project root to handle ic_python_db imports
        # Path from cli/realms/cli/commands/db.py -> repo_root
        project_root = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
        project_root = os.path.abspath(project_root)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        try:
            # Import the GGG module - first try local realm project, then fall back to bundled
            ggg_module = None
            try:
                ggg_module = importlib.import_module("ggg")
                logger.debug("Loaded ggg module from local realm project")
            except ImportError:
                # Fall back to bundled realms.ggg module
                ggg_module = importlib.import_module("realms.ggg")
                logger.debug("Loaded ggg module from bundled realms.ggg")

            # The exported classes: the __all__ list, else every entity the module defines (what realm_export takes)
            names = getattr(ggg_module, "__all__", None) or [
                n for n, c in vars(ggg_module).items() if inspect.isclass(c) and hasattr(c, "load_some")
            ]
            for class_name in names:
                try:
                    cls = getattr(ggg_module, class_name)
                    if inspect.isclass(cls) and hasattr(cls, "__name__"):
                        classes.append(cls)
                except AttributeError:
                    logger.debug(f"Failed to get class {class_name}: {traceback.format_exc()}")
                    continue

        except ImportError as e:
            # No GGG models available at all
            logger.debug(f"No ggg module available: {e}")
            pass

        self._ggg_classes = classes

        logger.debug(f'self._ggg_classes = {self._ggg_classes}')
        return classes

    def _discover_relationship_fields(self):
        """Discover relationship field mappings from GGG models."""
        if self._relationship_cache is not None:
            return self._relationship_cache

        relationship_fields = {}
        classes = self._discover_ggg_classes()

        for class_obj in classes:
            try:
                # Inspect the model class for relationship fields
                for attr_name in dir(class_obj):
                    attr = getattr(class_obj, attr_name, None)
                    if attr is None or attr_name.startswith("_"):
                        continue

                    # Check if it's a relationship field from ic_python_db
                    attr_type = type(attr).__name__
                    if attr_type in [
                        "ManyToOne",
                        "OneToOne",
                        "OneToMany",
                        "ManyToMany",
                    ]:
                        try:
                            # Get the related model name and back reference from the relationship
                            related_model = None
                            back_ref = None

                            # Try different ways to get relationship info
                            if hasattr(attr, "entity_types"):
                                related_model = attr.entity_types
                                if hasattr(attr, "reverse_name"):
                                    back_ref = attr.reverse_name
                            elif hasattr(attr, "_args") and len(attr._args) >= 1:
                                related_model = attr._args[0]
                                if len(attr._args) >= 2:
                                    back_ref = attr._args[1]
                            elif hasattr(attr, "args") and len(attr.args) >= 1:
                                related_model = attr.args[0]
                                if len(attr.args) >= 2:
                                    back_ref = attr.args[1]

                            if isinstance(related_model, str):
                                # Convert model name to entity type
                                related_entity_type = self._class_name_to_entity_type(
                                    related_model
                                )

                                # Handle different relationship types
                                if attr_type == "ManyToOne":
                                    # This model has a foreign key to the related model
                                    # e.g., Transfer.from_user -> ManyToOne("User", "transfers_from")
                                    # means Transfer has from_user_id field
                                    field_pattern = f"{attr_name}_id"
                                    relationship_fields[field_pattern] = (
                                        related_entity_type
                                    )
                                    # Also map the bare field name for alias-based lookups
                                    # e.g., "codex" -> "codex" (value might be alias like "satoshi_transfer_codex")
                                    relationship_fields[attr_name] = related_entity_type

                                elif attr_type == "OneToOne":
                                    # Similar to ManyToOne for foreign key fields
                                    field_pattern = f"{attr_name}_id"
                                    relationship_fields[field_pattern] = (
                                        related_entity_type
                                    )
                                    # Also map the bare field name for alias-based lookups
                                    relationship_fields[attr_name] = related_entity_type

                                elif (
                                    attr_type in ["OneToMany", "ManyToMany"]
                                    and back_ref
                                ):
                                    # This is a reverse relationship
                                    # e.g., User.transfers_from -> OneToMany("Transfer", "from_user")
                                    # means Transfer has from_user_id field pointing to User
                                    field_pattern = f"{back_ref}_id"
                                    current_entity_type = self._class_name_to_entity_type(class_obj.__name__)
                                    relationship_fields[field_pattern] = current_entity_type

                        except Exception:
                            # Skip problematic relationship definitions
                            logger.error(traceback.format_exc())
                            continue

            except Exception:
                # Skip problematic models
                logger.error(traceback.format_exc())
                continue

        self._relationship_cache = relationship_fields

        logger.debug(f'self._relationship_cache = {self._relationship_cache}')

        return relationship_fields

    def get_all_relationships(self, item):
        """Extract both explicit relations and inferred relationships from ID fields."""
        relations = item.get("relations", {}).copy()

        # Get dynamically discovered relationship fields
        relationship_fields = self._discover_relationship_fields()

        for field_name, entity_type in relationship_fields.items():
            if field_name in item and item[field_name]:
                related_item = {
                    "_id": item[field_name],
                    "_type": entity_type.rstrip("s").title(),
                    "name": f"Referenced {entity_type.rstrip('s').title()}",
                }
                relations[field_name.replace("_id", "")] = [related_item]

        return relations

    def refresh_data(self):
        """Refresh current data based on state."""

        logger.debug('refresh_data')

        logger.debug(f'self.state.view_mode = {self.state.view_mode}')

        if self.state.view_mode == "entity_list":
            self.state.current_items = sorted(self._ggg_classes, key=lambda cls: cls.__name__)
        elif self.state.view_mode == "record_list" and self.state.entity_type:
            logger.info(f"Fetching data for entity type: {self.state.entity_type}")
            result = self.list_entities(
                self.state.entity_type.__name__, self.state.page_num, self.state.page_size
            )
            logger.info(f"Backend result: {result}")
            if "error" not in result and "items" in result:
                self.state.current_items = result["items"]
                self.state.total_pages = result.get("total_pages", 1)
                logger.info(f"Loaded {len(result['items'])} items")
            else:
                logger.error(f"Failed to load data: {result}")
                self.state.current_items = []
                self.state.total_pages = 1

    def _fetch_entity_counts(self):
        """Fetch entity counts once and cache them: the ones ``realm_monitor`` reports, "?" for the others."""
        if self._entity_counts:
            return  # Already cached

        try:
            rows = self.call_backend("realm_monitor").get("rows") or {}
        except Exception:
            rows = {}
        for class_obj in self._ggg_classes:
            self._entity_counts[class_obj.__name__] = rows.get(class_obj.__name__, "?")

    def render_entity_list(self):
        """Render the entity selection menu."""
        lines = []
        lines.append("Realm Database Explorer")
        lines.append("Select an entity type to explore:")
        lines.append("")

        # Sort classes alphabetically
        sorted_classes = sorted(self._ggg_classes, key=lambda cls: cls.__name__)
        
        # Calculate columns (3 columns for better layout)
        num_cols = 3
        num_rows = (len(sorted_classes) + num_cols - 1) // num_cols
        
        for row in range(num_rows):
            line_parts = []
            for col in range(num_cols):
                idx = row + col * num_rows
                if idx < len(sorted_classes):
                    class_obj = sorted_classes[idx]
                    cursor = "> " if idx == self.state.cursor_position else "  "
                    count = self._entity_counts.get(class_obj.__name__, 0)
                    # Format: "  EntityName (count)"
                    part = f"{cursor}{class_obj.__name__} ({count})"
                    line_parts.append(f"{part:<25}")
                else:
                    line_parts.append(" " * 25)
            
            lines.append("".join(line_parts).rstrip())

        lines.append("")
        lines.append("Commands: Up/Down navigate | Right select | q quit")

        return "\n".join(lines)

    def render_record_list(self):
        """Render the record list view."""
        lines = []
        lines.append(f"{self.state.entity_type.__name__} List")

        if hasattr(self.state, "total_pages"):
            current_page = self.state.page_num + 1
            lines.append(f"Page {current_page} of {self.state.total_pages}")

        lines.append("")

        for i, item in enumerate(self.state.current_items):
            cursor = "> " if i == self.state.cursor_position else "  "
            if isinstance(item, dict):
                item_id = item.get("_id", "N/A")
                name = (
                    item.get("name")
                    or item.get("title")
                    or item.get("username")
                    or "N/A"
                )
                lines.append(f"{cursor}{i + 1:2}. {item_id:<20} {name}")
            else:
                # Handle string items (entity type names)
                lines.append(f"{cursor}{i + 1:2}. {item}")

        lines.append("")
        lines.append(
            "Commands: Up/Down navigate | Right view details | Left back | PgUp/PgDn pages | q quit"
        )

        return "\n".join(lines)

    def render_record_detail(self):
        """Render the detailed record view."""
        if not self.state.selected_item:
            return "No item selected"

        lines = []
        item = self.state.selected_item
        item_type = self.state.entity_type.__name__
        item_id = item.get("_id", "N/A")

        lines.append(f"{item_type} Details")
        lines.append(f"ID: {item_id}")
        lines.append("")

        # Get relationship fields for navigation
        relationship_fields = self._discover_relationship_fields()

        # Combine properties and navigable relationships
        navigable_items = []

        # Add regular properties
        for key, value in item.items():
            if key not in ["_id", "relations"] and not key.startswith("_"):
                if key in relationship_fields and value:
                    # This is a navigable relationship field
                    related_entity_type = relationship_fields[key]
                    navigable_items.append(
                        {
                            "type": "relationship_field",
                            "key": key,
                            "value": value,
                            "related_type": related_entity_type,
                            "display": f"{key}: {value} → {related_entity_type.rstrip('s').title()}",
                        }
                    )
                else:
                    # Regular property
                    navigable_items.append(
                        {
                            "type": "property",
                            "key": key,
                            "value": value,
                            "display": f"{key}: {value}",
                        }
                    )

        # Add explicit relationships
        relations = self.get_all_relationships(item)
        for rel_name, rel_items in relations.items():
            # Skip if this relationship is already shown as a property field
            # Check both "{rel_name}_id" and bare "{rel_name}" patterns
            if any(
                nav_item["key"] in [f"{rel_name}_id", rel_name]
                for nav_item in navigable_items
                if nav_item["type"] == "relationship_field"
            ):
                continue
            count = len(rel_items) if isinstance(rel_items, list) else 1
            navigable_items.append(
                {
                    "type": "relationship",
                    "key": rel_name,
                    "value": rel_items,
                    "display": f"{rel_name}: {count} items",
                }
            )

        # Render all items with navigation support
        lines.append("Properties & Relationships:")
        for i, nav_item in enumerate(navigable_items):
            cursor = "> " if i == self.state.cursor_position else "  "
            if nav_item["type"] in ["relationship_field", "relationship"]:
                lines.append(f"{cursor}  {nav_item['display']} [navigable]")
            else:
                lines.append(f"{cursor}  {nav_item['display']}")

        # Store navigable items for navigation
        self.state.navigable_items = navigable_items

        lines.append("")
        lines.append(
            "Commands: Up/Down navigate | Enter drill into relationships | Left back | q quit"
        )

        return "\n".join(lines)

    def create_layout(self):
        """Create the prompt_toolkit layout based on current state."""
        if self.state.view_mode == "entity_list":
            content = self.render_entity_list()
        elif self.state.view_mode == "record_list":
            content = self.render_record_list()
        elif self.state.view_mode == "record_detail":
            content = self.render_record_detail()
        else:
            content = "Unknown view mode"

        return Layout(
            HSplit(
                [
                    Window(content=FormattedTextControl(text=content), wrap_lines=True),
                ]
            )
        )

    def run(self):
        """Main interactive loop using prompt_toolkit Application."""
        try:
            if not self.call_backend("status").get("ok"):
                raise RuntimeError("status refused")
            logger.debug("Connected to backend canister\n")
        except Exception:
            logger.debug("Could not connect to backend canister")
            return

        # Fetch entity counts once at startup
        self._fetch_entity_counts()
        
        self.state.current_items = self._ggg_classes
        kb = self.create_key_bindings()

        def get_layout():
            return self.create_layout()

        self.app = Application(
            layout=get_layout(),
            key_bindings=kb,
            full_screen=True,
            refresh_interval=0.1,
        )

        original_invalidate = self.app.invalidate

        def invalidate():
            self.app.layout = get_layout()
            original_invalidate()

        self.app.invalidate = invalidate

        self.app.run()


def _connect(explorer: CursorDatabaseExplorer) -> None:
    try:
        reply = explorer.call_backend("status")
    except Exception as e:
        reply = {"error": str(e)}
    if not reply.get("ok"):
        console.print(f"[red]Error: Could not connect to backend canister: {reply.get('error')}[/red]")
        raise typer.Exit(1)


def db_get_command(
    entity_type: str,
    entity_id: Optional[str] = None,
    network: Optional[str] = None,
    canister: Optional[str] = None,
    folder: Optional[str] = None,
) -> None:
    """Get entities from the database and output as JSON.
    
    Args:
        entity_type: The entity type (e.g., User, Transfer, Mandate)
        entity_id: Optional specific entity ID to retrieve
        network: Network to use
        canister: Canister to connect to
        folder: Realm folder containing dfx.json
    """
    require_dfx()
    
    effective_network, effective_canister = get_effective_network_and_canister(
        network, canister
    )
    effective_cwd = get_effective_cwd(folder)

    explorer = CursorDatabaseExplorer(effective_network, effective_canister, cwd=effective_cwd)
    
    _connect(explorer)
    
    # Check if this is a namespaced extension entity (e.g., "vault::KnownSubaccount")
    is_extension_entity = "::" in entity_type
    
    if is_extension_entity:
        # For extension entities, we pass the namespaced type directly to the canister
        matching_class = None  # We'll query by string, not by class
    else:
        # Find the matching class for core entities
        matching_class = None
        for cls in explorer._ggg_classes:
            if cls.__name__.lower() == entity_type.lower():
                matching_class = cls
                break
        
        if not matching_class:
            # Print available entity types
            available = [cls.__name__ for cls in explorer._ggg_classes]
            console.print(f"[red]Error: Entity type '{entity_type}' not found[/red]")
            console.print(f"[yellow]Available entity types: {', '.join(sorted(available))}[/yellow]")
            raise typer.Exit(1)
    
    # Determine the query type name
    query_type = entity_type if is_extension_entity else matching_class.__name__
    
    # If entity_id is provided, get specific entity
    if entity_id:
        result = explorer.get_entity(query_type, entity_id)
        if "error" in result:
            console.print(f"[red]Error: {result['error']}[/red]")
            raise typer.Exit(1)
        
        # Output single entity as JSON
        print(json.dumps(result, indent=2))
    else:
        try:
            all_items = list(explorer.all_entities(query_type))
        except RuntimeError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)

        # Output all entities as JSON array
        print(json.dumps(all_items, indent=2))


def db_find_command(
    entity_type: str,
    filters: List[str],
    network: Optional[str] = None,
    canister: Optional[str] = None,
    folder: Optional[str] = None,
) -> None:
    """Find entities matching given field criteria.
    
    Args:
        entity_type: The entity type (e.g., User, Transfer, Mandate)
        filters: List of field=value filters (e.g., "id=system" "status=active")
        network: Network to use
        canister: Canister to connect to
        folder: Realm folder containing dfx.json
    """
    require_dfx()
    
    effective_network, effective_canister = get_effective_network_and_canister(
        network, canister
    )
    effective_cwd = get_effective_cwd(folder)

    explorer = CursorDatabaseExplorer(effective_network, effective_canister, cwd=effective_cwd)
    
    _connect(explorer)
    
    # Check if this is a namespaced extension entity (e.g., "vault::KnownSubaccount")
    is_extension_entity = "::" in entity_type
    
    if is_extension_entity:
        query_type = entity_type
    else:
        # Find the matching class for core entities
        matching_class = None
        for cls in explorer._ggg_classes:
            if cls.__name__.lower() == entity_type.lower():
                matching_class = cls
                break
        
        if not matching_class:
            available = [cls.__name__ for cls in explorer._ggg_classes]
            console.print(f"[red]Error: Entity type '{entity_type}' not found[/red]")
            console.print(f"[yellow]Available entity types: {', '.join(sorted(available))}[/yellow]")
            raise typer.Exit(1)
        
        query_type = matching_class.__name__
    
    # Parse filters into (field, value) tuples
    params = []
    for f in filters:
        if "=" not in f:
            console.print(f"[red]Error: Invalid filter format '{f}'. Use field=value[/red]")
            raise typer.Exit(1)
        field, value = f.split("=", 1)
        params.append((field.strip(), value.strip()))
    
    if not params:
        console.print("[red]Error: At least one filter is required[/red]")
        console.print("[yellow]Usage: realms db find <EntityType> <field=value> ...[/yellow]")
        raise typer.Exit(1)
    
    result = explorer.find_entities(query_type, params)
    
    if "error" in result and result["error"]:
        console.print(f"[red]Error: {result['error']}[/red]")
        raise typer.Exit(1)
    
    items = result.get("items", [])
    print(json.dumps(items, indent=2))


def db_schema_command(
    network: Optional[str] = None,
    canister: Optional[str] = None,
    folder: Optional[str] = None,
) -> None:
    """Get the database schema showing all entity types, their fields, and relationships.
    
    Outputs JSON with entity types, field definitions, and relationship mappings.
    This is useful for discovering what data types are available in the realm.
    
    Args:
        network: Network to use
        canister: Canister to connect to
        folder: Realm folder containing dfx.json
    
    Note:
        This command does not require dfx as it only inspects the local GGG model definitions.
    """
    effective_network, effective_canister = get_effective_network_and_canister(
        network, canister
    )
    effective_cwd = get_effective_cwd(folder)

    explorer = CursorDatabaseExplorer(effective_network, effective_canister, cwd=effective_cwd)
    
    # Discover all entity classes
    ggg_classes = explorer._discover_ggg_classes()
    
    if not ggg_classes:
        console.print("[yellow]Warning: No entity classes discovered. Schema may be incomplete.[/yellow]")
    
    # Discover relationship fields
    relationship_fields = explorer._discover_relationship_fields()
    
    schema = {"entities": {}}
    
    for cls in sorted(ggg_classes, key=lambda c: c.__name__):
        entity_name = cls.__name__
        entity_schema = {
            "fields": {},
            "relationships": {}
        }
        
        # Get field types from annotations
        annotations = getattr(cls, '__annotations__', {})
        for field_name, field_type in annotations.items():
            # Convert type to string representation
            if hasattr(field_type, '__name__'):
                type_str = field_type.__name__
            elif hasattr(field_type, '__origin__'):
                # Handle generic types like Optional[str], List[int]
                origin = getattr(field_type, '__origin__', None)
                args = getattr(field_type, '__args__', ())
                if origin:
                    origin_name = getattr(origin, '__name__', str(origin))
                    if args:
                        args_str = ', '.join(
                            getattr(a, '__name__', str(a)) for a in args
                        )
                        type_str = f"{origin_name}[{args_str}]"
                    else:
                        type_str = origin_name
                else:
                    type_str = str(field_type)
            else:
                type_str = str(field_type)
            
            entity_schema["fields"][field_name] = type_str
        
        # Get relationships by inspecting class attributes
        for attr_name in dir(cls):
            if attr_name.startswith('_'):
                continue
            try:
                attr = getattr(cls, attr_name, None)
                if attr is None:
                    continue
                
                attr_type = type(attr).__name__
                if attr_type in ["ManyToOne", "OneToOne", "OneToMany", "ManyToMany"]:
                    # Extract relationship info
                    related_model = None
                    if hasattr(attr, "entity_types"):
                        related_model = attr.entity_types
                    elif hasattr(attr, "_args") and len(attr._args) >= 1:
                        related_model = attr._args[0]
                    elif hasattr(attr, "args") and len(attr.args) >= 1:
                        related_model = attr.args[0]
                    
                    if related_model:
                        entity_schema["relationships"][attr_name] = {
                            "type": attr_type,
                            "target": related_model
                        }
            except Exception:
                continue
        
        schema["entities"][entity_name] = entity_schema
    
    # Output schema as JSON
    print(json.dumps(schema, indent=2))


def db_command(
    network: Optional[str] = typer.Option(
        None, "--network", "-n", help="Network to use (overrides context)"
    ),
    canister: Optional[str] = typer.Option(
        None, "--canister", "-c", help="Canister name to connect to (overrides context)"
    ),
    folder: Optional[str] = typer.Option(
        None, "--folder", "-f", help="Realm folder containing dfx.json (uses current realm context if not specified)"
    ),
) -> None:
    """Explore the Realm database in an interactive text-based interface with cursor navigation."""
    require_dfx()

    logger.debug("db_command")
    logger.debug(f"network: {network}")
    logger.debug(f"canister: {canister}")
    logger.debug(f"folder: {folder}")

    effective_network, effective_canister = get_effective_network_and_canister(
        network, canister
    )
    effective_cwd = get_effective_cwd(folder)

    logger.debug(f"Effective network: {effective_network}")
    logger.debug(f"Effective canister: {effective_canister}")
    logger.debug(f"Effective cwd: {effective_cwd}")

    explorer = CursorDatabaseExplorer(effective_network, effective_canister, cwd=effective_cwd)
    
    # Check if running in non-interactive mode (stdin is not a TTY)
    # In this case, print a simple list of entity types and counts instead of running the interactive app
    if not sys.stdin.isatty():
        logger.debug("Non-interactive mode detected, printing entity list")
        _connect(explorer)
        
        explorer._fetch_entity_counts()
        
        print("Realm Database Explorer")
        print("Available entity types:")
        print()
        
        sorted_classes = sorted(explorer._ggg_classes, key=lambda cls: cls.__name__)
        for cls in sorted_classes:
            count = explorer._entity_counts.get(cls.__name__, 0)
            print(f"  {cls.__name__} ({count})")
        
        print()
        print("Use 'realms db get <EntityType>' to query entities")
        return
    
    explorer.run()
