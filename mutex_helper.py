"""32-bit mutex closer for GW multiclient. Finds and closes the GW singleton mutex."""
import ctypes
import ctypes.wintypes
import sys
import time

PROCESS_DUP_HANDLE = 0x0040
PROCESS_QUERY_INFORMATION = 0x0400
DUPLICATE_CLOSE_SOURCE = 0x00000001
DUPLICATE_SAME_ACCESS = 0x00000002
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004

kernel32 = ctypes.windll.kernel32
ntdll = ctypes.windll.ntdll

# NtQueryObject to get handle names
class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("MaximumLength", ctypes.c_ushort),
        ("Buffer", ctypes.c_wchar_p),
    ]

class OBJECT_NAME_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Name", UNICODE_STRING),
    ]

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


def close_gw_mutex(pid, max_wait_s=15.0):
    """Find and close the GW singleton mutex by checking handle names."""
    process_handle = kernel32.OpenProcess(
        PROCESS_DUP_HANDLE | PROCESS_QUERY_INFORMATION,
        False, int(pid)
    )
    if not process_handle:
        print(f"FAIL:Cannot open process {pid}: error {ctypes.GetLastError()}")
        return False

    start = time.time()
    while (time.time() - start) < max_wait_s:
        # Query all system handles
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
            time.sleep(0.5)
            continue

        handle_count = ctypes.c_ulong.from_buffer_copy(buf, 0).value
        entry_size = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO)
        base_offset = ctypes.sizeof(ctypes.c_ulong)

        for i in range(min(handle_count, 500000)):
            try:
                entry = SYSTEM_HANDLE_TABLE_ENTRY_INFO.from_buffer_copy(
                    buf, base_offset + i * entry_size
                )
            except ValueError:
                break

            if entry.OwnerPid != pid:
                continue

            # Only check handles with Mutant-like access (0x1F0001)
            if entry.GrantedAccess != 0x001F0001:
                continue

            # Duplicate the handle to query its name
            dup_handle = ctypes.c_void_p(0)
            ok = kernel32.DuplicateHandle(
                process_handle,
                entry.HandleValue,
                kernel32.GetCurrentProcess(),
                ctypes.byref(dup_handle),
                0, False, DUPLICATE_SAME_ACCESS
            )
            if not ok:
                continue

            # Query the object name
            name_buf = ctypes.create_string_buffer(1024)
            ret_len = ctypes.c_ulong(0)
            status = ntdll.NtQueryObject(
                dup_handle, 1,  # ObjectNameInformation
                name_buf, 1024, ctypes.byref(ret_len)
            )

            handle_name = ""
            if status == 0:
                try:
                    oni = OBJECT_NAME_INFORMATION.from_buffer_copy(name_buf)
                    if oni.Name.Buffer:
                        handle_name = oni.Name.Buffer
                except Exception:
                    pass

            kernel32.CloseHandle(dup_handle)

            # GW mutex name contains "AN-Mutex-Window" or a GUID pattern
            # Look for any mutex with "AN-Mutex" or the classic GW GUID
            is_gw_mutex = False
            if handle_name:
                name_lower = handle_name.lower()
                if "an-mutex" in name_lower:
                    is_gw_mutex = True
                elif "a9a284b0" in name_lower:  # classic GW mutex GUID
                    is_gw_mutex = True
                elif "guild" in name_lower or "gw" in name_lower:
                    is_gw_mutex = True

            if not is_gw_mutex:
                continue

            print(f"INFO:Found GW mutex: '{handle_name}' (type={entry.ObjectType}, handle={entry.HandleValue})")

            # Close the mutex in the target process
            result = kernel32.DuplicateHandle(
                process_handle,
                entry.HandleValue,
                0, None, 0, False,
                DUPLICATE_CLOSE_SOURCE
            )
            if result:
                print(f"OK:Closed GW mutex handle {entry.HandleValue} in PID {pid}")
                kernel32.CloseHandle(process_handle)
                return True
            else:
                print(f"FAIL:DuplicateHandle(CLOSE_SOURCE) failed: {ctypes.GetLastError()}")

        time.sleep(1.0)

    kernel32.CloseHandle(process_handle)
    print(f"FAIL:No GW mutex found in PID {pid} within {max_wait_s}s")
    return False


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("FAIL:Usage: mutex_helper.py <pid>")
        sys.exit(1)
    success = close_gw_mutex(int(sys.argv[1]))
    sys.exit(0 if success else 1)
