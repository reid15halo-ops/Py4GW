"""Debug: list all handle types in a GW process to find the correct Mutant ObjectType."""
import ctypes
import ctypes.wintypes
import sys
from collections import Counter

PROCESS_DUP_HANDLE = 0x0040
PROCESS_QUERY_INFORMATION = 0x0400
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004

kernel32 = ctypes.windll.kernel32
ntdll = ctypes.windll.ntdll


class SYSTEM_HANDLE_TABLE_ENTRY_INFO(ctypes.Structure):
    _fields_ = [
        ("OwnerPid", ctypes.c_ushort),
        ("CreatorBackTraceIndex", ctypes.c_ushort),
        ("ObjectType", ctypes.c_ubyte),
        ("HandleAttributes", ctypes.c_ubyte),
        ("HandleValue", ctypes.c_ushort),
        ("Object", ctypes.c_void_p),
        ("GrantedAccess", ctypes.c_ulong),
    ]


def list_handles(pid):
    buf_size = 0x400000
    for _ in range(8):
        buf = ctypes.create_string_buffer(buf_size)
        return_length = ctypes.c_ulong(0)
        status = ntdll.NtQuerySystemInformation(16, buf, buf_size, ctypes.byref(return_length))
        if status == STATUS_INFO_LENGTH_MISMATCH:
            buf_size *= 2
            continue
        break

    if status != 0:
        print(f"NtQuerySystemInformation failed: {hex(status)}")
        return

    handle_count = ctypes.c_ulong.from_buffer_copy(buf, 0).value
    entry_size = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO)
    base = ctypes.sizeof(ctypes.c_ulong)

    type_counts = Counter()
    type_access = {}

    for i in range(min(handle_count, 500000)):
        try:
            entry = SYSTEM_HANDLE_TABLE_ENTRY_INFO.from_buffer_copy(buf, base + i * entry_size)
        except ValueError:
            break
        if entry.OwnerPid != pid:
            continue
        type_counts[entry.ObjectType] += 1
        if entry.ObjectType not in type_access:
            type_access[entry.ObjectType] = []
        if len(type_access[entry.ObjectType]) < 3:
            type_access[entry.ObjectType].append(
                f"handle={entry.HandleValue} access={hex(entry.GrantedAccess)}"
            )

    print(f"Handles owned by PID {pid}:")
    print(f"{'Type':>6} {'Count':>6}  Sample handles")
    print("-" * 60)
    for t in sorted(type_counts.keys()):
        samples = ", ".join(type_access.get(t, []))
        print(f"{t:>6} {type_counts[t]:>6}  {samples}")
    print(f"\nTotal: {sum(type_counts.values())} handles across {len(type_counts)} types")

    # Mutant types typically have GrantedAccess 0x001F0001 or 0x00100000
    print(f"\nLikely Mutant types (access contains 0x1F0001):")
    for t in sorted(type_counts.keys()):
        for entry_str in type_access.get(t, []):
            if "0x1f0001" in entry_str or "0x100000" in entry_str:
                print(f"  Type {t}: {entry_str}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: debug_handles.py <pid>")
        sys.exit(1)
    list_handles(int(sys.argv[1]))
