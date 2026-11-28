# SPDX-License-Identifier: Apache-2.0
"""A native console reader must preserve Unicode and let a failed writer end the process."""
import os
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="CPython native Windows console")
@pytest.mark.parametrize("finish", ["eof", "broken_writer"])
@pytest.mark.parametrize("warm", ["fresh", "raw_utf8_tail", "text_cache"])
@pytest.mark.parametrize("utf8_mode", [False, True], ids=["default_encoding", "utf8_mode"])
def test_native_console_unicode_and_shutdown(finish, warm, utf8_mode):
    child = textwrap.dedent(r'''
        import ctypes
        import msvcrt
        import sys
        from ctypes import wintypes
        import anyio
        from mcp import types
        from mcp.shared.message import SessionMessage
        from g6.mcp.stdio import stdio_server

        source = open("CONIN$", "r", encoding="utf-8")
        assert type(source.buffer.raw).__name__ == "_WindowsConsoleIO"
        sys.stdin = source

        class Key(ctypes.Structure):
            _fields_ = [("down", wintypes.BOOL), ("repeat", wintypes.WORD),
                        ("virtual", wintypes.WORD), ("scan", wintypes.WORD),
                        ("char", wintypes.WCHAR), ("state", wintypes.DWORD)]

        class Event(ctypes.Union):
            _fields_ = [("key", Key), ("padding", ctypes.c_byte * 16)]

        class Record(ctypes.Structure):
            _fields_ = [("kind", wintypes.WORD), ("event", Event)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.WriteConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Record), wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
        kernel.WriteConsoleInputW.restype = wintypes.BOOL
        handle = msvcrt.get_osfhandle(source.fileno())
        payload = '{"jsonrpc":"2.0","id":73,"method":"p\u00e9\u20ac"}\r'
        if sys.argv[2] != "fresh":
            payload = "\u00e9\r" + payload
        records = (Record * len(payload))()
        for record, char in zip(records, payload):
            record.kind = 1
            record.event.key = Key(True, 1, 0, 0, char, 0)
        written = wintypes.DWORD()
        assert kernel.WriteConsoleInputW(handle, records, len(records), ctypes.byref(written))
        assert written.value == len(records)
        if sys.argv[2] == "raw_utf8_tail":
            assert source.buffer.raw.read(1) == b"\xc3"
        elif sys.argv[2] == "text_cache":
            assert source.read(1) == "\u00e9"
            assert source.read(1) == "\n"

        class Output:
            async def write(self, data):
                if sys.argv[1] == "broken_writer":
                    raise BrokenPipeError("console reply ended")
                return len(data)
            async def flush(self):
                pass

        async def run():
            with anyio.fail_after(5):
                async with stdio_server(stdout=Output()) as (incoming, outgoing):
                    if sys.argv[2] == "raw_utf8_tail":
                        error = await incoming.receive()
                        assert type(error).__name__ == "ValidationError", error
                    request = await incoming.receive()
                    assert isinstance(request, SessionMessage), request
                    assert request.message.root.id == 73
                    assert request.message.root.method == "p\u00e9\u20ac"
                    if sys.argv[1] == "broken_writer":
                        await outgoing.send(SessionMessage(types.JSONRPCMessage(
                            types.JSONRPCResponse(jsonrpc="2.0", id=73, result={}))))
                        await anyio.sleep_forever()
                    await outgoing.aclose()

        try:
            anyio.run(run)
            print("CONSOLE_ENDED", flush=True)
        finally:
            source.close()
    ''')
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env.update(G6_CLAUDE=r"C:\nonexistent\g6-no-host.exe", G6_CODEX=r"C:\nonexistent\g6-no-host.exe")
    completed = subprocess.run([sys.executable, *(["-X", "utf8"] if utf8_mode else []), "-c", child, finish, warm],
                               env=env, capture_output=True,
                               text=True, creationflags=subprocess.CREATE_NEW_CONSOLE,
                               startupinfo=startup, timeout=20)
    assert completed.returncode == 0, completed.stderr
    assert "CONSOLE_ENDED" in completed.stdout, completed.stderr
