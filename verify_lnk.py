"""验证 shell.create_shortcut 生成的 .lnk 能被 Windows 正确解析。

用 IShellLinkW::GetPath(下标 3) / GetArguments(10) / GetDescription(6) 回读。
"""
import ctypes
import os
import sys
import tempfile
from ctypes import POINTER, byref, c_void_p, c_wchar_p, wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shell import (GUID, HRESULT, ULONG, _method, CLSID_ShellLink,
                   IID_IShellLinkW, IID_IPersistFile, create_shortcut,
                   remove_shortcut, ole32, CLSCTX_INPROC_SERVER,
                   COINIT_APARTMENTTHREADED, S_OK)


def load_and_read(lnk_path):
    ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    clsid, iid_link, iid_file = (GUID(CLSID_ShellLink), GUID(IID_IShellLinkW),
                                 GUID(IID_IPersistFile))
    link = c_void_p()
    hr = ole32.CoCreateInstance(byref(clsid), None, CLSCTX_INPROC_SERVER,
                                byref(iid_link), byref(link))
    assert hr == S_OK, hex(hr & 0xFFFFFFFF)
    ppf = c_void_p()
    _method(link, 0, HRESULT, POINTER(GUID), POINTER(c_void_p))(
        link, byref(iid_file), byref(ppf))

    # IPersistFile::Load(下标 5)
    hr = _method(ppf, 5, HRESULT, ctypes.c_wchar_p, wintypes.DWORD)(
        ppf, c_wchar_p(os.path.abspath(lnk_path)), 0)
    assert hr == S_OK, f"Load hr=0x{hr & 0xFFFFFFFF:08X}"

    path_buf = ctypes.create_unicode_buffer(520)
    _method(link, 3, HRESULT, ctypes.c_wchar_p, ctypes.c_int, c_void_p,
            wintypes.DWORD)(link, path_buf, 520, None, 0)      # GetPath
    args_buf = ctypes.create_unicode_buffer(520)
    _method(link, 10, HRESULT, ctypes.c_wchar_p, ctypes.c_int)(
        link, args_buf, 520)                                   # GetArguments
    desc_buf = ctypes.create_unicode_buffer(520)
    _method(link, 6, HRESULT, ctypes.c_wchar_p, ctypes.c_int)(
        link, desc_buf, 520)                                   # GetDescription
    wd_buf = ctypes.create_unicode_buffer(520)
    _method(link, 8, HRESULT, ctypes.c_wchar_p, ctypes.c_int)(
        link, wd_buf, 520)                                     # GetWorkingDirectory

    _method(ppf, 2, ULONG)(ppf)
    _method(link, 2, ULONG)(link)
    return path_buf.value, args_buf.value, desc_buf.value, wd_buf.value


if __name__ == "__main__":
    p = os.path.join(tempfile.gettempdir(), "verify_lnk.lnk")
    remove_shortcut(p)
    create_shortcut(p, sys.executable, "-c pass", os.getcwd(), "测试快捷方式")
    try:
        path, args, desc, wd = load_and_read(p)
        print("GetPath            :", path)
        print("GetArguments       :", args)
        print("GetDescription     :", desc)
        print("GetWorkingDirectory:", wd)
        ok = (path.lower() == sys.executable.lower() and args == "-c pass" and
              desc == "测试快捷方式" and wd == os.getcwd())
        print("\n结果:", "✅ 全部一致 —— .lnk 完全有效" if ok else "❌ 有字段不一致")
        sys.exit(0 if ok else 1)
    finally:
        remove_shortcut(p)