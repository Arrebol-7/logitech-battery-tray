"""纯 ctypes 封装 Windows Shell 操作：快捷方式（.lnk）、桌面链接（符号链接/junction/硬链接）。

两套机制，用途不同：
  * .lnk 快捷方式  —— 走 COM（IShellLinkW + IPersistFile），用于「启动」文件夹开机自启。
  * 桌面链接        —— 用 NTFS 真链接（PROMPT.md §一.6 要求「不要复制脚本」）：
      目录优先 os.symlink（需管理员/开发者模式）→ 降级 mklink /J（junction，无需权限）
      文件优先 os.symlink → 降级 os.link（硬链接，同卷即可，无需权限）

不使用 pywin32 / comtypes（PROMPT.md 硬性约束：纯标准库）。

实现要点（都是踩过的坑）：
  - COM 方法按 vtable 下标调用，this 指针 + 参数用 c_void_p 统一承载；
  - 字符串参数必须传 c_wchar_p(a) 实例（传裸 str 会当指针值导致访问违例）；
  - IPersistFile::Save 的 fRemember 是 BOOL(32 位) 值，绝不能当指针传；
  - IShellLinkW vtable 从 IUnknown 之后严格计数（SetPath=20，不是 23）。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import POINTER, byref, c_void_p, c_wchar_p, wintypes

ole32 = ctypes.OleDLL("ole32")
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

CLSCTX_INPROC_SERVER = 0x1
COINIT_APARTMENTTHREADED = 0x2
S_OK = 0

CLSID_ShellLink = "{00021401-0000-0000-C000-000000000046}"
IID_IShellLinkW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPersistFile = "{0000010b-0000-0000-C000-000000000046}"

HRESULT = ctypes.c_long
ULONG = ctypes.c_ulong


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, s):
        s = s.strip("{}")
        p = s.split("-")
        self.Data1 = int(p[0], 16)
        self.Data2 = int(p[1], 16)
        self.Data3 = int(p[2], 16)
        self.Data4 = (ctypes.c_ubyte * 8)(*bytes.fromhex(p[3] + p[4]))


def _method(ptr, index, restype, *argtypes):
    """取 COM 对象 vtable 第 index 项，按给定 argtypes 建可调用原型。"""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(vtbl[index])


# ================================================================ .lnk 快捷方式
def create_shortcut(lnk_path, target, arguments="", workdir=None, desc=""):
    """创建 .lnk。target 可以是 exe 或目录；arguments 是命令行参数。"""
    workdir = workdir or os.path.dirname(target)
    lnk_path = os.path.abspath(lnk_path)
    ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)

    clsid = GUID(CLSID_ShellLink)
    iid_link = GUID(IID_IShellLinkW)
    iid_file = GUID(IID_IPersistFile)

    link = c_void_p()
    hr = ole32.CoCreateInstance(byref(clsid), None, CLSCTX_INPROC_SERVER,
                                byref(iid_link), byref(link))
    if hr != S_OK:
        raise OSError(f"CoCreateInstance 失败 hr=0x{hr & 0xFFFFFFFF:08X}")

    # IShellLinkW vtable（this 为隐式首参）:
    #   0 QI, 1 AddRef, 2 Release,
    #   3 GetPath, 4 GetIDList, 5 SetIDList, 6 GetDescription, 7 SetDescription,
    #   8 GetWorkingDirectory, 9 SetWorkingDirectory, 10 GetArguments,
    #   11 SetArguments, 12 GetHotkey, 13 SetHotkey, 14 GetShowCmd,
    #   15 SetShowCmd, 16 GetIconLocation, 17 SetIconLocation,
    #   18 SetRelativePath, 19 Resolve, 20 SetPath
    try:
        _method(link, 20, HRESULT, ctypes.c_wchar_p)(link, c_wchar_p(target))
        _method(link, 11, HRESULT, ctypes.c_wchar_p)(link, c_wchar_p(arguments))
        _method(link, 9, HRESULT, ctypes.c_wchar_p)(link, c_wchar_p(workdir))
        _method(link, 7, HRESULT, ctypes.c_wchar_p)(link, c_wchar_p(desc))

        ppf = c_void_p()
        hr = _method(link, 0, HRESULT, POINTER(GUID), POINTER(c_void_p))(
            link, byref(iid_file), byref(ppf))
        if hr != S_OK:
            raise OSError(f"QueryInterface(IPersistFile) 失败 hr=0x{hr & 0xFFFFFFFF:08X}")

        # IPersistFile vtable: 0 QI,1 AddRef,2 Release,3 GetClassID,4 IsDirty,
        #                      5 Load,6 Save,7 SaveCompleted,8 GetCurFile
        try:
            hr = _method(ppf, 6, HRESULT, ctypes.c_wchar_p, wintypes.BOOL)(
                ppf, c_wchar_p(lnk_path), True)
            if hr != S_OK:
                raise OSError(f"IPersistFile::Save 失败 hr=0x{hr & 0xFFFFFFFF:08X}")
        finally:
            _method(ppf, 2, ULONG)(ppf)
    finally:
        _method(link, 2, ULONG)(link)
    return lnk_path


def remove_shortcut(lnk_path):
    try:
        os.remove(lnk_path)
    except FileNotFoundError:
        pass


def autostart_target(here):
    """返回 (pythonw.exe 路径, 脚本路径)，用于开机自启（绝不弹黑窗）。"""
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = exe
    return pythonw, os.path.join(here, "start_tray.pyw")


# ================================================================ NTFS 真链接
def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                                   # noqa: BLE001
        return False


def make_dir_link(link_path, target):
    """建目录链接：优先符号链接，无权限时降级 junction（mklink /J）。

    返回 ("symlink"|"junction", 实际路径)。两者在资源管理器里都表现为
    可进入的目录，且不复制内容 —— 符合 PROMPT.md「不要复制脚本」。
    """
    target = os.path.abspath(target)
    link_path = os.path.abspath(link_path)
    if os.path.exists(link_path) or os.path.islink(link_path):
        _remove_dir_link(link_path)

    try:
        os.symlink(target, link_path, target_is_directory=True)
        return "symlink", link_path
    except (OSError, NotImplementedError):
        pass

    # junction：不需要管理员权限，但要求目标与链接在同一卷（NTFS）
    _mklink("/J", link_path, target)
    if _is_reparse_point(link_path):
        return "junction", link_path
    # 跨卷时 junction 也不行 → .lnk（仍是指针，不复制内容）
    create_shortcut(link_path + ".lnk", target, "", target,
                    "2.4G 无线外设电量工具目录")
    return "shortcut", link_path + ".lnk"


def make_file_link(link_path, target):
    """建文件链接：符号链接 → 硬链接（同卷）→ .lnk 快捷方式（跨卷兜底）。

    返回 (类型, 实际路径)。
    """
    target = os.path.abspath(target)
    link_path = os.path.abspath(link_path)
    if os.path.exists(link_path) or os.path.islink(link_path):
        _remove_file_link(link_path)

    try:
        os.symlink(target, link_path)
        return "symlink", link_path
    except (OSError, NotImplementedError):
        pass

    try:
        os.link(target, link_path)
        return "hardlink", link_path
    except OSError:
        pass

    # 跨卷 + 无符号链接权限 → .lnk（用 pythonw 启动，无黑框）
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    lnk = link_path + ".lnk"
    create_shortcut(lnk, pythonw, f'"{target}"', os.path.dirname(target),
                    "启动 2.4G 无线外设电量托盘")
    return "shortcut", lnk


def _mklink(flag, link_path, target):
    """调用 cmd 的 mklink（junction 只能这样建）。输出忽略编码差异。"""
    try:
        subprocess.run(["cmd", "/c", "mklink", flag, link_path, target],
                       capture_output=True, errors="replace", shell=False)
    except Exception:                                       # noqa: BLE001
        pass


def _is_reparse_point(path):
    """判断路径是否是重解析点（junction 的判据）。"""
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        return attrs != -1 and bool(attrs & 0x400)
    except Exception:                                       # noqa: BLE001
        return False


def _remove_dir_link(path):
    """删除目录链接本身，绝不递归删除目标内容。"""
    if not os.path.islink(path):
        # junction 的 islink() 在部分 Python 版本返回 False。
        # 用 reparse 属性 + rmdir 双重判断：rmdir 只对「空的链接本身」成功，
        # 真目录里还有文件时会报错 —— 因此绝不会误删用户数据。
        if _is_reparse_point(path):
            try:
                os.rmdir(path)
                return
            except OSError:
                pass
            subprocess.run(["cmd", "/c", "rmdir", path], capture_output=True,
                           errors="replace", shell=False)
            return
        return
    try:
        os.unlink(path)
    except (OSError, PermissionError):
        subprocess.run(["cmd", "/c", "rmdir", path], capture_output=True,
                       errors="replace", shell=False)


def _remove_file_link(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def link_kind(path):
    """判断链接类型：'symlink' / 'junction' / 'hardlink' / 'shortcut' / None。"""
    if path.lower().endswith(".lnk") and os.path.exists(path):
        return "shortcut"
    if not os.path.exists(path) and not os.path.islink(path):
        return None
    if os.path.islink(path):
        return "symlink"
    if _is_reparse_point(path):
        return "junction"
    try:
        st = os.stat(path)
        if st.st_nlink > 1:
            return "hardlink"
    except OSError:
        pass
    return None


def resolve_link_display(link_path):
    """给桌面入口用：返回 (类型, 实际落地的路径)。自动处理 .lnk 变体。"""
    if os.path.exists(link_path):
        return link_kind(link_path), link_path
    lnk = link_path + ".lnk"
    if os.path.exists(lnk):
        return "shortcut", lnk
    return None, link_path


# ================================================================ 桌面入口
def desktop_dir():
    buf = ctypes.create_unicode_buffer(260)
    shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)   # CSIDL_DESKTOPDIRECTORY
    return buf.value


DIR_LINK_NAME = "2.4G电量工具"
FILE_LINK_NAME = "启动电量托盘"


def make_desktop_links(here):
    """桌面建「目录链接」+「文件链接」（PROMPT.md §一.6 / 验收标准 4）。

    目录链接：指向项目目录，可直接进去看源码，不复制任何东西。
    文件链接：指向启动脚本，双击即启动托盘。

    降级链（本机实测：无管理员权限、无开发者模式、桌面在 C: 而项目在 D:）：
      目录: symlink → junction → .lnk
      文件: symlink → hardlink → .lnk
    """
    desktop = desktop_dir()
    here = os.path.abspath(here)
    made = []

    dir_link = os.path.join(desktop, DIR_LINK_NAME)
    kind_d, path_d = make_dir_link(dir_link, here)
    made.append((kind_d, path_d, here))

    file_link = os.path.join(desktop, FILE_LINK_NAME)
    kind_f, path_f = make_file_link(file_link, os.path.join(here, "start_tray.pyw"))
    made.append((kind_f, path_f, os.path.join(here, "start_tray.pyw")))

    for kind, path, target in made:
        print(f"已创建 [{kind}]: {path}")
        print(f"           -> {target}")
    if any(k != "symlink" for k, _, _ in made):
        print("\n说明：当前账户无符号链接权限（未开开发者模式），已自动降级：")
        print("      junction / .lnk 都是「指针」，不复制任何脚本内容；")
        print("      资源管理器里表现与符号链接一致，双击可进入/启动。")
    return 0


def remove_desktop_links():
    desktop = desktop_dir()
    for name, remover in ((DIR_LINK_NAME, _remove_dir_link),
                          (FILE_LINK_NAME, _remove_file_link)):
        for p in (os.path.join(desktop, name), os.path.join(desktop, name + ".lnk")):
            if os.path.exists(p) or os.path.islink(p):
                if p.lower().endswith(".lnk"):
                    _remove_file_link(p)
                else:
                    remover(p)
                print(f"已删除: {p}")