"""Windows 专用的受监督进程；创建时入 Job，不存在先运行再附着的窗口。

生命周期隔离，不是 OS 沙箱。仅供宿主调用，绝不注册为模型工具。
"""

import ctypes as c
import os
import subprocess
import time
from ctypes import wintypes as w
from pathlib import Path


class _BasicLimits(c.Structure):
    _fields_ = [
        ("process_time", c.c_int64),
        ("job_time", c.c_int64),
        ("flags", w.DWORD),
        ("min_ws", c.c_size_t),
        ("max_ws", c.c_size_t),
        ("active", w.DWORD),
        ("affinity", c.c_size_t),
        ("priority", w.DWORD),
        ("scheduling", w.DWORD),
    ]


class _IoCounters(c.Structure):
    _fields_ = [
        (name, c.c_uint64)
        for name in (
            "read_ops",
            "write_ops",
            "other_ops",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        )
    ]


class _Limits(c.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io", _IoCounters),
        ("process_memory", c.c_size_t),
        ("job_memory", c.c_size_t),
        ("peak_process", c.c_size_t),
        ("peak_job", c.c_size_t),
    ]


class _Accounting(c.Structure):
    _fields_ = [
        ("user", c.c_int64),
        ("kernel", c.c_int64),
        ("period_user", c.c_int64),
        ("period_kernel", c.c_int64),
        ("page_faults", w.DWORD),
        ("total", w.DWORD),
        ("active", w.DWORD),
        ("terminated", w.DWORD),
    ]


class _ProcessIds(c.Structure):
    _fields_ = [("assigned", w.DWORD), ("count", w.DWORD), ("ids", c.c_size_t * 4096)]


class _Startup(c.Structure):
    _fields_ = [
        ("cb", w.DWORD),
        ("reserved", w.LPWSTR),
        ("desktop", w.LPWSTR),
        ("title", w.LPWSTR),
        ("x", w.DWORD),
        ("y", w.DWORD),
        ("x_size", w.DWORD),
        ("y_size", w.DWORD),
        ("x_chars", w.DWORD),
        ("y_chars", w.DWORD),
        ("fill", w.DWORD),
        ("flags", w.DWORD),
        ("show", w.WORD),
        ("reserved_size", w.WORD),
        ("reserved_ptr", c.c_void_p),
        ("stdin", w.HANDLE),
        ("stdout", w.HANDLE),
        ("stderr", w.HANDLE),
    ]


class _StartupEx(c.Structure):
    _fields_ = [("startup", _Startup), ("attributes", c.c_void_p)]


class _ProcessInfo(c.Structure):
    _fields_ = [
        ("process", w.HANDLE),
        ("thread", w.HANDLE),
        ("pid", w.DWORD),
        ("tid", w.DWORD),
    ]


