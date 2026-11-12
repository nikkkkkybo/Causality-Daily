"""OS-bound encryption for the local vault (Windows DPAPI).

Standard library only. On Windows, DPAPI ties the ciphertext to the current
user account and machine; copying the database elsewhere should fail to
decrypt. On other platforms this module reports unavailable and callers must
refuse to store vault data rather than fall back to plaintext.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

CRYPTPROTECT_UI_FORBIDDEN = 0x1
_AVAILABLE = sys.platform == "win32"


class VaultUnavailable(RuntimeError):
    """Raised when the platform cannot provide local vault encryption."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def available() -> bool:
    return _AVAILABLE


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data) or 1)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _library():
    if not _AVAILABLE:
        raise VaultUnavailable("当前平台不支持本地保险箱加密（需要 Windows DPAPI）")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def protect(data: bytes) -> bytes:
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("vault data must be bytes")
    crypt32, kernel32 = _library()
    source, keep = _blob(bytes(data))
    out = _DataBlob()
    if not crypt32.CryptProtectData(
            ctypes.byref(source), None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        raise VaultUnavailable(f"保险箱加密失败（WinError {ctypes.get_last_error()}）")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def unprotect(blob: bytes) -> bytes:
    if not isinstance(blob, (bytes, bytearray)):
        raise ValueError("vault cipher must be bytes")
    crypt32, kernel32 = _library()
    source, keep = _blob(bytes(blob))
    out = _DataBlob()
    if not crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        raise VaultUnavailable(
            "保险箱无法解锁：可能换了机器或 Windows 用户，或数据已损坏")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))
