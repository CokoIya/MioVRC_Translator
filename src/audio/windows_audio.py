from __future__ import annotations

import logging
import os
import sys
import uuid
from typing import Any, Iterable

logger = logging.getLogger(__name__)


if sys.platform == "win32":
    import ctypes
    from ctypes import POINTER, Structure, Union, byref, cast, c_void_p, c_ulong
    from ctypes import wintypes

    HRESULT = ctypes.c_long
    CLSCTX_ALL = 23
    COINIT_MULTITHREADED = 0x0
    DEVICE_STATE_ACTIVE = 0x00000001
    DEVICE_STATE_DISABLED = 0x00000002
    DEVICE_STATE_NOTPRESENT = 0x00000004
    DEVICE_STATE_UNPLUGGED = 0x00000008
    DEVICE_STATEMASK_ALL = 0x0000000F
    E_RENDER = 0
    E_CAPTURE = 1
    E_CONSOLE = 0
    E_MULTIMEDIA = 1
    E_COMMUNICATIONS = 2
    STGM_READ = 0
    VT_LPWSTR = 31
    AUDIO_SESSION_STATE_ACTIVE = 1
    RPC_E_CHANGED_MODE = 0x80010106
    TH32CS_SNAPPROCESS = 0x00000002
    _SYSTEM_AUDIO_SESSION_PROCESS_NAMES = {
        "audiodg.exe",
        "svchost.exe",
        "system",
    }

    _ole32 = ctypes.windll.ole32
    _kernel32 = ctypes.windll.kernel32

    class GUID(Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class PROPERTYKEY(Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT_UNION(Union):
        _fields_ = [("pwszVal", wintypes.LPWSTR)]

    class PROPVARIANT(Structure):
        _anonymous_ = ("value",)
        _fields_ = [
            ("vt", wintypes.USHORT),
            ("wReserved1", wintypes.USHORT),
            ("wReserved2", wintypes.USHORT),
            ("wReserved3", wintypes.USHORT),
            ("value", PROPVARIANT_UNION),
        ]

    class PROCESSENTRY32W(Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * wintypes.MAX_PATH),
        ]

    def _guid(value: str) -> GUID:
        parsed = uuid.UUID(value)
        data4 = (ctypes.c_ubyte * 8)(*parsed.bytes[8:])
        return GUID(parsed.time_low, parsed.time_mid, parsed.time_hi_version, data4)

    CLSID_MMDeviceEnumerator = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
    IID_IMMDeviceEnumerator = _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
    IID_IAudioSessionManager2 = _guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
    IID_IAudioSessionControl2 = _guid("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d")
    PKEY_DEVICE_FRIENDLY_NAME = PROPERTYKEY(
        _guid("A45C254E-DF1C-4EFD-8020-67D146A850E0"),
        14,
    )

    _ole32.CoInitializeEx.argtypes = [c_void_p, wintypes.DWORD]
    _ole32.CoInitializeEx.restype = HRESULT
    _ole32.CoCreateInstance.argtypes = [
        POINTER(GUID),
        c_void_p,
        wintypes.DWORD,
        POINTER(GUID),
        POINTER(c_void_p),
    ]
    _ole32.CoCreateInstance.restype = HRESULT
    _ole32.PropVariantClear.argtypes = [POINTER(PROPVARIANT)]
    _ole32.PropVariantClear.restype = HRESULT
    _ole32.CoTaskMemFree.argtypes = [c_void_p]
    _ole32.CoTaskMemFree.restype = None
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, POINTER(PROCESSENTRY32W)]
    _kernel32.Process32FirstW.restype = wintypes.BOOL
    _kernel32.Process32NextW.argtypes = [wintypes.HANDLE, POINTER(PROCESSENTRY32W)]
    _kernel32.Process32NextW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    QueryInterfaceProto = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        POINTER(GUID),
        POINTER(c_void_p),
    )
    ReleaseProto = ctypes.WINFUNCTYPE(c_ulong, c_void_p)
    EnumAudioEndpointsProto = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        POINTER(c_void_p),
    )
    GetDefaultAudioEndpointProto = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        POINTER(c_void_p),
    )
    CollectionGetCountProto = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        POINTER(wintypes.UINT),
    )
    CollectionItemProto = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        wintypes.UINT,
        POINTER(c_void_p),
    )
    DeviceActivateProto = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        POINTER(GUID),
        wintypes.DWORD,
        c_void_p,
        POINTER(c_void_p),
    )
    DeviceOpenPropertyStoreProto = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        wintypes.DWORD,
        POINTER(c_void_p),
    )
    DeviceGetIdProto = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        POINTER(wintypes.LPWSTR),
    )
    DeviceGetStateProto = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        POINTER(wintypes.DWORD),
    )
    PropertyStoreGetValueProto = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        POINTER(PROPERTYKEY),
        POINTER(PROPVARIANT),
    )
    GetSessionEnumeratorProto = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        POINTER(c_void_p),
    )
    SessionEnumGetCountProto = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        POINTER(wintypes.INT),
    )
    SessionEnumGetSessionProto = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        wintypes.INT,
        POINTER(c_void_p),
    )
    SessionGetStateProto = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        POINTER(wintypes.INT),
    )
    SessionGetProcessIdProto = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        POINTER(wintypes.DWORD),
    )

    def _succeeded(hr: int) -> bool:
        return int(hr) >= 0

    def _co_initialize() -> bool:
        hr = _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        code = ctypes.c_ulong(hr).value
        if code == RPC_E_CHANGED_MODE:
            return False
        if not _succeeded(hr):
            raise OSError(f"CoInitializeEx failed: 0x{code:08X}")
        return True

    def _vtable_method(pointer: c_void_p, index: int, prototype):
        # COM 接口没有头文件，直接从 vtable 指针按偏移取方法
        vtable = cast(pointer, POINTER(POINTER(c_void_p))).contents
        return prototype(vtable[index])

    def _release(pointer: c_void_p | None) -> None:
        if not pointer:
            return
        try:
            _vtable_method(pointer, 2, ReleaseProto)(pointer)
        except Exception:
            pass

    def _query_interface(pointer: c_void_p, iid: GUID) -> c_void_p | None:
        result = c_void_p()
        hr = _vtable_method(pointer, 0, QueryInterfaceProto)(
            pointer,
            byref(iid),
            byref(result),
        )
        if not _succeeded(hr):
            return None
        return result

    def _create_device_enumerator() -> c_void_p | None:
        enumerator = c_void_p()
        hr = _ole32.CoCreateInstance(
            byref(CLSID_MMDeviceEnumerator),
            None,
            CLSCTX_ALL,
            byref(IID_IMMDeviceEnumerator),
            byref(enumerator),
        )
        if not _succeeded(hr):
            return None
        return enumerator

    def _get_device_name(device: c_void_p) -> str | None:
        store = c_void_p()
        hr = _vtable_method(device, 4, DeviceOpenPropertyStoreProto)(
            device,
            STGM_READ,
            byref(store),
        )
        if not _succeeded(hr):
            return None

        value = PROPVARIANT()
        try:
            hr = _vtable_method(store, 5, PropertyStoreGetValueProto)(
                store,
                byref(PKEY_DEVICE_FRIENDLY_NAME),
                byref(value),
            )
            if not _succeeded(hr) or int(value.vt) != VT_LPWSTR or not value.pwszVal:
                return None
            name = str(value.pwszVal).strip()
            return name or None
        finally:
            try:
                _ole32.PropVariantClear(byref(value))
            except Exception:
                pass
            _release(store)

    def _get_device_id(device: c_void_p) -> str | None:
        value = wintypes.LPWSTR()
        hr = _vtable_method(device, 5, DeviceGetIdProto)(
            device,
            byref(value),
        )
        if not _succeeded(hr) or not value:
            return None
        try:
            device_id = str(value.value or "").strip()
            return device_id or None
        finally:
            try:
                _ole32.CoTaskMemFree(cast(value, c_void_p))
            except Exception:
                pass

    def _get_device_state(device: c_void_p) -> int | None:
        state = wintypes.DWORD()
        hr = _vtable_method(device, 6, DeviceGetStateProto)(
            device,
            byref(state),
        )
        if not _succeeded(hr):
            return None
        return int(state.value)

    def _device_state_name(state: int | None) -> str:
        if state == DEVICE_STATE_ACTIVE:
            return "active"
        if state == DEVICE_STATE_DISABLED:
            return "disabled"
        if state == DEVICE_STATE_NOTPRESENT:
            return "not_present"
        if state == DEVICE_STATE_UNPLUGGED:
            return "unplugged"
        if state is None:
            return "unknown"
        labels: list[str] = []
        if state & DEVICE_STATE_ACTIVE:
            labels.append("active")
        if state & DEVICE_STATE_DISABLED:
            labels.append("disabled")
        if state & DEVICE_STATE_NOTPRESENT:
            labels.append("not_present")
        if state & DEVICE_STATE_UNPLUGGED:
            labels.append("unplugged")
        return "|".join(labels) or f"unknown_0x{state:08x}"

    def _default_endpoint_ids(enumerator: c_void_p, flow: int) -> dict[str, str]:
        defaults: dict[str, str] = {}
        for role, role_name in (
            (E_MULTIMEDIA, "multimedia"),
            (E_CONSOLE, "console"),
            (E_COMMUNICATIONS, "communications"),
        ):
            device = c_void_p()
            try:
                hr = _vtable_method(
                    enumerator,
                    4,
                    GetDefaultAudioEndpointProto,
                )(
                    enumerator,
                    flow,
                    role,
                    byref(device),
                )
                if not _succeeded(hr) or not device:
                    continue
                device_id = _get_device_id(device)
                if device_id:
                    defaults[role_name] = device_id
            finally:
                _release(device)
        return defaults

    def _active_audio_session_process_ids(
        device: c_void_p,
    ) -> tuple[bool, list[int]]:
        """Return active-session state for one CoreAudio endpoint.

        Capture endpoints expose audio sessions just like render endpoints.  The
        process IDs let callers distinguish a microphone already used by another
        application from the capture stream opened by Mio itself.
        """

        manager = None
        session_enum = None
        has_active_session = False
        process_ids: set[int] = set()
        try:
            manager = c_void_p()
            hr = _vtable_method(device, 3, DeviceActivateProto)(
                device,
                byref(IID_IAudioSessionManager2),
                CLSCTX_ALL,
                None,
                byref(manager),
            )
            if not _succeeded(hr) or not manager:
                return False, []

            session_enum = c_void_p()
            hr = _vtable_method(manager, 5, GetSessionEnumeratorProto)(
                manager,
                byref(session_enum),
            )
            if not _succeeded(hr) or not session_enum:
                return False, []

            session_count = wintypes.INT()
            hr = _vtable_method(session_enum, 3, SessionEnumGetCountProto)(
                session_enum,
                byref(session_count),
            )
            if not _succeeded(hr):
                return False, []

            for session_index in range(int(session_count.value)):
                session = c_void_p()
                session2 = None
                try:
                    hr = _vtable_method(session_enum, 4, SessionEnumGetSessionProto)(
                        session_enum,
                        session_index,
                        byref(session),
                    )
                    if not _succeeded(hr) or not session:
                        continue

                    state = wintypes.INT()
                    hr = _vtable_method(session, 3, SessionGetStateProto)(
                        session,
                        byref(state),
                    )
                    if not _succeeded(hr) or int(state.value) != AUDIO_SESSION_STATE_ACTIVE:
                        continue
                    has_active_session = True

                    session2 = _query_interface(session, IID_IAudioSessionControl2)
                    if session2 is None:
                        continue
                    process_id = wintypes.DWORD()
                    hr = _vtable_method(session2, 14, SessionGetProcessIdProto)(
                        session2,
                        byref(process_id),
                    )
                    if _succeeded(hr) and int(process_id.value) > 0:
                        process_ids.add(int(process_id.value))
                finally:
                    _release(session2)
                    _release(session)
            return has_active_session, sorted(process_ids)
        except Exception:
            logger.debug("CoreAudio session inspection failed", exc_info=True)
            return has_active_session, sorted(process_ids)
        finally:
            _release(session_enum)
            _release(manager)

    def _list_audio_endpoints_for_flow(
        enumerator: c_void_p,
        *,
        flow: int,
        include_inactive: bool,
        ignored_session_process_ids: set[int],
    ) -> list[dict[str, Any]]:
        collection = c_void_p()
        state_mask = DEVICE_STATEMASK_ALL if include_inactive else DEVICE_STATE_ACTIVE
        hr = _vtable_method(enumerator, 3, EnumAudioEndpointsProto)(
            enumerator,
            flow,
            state_mask,
            byref(collection),
        )
        if not _succeeded(hr) or not collection:
            return []

        defaults = _default_endpoint_ids(enumerator, flow)
        default_ids = set(defaults.values())
        result: list[dict[str, Any]] = []
        try:
            count = wintypes.UINT()
            hr = _vtable_method(collection, 3, CollectionGetCountProto)(
                collection,
                byref(count),
            )
            if not _succeeded(hr):
                return []

            for index in range(int(count.value)):
                device = c_void_p()
                try:
                    hr = _vtable_method(collection, 4, CollectionItemProto)(
                        collection,
                        index,
                        byref(device),
                    )
                    if not _succeeded(hr) or not device:
                        continue
                    device_id = _get_device_id(device)
                    name = _get_device_name(device)
                    state = _get_device_state(device)
                    if not device_id and not name:
                        continue
                    has_active_session = False
                    active_session_process_ids: list[int] = []
                    if flow == E_CAPTURE and state == DEVICE_STATE_ACTIVE:
                        (
                            has_active_session,
                            active_session_process_ids,
                        ) = _active_audio_session_process_ids(device)
                    has_external_active_session = bool(has_active_session) and (
                        any(
                            process_id not in ignored_session_process_ids
                            for process_id in active_session_process_ids
                        )
                    )
                    result.append(
                        {
                            "id": device_id,
                            "name": name,
                            "flow": "render" if flow == E_RENDER else "capture",
                            "state": state,
                            "state_name": _device_state_name(state),
                            "active": state == DEVICE_STATE_ACTIVE,
                            "is_default": bool(device_id and device_id in default_ids),
                            "default_roles": [
                                role_name
                                for role_name, default_id in defaults.items()
                                if device_id and default_id == device_id
                            ],
                            "has_active_session": has_active_session,
                            "has_external_active_session": has_external_active_session,
                            "active_session_process_ids": active_session_process_ids,
                        }
                    )
                finally:
                    _release(device)
            return result
        finally:
            _release(collection)

    def list_audio_endpoints(*, include_inactive: bool = True) -> list[dict[str, Any]]:
        """Return Windows CoreAudio endpoints with state and default-role data."""

        should_uninitialize = False
        enumerator = None
        try:
            should_uninitialize = _co_initialize()
            enumerator = _create_device_enumerator()
            if enumerator is None:
                return []
            process_names_by_id = _process_image_names()
            ignored_session_process_ids = {os.getpid()}
            ignored_session_process_ids.update(
                process_id
                for process_id, image_name in process_names_by_id.items()
                if image_name in _SYSTEM_AUDIO_SESSION_PROCESS_NAMES
            )
            endpoints = _list_audio_endpoints_for_flow(
                enumerator,
                flow=E_RENDER,
                include_inactive=include_inactive,
                ignored_session_process_ids=ignored_session_process_ids,
            )
            endpoints.extend(
                _list_audio_endpoints_for_flow(
                    enumerator,
                    flow=E_CAPTURE,
                    include_inactive=include_inactive,
                    ignored_session_process_ids=ignored_session_process_ids,
                )
            )
            return endpoints
        except Exception:
            logger.debug("Windows CoreAudio endpoint enumeration failed", exc_info=True)
            return []
        finally:
            _release(enumerator)
            if should_uninitialize:
                try:
                    _ole32.CoUninitialize()
                except Exception:
                    pass

    def _process_image_names() -> dict[int, str]:
        result: dict[int, str] = {}
        snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if snapshot == invalid_handle:
            return result

        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not _kernel32.Process32FirstW(snapshot, byref(entry)):
                return result

            while True:
                image_name = str(entry.szExeFile).strip().lower()
                if image_name:
                    result[int(entry.th32ProcessID)] = image_name
                if not _kernel32.Process32NextW(snapshot, byref(entry)):
                    break
        finally:
            _kernel32.CloseHandle(snapshot)
        return result

    def _list_process_ids(process_names: Iterable[str]) -> set[int]:
        targets = {str(name).strip().lower() for name in process_names if str(name).strip()}
        if not targets:
            return set()
        return {
            process_id
            for process_id, image_name in _process_image_names().items()
            if image_name in targets
        }

    def _device_matches_for_process_ids(process_ids: set[int]) -> list[tuple[str, bool]]:
        if not process_ids:
            return []

        should_uninitialize = False
        enumerator = None
        collection = None
        matches: list[tuple[str, bool]] = []
        try:
            should_uninitialize = _co_initialize()
            enumerator = _create_device_enumerator()
            if enumerator is None:
                return []

            collection = c_void_p()
            hr = _vtable_method(enumerator, 3, EnumAudioEndpointsProto)(
                enumerator,
                E_RENDER,
                DEVICE_STATE_ACTIVE,
                byref(collection),
            )
            if not _succeeded(hr):
                return []

            count = wintypes.UINT()
            hr = _vtable_method(collection, 3, CollectionGetCountProto)(
                collection,
                byref(count),
            )
            if not _succeeded(hr):
                return []

            for index in range(int(count.value)):
                device = c_void_p()
                hr = _vtable_method(collection, 4, CollectionItemProto)(
                    collection,
                    index,
                    byref(device),
                )
                if not _succeeded(hr) or not device:
                    continue

                manager = None
                session_enum = None
                try:
                    device_name = _get_device_name(device)
                    if not device_name:
                        continue

                    manager = c_void_p()
                    hr = _vtable_method(device, 3, DeviceActivateProto)(
                        device,
                        byref(IID_IAudioSessionManager2),
                        CLSCTX_ALL,
                        None,
                        byref(manager),
                    )
                    if not _succeeded(hr) or not manager:
                        continue

                    session_enum = c_void_p()
                    hr = _vtable_method(manager, 5, GetSessionEnumeratorProto)(
                        manager,
                        byref(session_enum),
                    )
                    if not _succeeded(hr) or not session_enum:
                        continue

                    session_count = wintypes.INT()
                    hr = _vtable_method(session_enum, 3, SessionEnumGetCountProto)(
                        session_enum,
                        byref(session_count),
                    )
                    if not _succeeded(hr):
                        continue

                    for session_index in range(int(session_count.value)):
                        session = c_void_p()
                        hr = _vtable_method(session_enum, 4, SessionEnumGetSessionProto)(
                            session_enum,
                            session_index,
                            byref(session),
                        )
                        if not _succeeded(hr) or not session:
                            continue

                        session2 = None
                        try:
                            session2 = _query_interface(session, IID_IAudioSessionControl2)
                            if session2 is None:
                                continue

                            process_id = wintypes.DWORD()
                            hr = _vtable_method(session2, 14, SessionGetProcessIdProto)(
                                session2,
                                byref(process_id),
                            )
                            if not _succeeded(hr) or int(process_id.value) not in process_ids:
                                continue

                            state = wintypes.INT()
                            hr = _vtable_method(session, 3, SessionGetStateProto)(
                                session,
                                byref(state),
                            )
                            is_active = _succeeded(hr) and int(state.value) == AUDIO_SESSION_STATE_ACTIVE
                            matches.append((device_name, is_active))
                        finally:
                            _release(session2)
                            _release(session)
                finally:
                    _release(session_enum)
                    _release(manager)
                    _release(device)

            return matches
        except Exception:
            return matches
        finally:
            _release(collection)
            _release(enumerator)
            if should_uninitialize:
                try:
                    _ole32.CoUninitialize()
                except Exception:
                    pass

    def detect_process_output_device_name(process_names: Iterable[str]) -> str | None:
        process_ids = _list_process_ids(process_names)
        matches = _device_matches_for_process_ids(process_ids)
        if not matches:
            return None

        seen: set[str] = set()
        for preferred_state in (True, False):
            for name, is_active in matches:
                clean = str(name or "").strip()
                if not clean or clean in seen or is_active != preferred_state:
                    continue
                seen.add(clean)
                return clean
        return None

    def inspect_process_output_state(
        process_names: Iterable[str],
        *,
        default_output_device: str | None = None,
    ) -> dict[str, object]:
        names = [str(name).strip() for name in process_names if str(name).strip()]
        process_ids = _list_process_ids(names)
        matches = _device_matches_for_process_ids(process_ids)
        coreaudio_endpoints = list_audio_endpoints(include_inactive=True)
        active_device = next(
            (str(name).strip() for name, is_active in matches if is_active and str(name).strip()),
            None,
        )
        if active_device is None:
            active_device = next(
                (str(name).strip() for name, _ in matches if str(name).strip()),
                None,
            )
        snapshot = {
            "process_names": names,
            "process_ids": sorted(process_ids),
            "is_running": bool(process_ids),
            "default_output_device": default_output_device,
            "active_device": active_device,
            "has_active_audio_session": any(bool(is_active) for _, is_active in matches),
            "matches": [
                {
                    "device_name": str(name).strip(),
                    "is_active": bool(is_active),
                }
                for name, is_active in matches
                if str(name).strip()
            ],
            "coreaudio": {
                "endpoint_count": len(coreaudio_endpoints),
                "active_count": sum(
                    1 for endpoint in coreaudio_endpoints if endpoint.get("active")
                ),
                "inactive_count": sum(
                    1 for endpoint in coreaudio_endpoints if not endpoint.get("active")
                ),
                "defaults": [
                    {
                        "name": endpoint.get("name"),
                        "flow": endpoint.get("flow"),
                        "roles": endpoint.get("default_roles", []),
                    }
                    for endpoint in coreaudio_endpoints
                    if endpoint.get("is_default")
                ],
            },
        }
        logger.debug("Process output inspection: %s", snapshot)
        return snapshot

    def is_process_running(process_names: Iterable[str]) -> bool:
        return bool(_list_process_ids(process_names))


else:

    def list_audio_endpoints(*, include_inactive: bool = True) -> list[dict[str, Any]]:
        del include_inactive
        return []

    def detect_process_output_device_name(process_names: Iterable[str]) -> str | None:
        return None

    def inspect_process_output_state(
        process_names: Iterable[str],
        *,
        default_output_device: str | None = None,
    ) -> dict[str, object]:
        names = [str(name).strip() for name in process_names if str(name).strip()]
        return {
            "process_names": names,
            "process_ids": [],
            "is_running": False,
            "default_output_device": default_output_device,
            "active_device": None,
            "has_active_audio_session": False,
            "matches": [],
            "coreaudio": {
                "endpoint_count": 0,
                "active_count": 0,
                "inactive_count": 0,
                "defaults": [],
            },
        }

    def is_process_running(process_names: Iterable[str]) -> bool:
        return False