class EvidenceProcess:
    def __init__(self, command: list[str], *, cwd: Path, env: dict[str, str]):
        """创建固定宿主入口的子进程，并只传递标准流句柄。

        Note:
            Job 句柄不可继承且仅由本对象持有。Windows 10+ 的 JOB_LIST 属性
            使子进程在执行第一条指令前就属于 Job；父进程硬退出也会清理子树。
            调用方须在 finally 中 close；不支持的平台和系统调用失败均关闭入口。
        """
        if os.name != "nt":
            raise OSError("evidence worker requires Windows 10+ Job Objects")
        import msvcrt

        self.api = c.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": (
                [w.HANDLE, c.c_int, c.c_void_p, w.DWORD],
                w.BOOL,
            ),
            "InitializeProcThreadAttributeList": (
                [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)],
                w.BOOL,
            ),
            "UpdateProcThreadAttribute": (
                [
                    c.c_void_p,
                    w.DWORD,
                    c.c_size_t,
                    c.c_void_p,
                    c.c_size_t,
                    c.c_void_p,
                    c.c_void_p,
                ],
                w.BOOL,
            ),
            "DeleteProcThreadAttributeList": ([c.c_void_p], None),
            "CreateProcessW": (
                [
                    w.LPCWSTR,
                    w.LPWSTR,
                    c.c_void_p,
                    c.c_void_p,
                    w.BOOL,
                    w.DWORD,
                    c.c_void_p,
                    w.LPCWSTR,
                    c.POINTER(_StartupEx),
                    c.POINTER(_ProcessInfo),
                ],
                w.BOOL,
            ),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "GetExitCodeProcess": ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "QueryInformationJobObject": (
                [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p],
                w.BOOL,
            ),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.api, name)
            fn.argtypes, fn.restype = args, result
        self.job = self.api.CreateJobObjectW(None, None)
        self.process = None
        if not self.job:
            raise c.WinError(c.get_last_error())
        streams = []
        attributes = None
        initialized = False
        try:
            limits = _Limits()
            limits.basic.flags = (
                0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
            )
            self._check(
                self.api.SetInformationJobObject(
                    self.job, 9, c.byref(limits), c.sizeof(limits)
                )
            )
            streams = [
                open(os.devnull, "rb"),
                (cwd / "stdout.bin").open("xb"),
                (cwd / "stderr.bin").open("xb"),
            ]
            handles = [msvcrt.get_osfhandle(stream.fileno()) for stream in streams]
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            size = c.c_size_t()
            self.api.InitializeProcThreadAttributeList(None, 2, 0, c.byref(size))
            attributes = c.create_string_buffer(size.value)
            self._check(
                self.api.InitializeProcThreadAttributeList(
                    attributes, 2, 0, c.byref(size)
                )
            )
            initialized = True
            jobs = (w.HANDLE * 1)(self.job)
            inherited = (w.HANDLE * 3)(*handles)
            self._check(
                self.api.UpdateProcThreadAttribute(
                    attributes, 0, 0x2000D, jobs, c.sizeof(jobs), None, None
                )
            )
            self._check(
                self.api.UpdateProcThreadAttribute(
                    attributes, 0, 0x20002, inherited, c.sizeof(inherited), None, None
                )
            )
            startup = _StartupEx()
            startup.startup.cb = c.sizeof(startup)
            startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = (
                handles
            )
            startup.attributes = c.cast(attributes, c.c_void_p)
            info = _ProcessInfo()
            environment = c.create_unicode_buffer(
                "\0".join(f"{k}={v}" for k, v in sorted(env.items())) + "\0\0"
            )
            command_line = c.create_unicode_buffer(subprocess.list2cmdline(command))
            # EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW
            self._check(
                self.api.CreateProcessW(
                    command[0],
                    command_line,
                    None,
                    None,
                    True,
                    0x08080400,
                    environment,
                    str(cwd),
                    c.byref(startup),
                    c.byref(info),
                )
            )
            self.process, self.pid = info.process, info.pid
            self.api.CloseHandle(info.thread)
        except BaseException:
            self.close()
            raise
        finally:
            if initialized:
                self.api.DeleteProcThreadAttributeList(attributes)
            for stream in streams:
                stream.close()

    @staticmethod
    def _check(ok):
        if not ok:
            raise c.WinError(c.get_last_error())

    def poll(self) -> int | None:
        state = self.api.WaitForSingleObject(self.process, 0)
        if state == 258:
            return None
        if state != 0:
            raise c.WinError(c.get_last_error())
        code = w.DWORD()
        self._check(self.api.GetExitCodeProcess(self.process, c.byref(code)))
        return code.value

    def close(self):
        if self.job:
            members = []
            try:
                ids = _ProcessIds()
                self._check(
                    self.api.QueryInformationJobObject(
                        self.job, 3, c.byref(ids), c.sizeof(ids), None
                    )
                )
                for pid in ids.ids[: ids.count]:
                    handle = self.api.OpenProcess(
                        0x100000, False, pid
                    )  # SYNCHRONIZE only
                    if handle:
                        members.append(handle)
                self._check(self.api.TerminateJobObject(self.job, 1))
                deadline = time.monotonic() + 5
                while True:
                    accounting = _Accounting()
                    self._check(
                        self.api.QueryInformationJobObject(
                            self.job, 1, c.byref(accounting), c.sizeof(accounting), None
                        )
                    )
                    if accounting.active == 0:
                        break
                    if time.monotonic() >= deadline:
                        raise OSError(
                            "worker Job cleanup did not settle within five seconds"
                        )
                    time.sleep(0.01)
                # ActiveProcesses can reach zero before the process objects signal.
                for handle in members:
                    milliseconds = max(0, int((deadline - time.monotonic()) * 1000))
                    if self.api.WaitForSingleObject(handle, milliseconds) != 0:
                        raise OSError("worker member exit has not settled")
            finally:
                self.api.CloseHandle(self.job)
                self.job = None
                for handle in members:
                    self.api.CloseHandle(handle)
        if self.process:
            self.api.WaitForSingleObject(self.process, 5000)
            self.api.CloseHandle(self.process)
            self.process = None
