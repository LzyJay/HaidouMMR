"""读取 LeagueClientUx.exe 进程命令行, 提取 LCU 的 --app-port / --remoting-auth-token。

WeGame 渠道版的 lockfile 常年不刷新, 只能从进程命令行取。

两条取命令行的路子
------------------
1. **NtQueryInformationProcess(ProcessCommandLineInformation=60)** —— 首选。
   只要 PROCESS_QUERY_LIMITED_INFORMATION 权限, **不需要 VM_READ**。
   所以它在沙箱里、在受反作弊保护的进程上都能用。

   ⚠️ 关键: 缓冲区必须够大。内核若嫌小会返回
   STATUS_INFO_LENGTH_MISMATCH(0xC0000004), 并通过 ReturnLength 告知实际需要多少。
   实测命令行约 1400 字符 ⇒ 需要 ~2900 字节。只传 sizeof(UNICODE_STRING)=16
   必然失败, 然后就会掉进下面第 2 条 —— 而那条在很多环境下是被拒的,
   于是表现为「明明客户端在跑, 却说未找到客户端」。

2. **PEB + ReadProcessMemory** —— 兜底, 给不支持信息类 60 的老系统。
   需要 PROCESS_VM_READ。沙箱会拦(ERROR_ACCESS_DENIED=5),
   部分反作弊保护的进程也会拦。
"""
import ctypes
import re
import subprocess
from ctypes import wintypes

ntdll = ctypes.WinDLL('ntdll')
k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.ReadProcessMemory.restype = wintypes.BOOL
k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                  ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]

# 不声明 argtypes/restype, 64 位下传指针会被截断
ntdll.NtQueryInformationProcess.restype = ctypes.c_long
ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            ctypes.c_void_p, ctypes.c_ulong,
                                            ctypes.POINTER(ctypes.c_ulong)]

_PROCESS_QUERY_LIMITED = 0x1000
_CLASS_CMDLINE = 60               # ProcessCommandLineInformation
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004


# --------------------------------------------------------------------------
# 子进程不许弹控制台窗口（2026-09-22 修）
# --------------------------------------------------------------------------
# `tasklist` 是**控制台程序**。从「GUI 子系统」的进程里起它时（打包加了
# `--windowed` 的 HaidouMMR.exe 就是 GUI 子系统），Windows 会**给它新分配一个
# 控制台窗口** —— 用户看到的就是「一抓数据就闪黑窗口」。
# 必须显式关掉，而且是双保险：
#   · `CREATE_NO_WINDOW`  —— 创建时不分配控制台
#   · `STARTF_USESHOWWINDOW` + `SW_HIDE` —— 万一带了窗口也藏起来
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def hidden_kwargs() -> dict:
    """给 `subprocess.run/Popen` 用的「不弹窗」参数包。

    任何从本项目里起的控制台子进程都该带上它，否则打包成 exe 后必然闪黑窗口。
    """
    kw = {"creationflags": _CREATE_NO_WINDOW}
    si_cls = getattr(subprocess, "STARTUPINFO", None)     # 仅 Windows 有
    if si_cls is not None:
        si = si_cls()
        si.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001)
        si.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kw["startupinfo"] = si
    return kw


def _cmdline_via_query(h, size: int = 8192):
    """走信息类 60。只需 QUERY_LIMITED, 不碰 VM_READ。"""
    buf = ctypes.create_string_buffer(size)
    ret = ctypes.c_ulong(0)
    st = ntdll.NtQueryInformationProcess(h, _CLASS_CMDLINE, buf, size,
                                         ctypes.byref(ret))
    if st == _STATUS_INFO_LENGTH_MISMATCH and ret.value > size:
        size = ret.value + 16                 # 按内核报的需求量重来
        buf = ctypes.create_string_buffer(size)
        st = ntdll.NtQueryInformationProcess(h, _CLASS_CMDLINE, buf, size,
                                             ctypes.byref(ret))
    if st != 0:
        return None
    # 返回的是 UNICODE_STRING(Length, MaximumLength, pad, Buffer),
    # 字符串本体紧跟在结构体之后, 且就在**本进程**的这块缓冲区里。
    length = int.from_bytes(buf.raw[0:2], 'little')
    if not length:
        return None
    return buf.raw[16:16 + length].decode('utf-16-le', 'ignore')


class US(ctypes.Structure):
    _fields_ = [('Length', ctypes.c_ushort), ('MaximumLength', ctypes.c_ushort),
                ('Buffer', ctypes.c_wchar_p)]


class PBI(ctypes.Structure):
    _fields_ = [('R1', ctypes.c_void_p), ('Peb', ctypes.c_void_p),
                ('R2', ctypes.c_void_p * 2), ('Pid', ctypes.c_ulonglong),
                ('R3', ctypes.c_void_p)]


def _cmdline_via_peb(h):
    pbi = PBI()
    if ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None) != 0:
        return None
    if not pbi.Peb:
        return None
    buf = ctypes.create_string_buffer(8)
    rd = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(pbi.Peb + 0x20), buf, 8, ctypes.byref(rd)):
        return None
    p = ctypes.c_void_p.from_buffer_copy(buf.raw).value
    if not p:
        return None
    b2 = ctypes.create_string_buffer(16)
    rd2 = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(p + 0x70), b2, 16, ctypes.byref(rd2)):
        return None
    ln = int.from_bytes(b2.raw[0:2], 'little')
    ptr = ctypes.c_void_p.from_buffer_copy(b2.raw[8:16]).value
    if not ln or not ptr:
        return None
    b3 = ctypes.create_string_buffer(ln)
    rd3 = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(ptr), b3, ln, ctypes.byref(rd3)):
        return None
    return b3.raw.decode('utf-16-le', 'ignore')


def cmdline(pid):
    """先试不需要 VM_READ 的信息类 60, 再退回 PEB。"""
    # 路子 1: QUERY_LIMITED 就够, 优先
    h = k32.OpenProcess(_PROCESS_QUERY_LIMITED, False, pid)
    if h:
        try:
            c = _cmdline_via_query(h)
            if c:
                return c
        finally:
            k32.CloseHandle(h)

    # 路子 2: PEB 兜底(老系统 / 信息类 60 不可用时)
    for acc in (0x1000, 0x0410, 0x1F0FFF):
        h = k32.OpenProcess(acc, False, pid)
        if not h:
            continue
        try:
            c = _cmdline_via_peb(h)
            if c:
                return c
        finally:
            k32.CloseHandle(h)
    return None


def find_lcu():
    # ⚠️ `**hidden_kwargs()` 不能省：tasklist 是控制台程序，从 GUI 子系统的
    # exe 里起它会新分配一个控制台窗口（用户看到抓数据时闪黑窗口）。
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq LeagueClientUx.exe', '/FO', 'CSV', '/NH'],
                         capture_output=True, text=True, encoding='gbk', errors='ignore',
                         **hidden_kwargs())
    pids = []
    for line in out.stdout.strip().splitlines():
        m = re.search(r'"([^"]+)","(\d+)"', line)
        if m:
            pids.append(int(m.group(2)))
    for pid in pids:
        cmd = cmdline(pid)
        if not cmd:
            continue
        port = re.search(r'--app-port=([^\s"]+)', cmd)
        tok = re.search(r'--remoting-auth-token=([^\s"]+)', cmd)
        if port and tok:
            return int(port.group(1)), tok.group(1)
    return None, None


if __name__ == '__main__':
    p, t = find_lcu()
    print(f'PORT={p} TOKEN={t}')
