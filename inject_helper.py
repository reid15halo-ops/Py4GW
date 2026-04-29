"""32-bit DLL injector helper. Called by the 64-bit launcher to inject into 32-bit GW."""
import ctypes
import ctypes.wintypes
import sys
import os

PROCESS_ALL_ACCESS = 0x1F0FFF
VIRTUAL_MEM = 0x1000 | 0x2000  # MEM_COMMIT | MEM_RESERVE
PAGE_READWRITE = 0x04
WAIT_OBJECT_0 = 0x00000000
MEM_RELEASE = 0x8000

kernel32 = ctypes.windll.kernel32

# Set proper argtypes/restype to avoid pointer truncation on 32-bit
kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE

kernel32.GetProcAddress.argtypes = [ctypes.wintypes.HMODULE, ctypes.c_char_p]
kernel32.GetProcAddress.restype = ctypes.c_void_p

kernel32.VirtualAllocEx.argtypes = [
    ctypes.wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.wintypes.DWORD, ctypes.wintypes.DWORD
]
kernel32.VirtualAllocEx.restype = ctypes.c_void_p

kernel32.WriteProcessMemory.argtypes = [
    ctypes.wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)
]
kernel32.WriteProcessMemory.restype = ctypes.wintypes.BOOL

kernel32.CreateRemoteThread.argtypes = [
    ctypes.wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.wintypes.DWORD,
    ctypes.POINTER(ctypes.wintypes.DWORD)
]
kernel32.CreateRemoteThread.restype = ctypes.wintypes.HANDLE

kernel32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
kernel32.WaitForSingleObject.restype = ctypes.wintypes.DWORD

kernel32.GetExitCodeThread.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(ctypes.wintypes.DWORD)]
kernel32.GetExitCodeThread.restype = ctypes.wintypes.BOOL

kernel32.VirtualFreeEx.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.wintypes.DWORD]
kernel32.VirtualFreeEx.restype = ctypes.wintypes.BOOL

kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
kernel32.CloseHandle.restype = ctypes.wintypes.BOOL


def inject_dll(pid, dll_path):
    process_handle = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, int(pid))
    if not process_handle:
        print(f"FAIL:Cannot open process {pid}: error {ctypes.GetLastError()}")
        return False

    loadlib_addr = kernel32.GetProcAddress(kernel32._handle, b"LoadLibraryA")
    if not loadlib_addr:
        print("FAIL:Cannot get LoadLibraryA address")
        kernel32.CloseHandle(process_handle)
        return False
    print(f"INFO:LoadLibraryA at {hex(loadlib_addr)}")

    abs_path = os.path.abspath(dll_path)
    dll_path_bytes = abs_path.encode('ascii') + b'\0'
    path_size = len(dll_path_bytes)
    print(f"INFO:DLL path: {abs_path} ({path_size} bytes)")

    allocated = kernel32.VirtualAllocEx(process_handle, None, path_size, VIRTUAL_MEM, PAGE_READWRITE)
    if not allocated:
        print(f"FAIL:Cannot allocate memory: error {ctypes.GetLastError()}")
        kernel32.CloseHandle(process_handle)
        return False
    print(f"INFO:Allocated at {hex(allocated)}")

    written = ctypes.c_size_t(0)
    if not kernel32.WriteProcessMemory(process_handle, allocated, dll_path_bytes, path_size, ctypes.byref(written)):
        print(f"FAIL:Cannot write memory: error {ctypes.GetLastError()}")
        kernel32.VirtualFreeEx(process_handle, allocated, 0, MEM_RELEASE)
        kernel32.CloseHandle(process_handle)
        return False
    print(f"INFO:Wrote {written.value} bytes")

    thread = kernel32.CreateRemoteThread(process_handle, None, 0, loadlib_addr, allocated, 0, None)
    if not thread:
        print(f"FAIL:Cannot create remote thread: error {ctypes.GetLastError()}")
        kernel32.VirtualFreeEx(process_handle, allocated, 0, MEM_RELEASE)
        kernel32.CloseHandle(process_handle)
        return False

    wait_result = kernel32.WaitForSingleObject(thread, 15000)
    if wait_result != WAIT_OBJECT_0:
        print(f"FAIL:WaitForSingleObject returned {wait_result} (expected 0)")
        kernel32.CloseHandle(thread)
        kernel32.VirtualFreeEx(process_handle, allocated, 0, MEM_RELEASE)
        kernel32.CloseHandle(process_handle)
        return False

    exit_code = ctypes.wintypes.DWORD(0)
    kernel32.GetExitCodeThread(thread, ctypes.byref(exit_code))

    kernel32.CloseHandle(thread)
    kernel32.VirtualFreeEx(process_handle, allocated, 0, MEM_RELEASE)
    kernel32.CloseHandle(process_handle)

    if exit_code.value != 0:
        print(f"OK:Injected successfully, module handle: {hex(exit_code.value)}")
        return True
    else:
        print("FAIL:LoadLibraryA returned 0 (DLL failed to load)")
        return False


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("FAIL:Usage: inject_helper.py <pid> <dll_path>")
        sys.exit(1)
    success = inject_dll(int(sys.argv[1]), sys.argv[2])
    sys.exit(0 if success else 1)
