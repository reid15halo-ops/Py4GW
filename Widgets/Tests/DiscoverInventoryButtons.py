"""
DiscoverInventoryButtons.py — Frame discovery widget for the new April 2026 buttons:
  - "Identify All" (in Inventory Window / Hero Panel)
  - "Store All Materials" (in Xunlai Chest)

Usage: Enable this widget in-game, open your Inventory window and/or Xunlai Chest,
then check the Py4GW console for discovered frame hashes.

Once discovered, add the hashes to frame_aliases.json and Inventory.py.
"""

from Py4GWCoreLib import *
from Py4GWCoreLib.UIManager import UIManager

MODULE = "DiscoverButtons"

# Known parent hashes
INVENTORY_WINDOW_HASH = 2821845329  # "Inventory Window.Character Info.Weapon Sets Button" parent area
XUNLAI_WINDOW_HASH = 2315448754     # "Xunlai Window"

_discovered = False


def _walk_children(parent_fid: int, depth: int = 0, max_depth: int = 5) -> list[dict]:
    """Walk frame tree from parent, collecting child info."""
    results = []
    if depth > max_depth or parent_fid <= 0:
        return results

    child_fid = UIManager.GetFirstChildFrameID(parent_fid)
    child_idx = 0
    while child_fid and child_fid > 0:
        try:
            frame = PyUIManager.UIFrame(child_fid)
            path = UIManager.ConstructFramePath(child_fid)
            coords = UIManager.GetFrameCoords(child_fid)
            visible = UIManager.FrameExists(child_fid)

            results.append({
                "frame_id": child_fid,
                "hash": frame.frame_hash if frame.frame_hash else 0,
                "child_offset": frame.child_offset_id if hasattr(frame, 'child_offset_id') else child_idx,
                "path": path,
                "coords": coords,
                "visible": visible,
                "depth": depth,
            })

            # Recurse into children
            results.extend(_walk_children(child_fid, depth + 1, max_depth))
        except Exception as e:
            Py4GW.Console.Log(MODULE, f"Error walking child {child_fid}: {e}", Py4GW.Console.MessageType.Warning)

        child_fid = UIManager.GetNextChildFrameID(child_fid)
        child_idx += 1

        if child_idx > 50:  # Safety limit
            break

    return results


def _discover_from_hash(parent_hash: int, window_name: str):
    """Discover all children of a known window hash."""
    parent_fid = UIManager.GetFrameIDByHash(parent_hash)
    if parent_fid <= 0:
        return

    Py4GW.Console.Log(MODULE, f"=== {window_name} (hash={parent_hash}, fid={parent_fid}) ===", Py4GW.Console.MessageType.Info)

    children = _walk_children(parent_fid, depth=0, max_depth=4)

    for c in children:
        indent = "  " * c["depth"]
        hash_str = f"hash={c['hash']}" if c['hash'] else "no-hash"
        Py4GW.Console.Log(
            MODULE,
            f"{indent}[{c['child_offset']}] fid={c['frame_id']} {hash_str} path={c['path']} visible={c['visible']}",
            Py4GW.Console.MessageType.Info,
        )


def _discover_inventory_root():
    """Try to find the Inventory Window root and walk its children."""
    # The Inventory Window itself — try common label
    fid = UIManager.GetFrameIDByLabel("Inventory")
    if fid > 0:
        Py4GW.Console.Log(MODULE, f"=== Inventory by label (fid={fid}) ===", Py4GW.Console.MessageType.Info)
        children = _walk_children(fid, depth=0, max_depth=3)
        for c in children:
            indent = "  " * c["depth"]
            hash_str = f"hash={c['hash']}" if c['hash'] else "no-hash"
            Py4GW.Console.Log(MODULE, f"{indent}[{c['child_offset']}] fid={c['frame_id']} {hash_str} path={c['path']} visible={c['visible']}", Py4GW.Console.MessageType.Info)

    # Also try "Hero" panel
    for label in ["Hero", "Hero Panel", "Character", "Equipment"]:
        fid = UIManager.GetFrameIDByLabel(label)
        if fid > 0:
            Py4GW.Console.Log(MODULE, f"=== '{label}' by label (fid={fid}) ===", Py4GW.Console.MessageType.Info)
            children = _walk_children(fid, depth=0, max_depth=3)
            for c in children:
                indent = "  " * c["depth"]
                hash_str = f"hash={c['hash']}" if c['hash'] else "no-hash"
                Py4GW.Console.Log(MODULE, f"{indent}[{c['child_offset']}] fid={c['frame_id']} {hash_str} path={c['path']} visible={c['visible']}", Py4GW.Console.MessageType.Info)


def main():
    global _discovered

    if _discovered:
        return

    try:
        if not Map.IsMapReady():
            return

        Py4GW.Console.Log(MODULE, "Starting frame discovery for new Inventory/Xunlai buttons...", Py4GW.Console.MessageType.Info)

        # Discover Xunlai Window children (Store All Materials button)
        _discover_from_hash(XUNLAI_WINDOW_HASH, "Xunlai Window")

        # Discover Inventory Window area
        _discover_inventory_root()

        # Also try the full hierarchy approach for any frame containing "Identify" or "Material"
        try:
            hierarchy = UIManager.GetFrameHierarchy()
            if hierarchy:
                Py4GW.Console.Log(MODULE, f"Full hierarchy has {len(hierarchy)} entries", Py4GW.Console.MessageType.Info)
                for key, val in hierarchy.items() if isinstance(hierarchy, dict) else []:
                    val_str = str(val).lower()
                    if any(kw in val_str for kw in ["identify", "material", "deposit", "store"]):
                        Py4GW.Console.Log(MODULE, f"  MATCH: {key} = {val}", Py4GW.Console.MessageType.Success)
        except Exception as e:
            Py4GW.Console.Log(MODULE, f"Hierarchy scan error: {e}", Py4GW.Console.MessageType.Warning)

        _discovered = True
        Py4GW.Console.Log(MODULE, "Discovery complete. Check output above for new button frames.", Py4GW.Console.MessageType.Success)

    except Exception as e:
        Py4GW.Console.Log(MODULE, f"Discovery error: {e}", Py4GW.Console.MessageType.Error)


if __name__ == "__main__":
    main()
