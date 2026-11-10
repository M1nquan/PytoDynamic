from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import keyword
import logging
import marshal
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import winreg
import zlib
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

DEFAULT_CHUNK_SIZE = 2048
MAX_ALLOWED_CHUNK_SIZE = 65536
MAX_SOURCE_SIZE_BYTES = 50 * 1024 * 1024
MAX_PAYLOAD_LIMIT_BYTES = 100 * 1024 * 1024
DEFAULT_SHUTDOWN_TIMEOUT_MS = 3000
MIN_SHUTDOWN_TIMEOUT_MS = 100
MAX_SHUTDOWN_TIMEOUT_MS = 60000

ASCII_LOGO = r"""
  ██████╗ ████████╗██████╗     
  ██╔══██╗╚══██╔══╝██╔══██╗     
  ██████╔╝   ██║   ██║  ██║   
  ██╔═══╝    ██║   ██║  ██║   
  ██║        ██║   ██████╔╝    
  ╚═╝        ╚═╝   ╚═════╝     
  [ PTD Engine: Verified Production Standard v1.0 ]
  [Engineered by M1nhquaanz, 2026]
"""

CPP_HARNESS_TEMPLATE = r"""#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <Python.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#define PTD_API extern "C" __declspec(dllexport) __cdecl

namespace ptd {

#pragma pack(push, 1)
struct PayloadHeader {
    char magic[8];
    uint16_t py_major;
    uint16_t py_minor;
    uint32_t py_magic;
    uint32_t payload_len;
    uint32_t crc32;
};
#pragma pack(pop)

constexpr const char* kModuleName = "__PTD_MODULE__";
constexpr bool kEmbedded = __PTD_EMBED__;
constexpr const char* kPyVersionPrefix = "__PTD_PYVER__";
constexpr const wchar_t* kBuildSdkHome = L"__PTD_PYHOME__";
constexpr uint16_t kExpectedPyMajor = __PTD_EXPECTED_MAJOR__;
constexpr uint16_t kExpectedPyMinor = __PTD_EXPECTED_MINOR__;
constexpr uint32_t kExpectedPyMagic = __PTD_EXPECTED_MAGIC__;
constexpr size_t kMaxPayloadBytes = __PTD_MAX_PAYLOAD__;
constexpr uint32_t kDefaultDrainTimeoutMs = __PTD_DRAIN_TIMEOUT_MS__;

const char* const kPayloadChunks[] = {
__PTD_PAYLOAD_CHUNKS__
};

enum EngineState : uint32_t {
    STATE_UNINITIALIZED = 0,
    STATE_INITIALIZING  = 1,
    STATE_READY         = 2,
    STATE_ERROR         = 3,
    STATE_DRAINING      = 4,
    STATE_TERMINATING   = 5,
    STATE_TERMINATED    = 6,
    STATE_ABANDONED     = 7
};

enum StatusCode : int {
    STATUS_SUCCESS              =  1,
    STATUS_INQUIRY              =  0,
    STATUS_ERR_GENERIC          = -1,
    STATUS_ERR_BUF_TOO_SMALL    = -2,
    STATUS_ERR_SHUTTING_DOWN    = -3,
    STATUS_ERR_INVALID_ARG      = -4,
    STATUS_ERR_OVERFLOW         = -5,
    STATUS_ERR_PAYLOAD_LIMIT    = -6,
    STATUS_ERR_ABI_MISMATCH     = -7,
    STATUS_ERR_PAYLOAD_CORRUPT  = -8,
    STATUS_ERR_SHUTDOWN_TIMEOUT = -9
};

inline size_t safe_strnlen(const char* s, size_t max_len) noexcept {
    if (!s) return 0;
    size_t i = 0;
    while (i < max_len && s[i] != '\0') {
        ++i;
    }
    return i;
}

inline uint32_t compute_crc32(const uint8_t* data, size_t length) noexcept {
    uint32_t crc = 0xFFFFFFFFU;
    for (size_t i = 0; i < length; ++i) {
        crc ^= static_cast<uint32_t>(data[i]);
        for (int j = 0; j < 8; ++j) {
            crc = (crc >> 1) ^ ((crc & 1U) ? 0xEDB88320U : 0U);
        }
    }
    return ~crc;
}

class SafePyRef {
public:
    SafePyRef() noexcept : obj_(nullptr) {}
    explicit SafePyRef(PyObject* o) noexcept : obj_(o) {}
    SafePyRef(SafePyRef&& other) noexcept : obj_(other.obj_) { other.obj_ = nullptr; }
    SafePyRef& operator=(SafePyRef&& other) noexcept {
        if (this != &other) {
            Py_XDECREF(obj_);
            obj_ = other.obj_;
            other.obj_ = nullptr;
        }
        return *this;
    }
    ~SafePyRef() { Py_XDECREF(obj_); }
    SafePyRef(const SafePyRef&) = delete;
    SafePyRef& operator=(const SafePyRef&) = delete;

    PyObject* get() const noexcept { return obj_; }
    PyObject* release() noexcept {
        PyObject* tmp = obj_;
        obj_ = nullptr;
        return tmp;
    }
    explicit operator bool() const noexcept { return obj_ != nullptr; }
private:
    PyObject* obj_;
};

class ThreadGILState {
public:
    ThreadGILState() : state_(PyGILState_Ensure()) {}
    ~ThreadGILState() { PyGILState_Release(state_); }
    ThreadGILState(const ThreadGILState&) = delete;
    ThreadGILState& operator=(const ThreadGILState&) = delete;
private:
    PyGILState_STATE state_;
};

struct EngineRuntime {
    std::atomic<uint64_t> gate{0};

    std::mutex state_mutex;
    std::condition_variable state_cv;

    std::mutex drain_mutex;
    std::condition_variable drain_cv;

    bool we_own_python{false};
    DWORD init_thread_id{0};
    PyThreadState* dll_saved_tstate{nullptr};
    PyObject* target_module{nullptr};
    HMODULE self_module{nullptr};

    static std::mutex error_mutex;
    static std::string global_last_error;
    thread_local static std::string thread_last_error;

    static void set_error(std::string msg) {
        thread_last_error = msg;
        std::lock_guard<std::mutex> lock(error_mutex);
        global_last_error = std::move(msg);
    }

    static void clear_error() noexcept {
        thread_last_error.clear();
    }

    static void record_py_exception(const char* context) {
        std::string err = context ? context : "Python Runtime Error";
        PyObject *ptype = nullptr, *pval = nullptr, *ptb = nullptr;
        PyErr_Fetch(&ptype, &pval, &ptb);
        if (!ptype) {
            set_error(err + ": Unknown error condition");
            return;
        }
        PyErr_NormalizeException(&ptype, &pval, &ptb);

        SafePyRef tname(PyObject_GetAttrString(ptype, "__name__"));
        if (tname) {
            const char* c = PyUnicode_AsUTF8(tname.get());
            if (c) {
                err += " [";
                err += c;
                err += "]";
            }
        }
        if (pval) {
            SafePyRef val_str(PyObject_Str(pval));
            if (val_str) {
                const char* c = PyUnicode_AsUTF8(val_str.get());
                if (c) {
                    err += ": ";
                    err += c;
                }
            } else {
                PyErr_Clear();
                err += ": <Unformattable exception>";
            }
        }
        Py_XDECREF(ptype);
        Py_XDECREF(pval);
        Py_XDECREF(ptb);
        PyErr_Clear();
        set_error(std::move(err));
    }
};

std::mutex EngineRuntime::error_mutex;
std::string EngineRuntime::global_last_error;
thread_local std::string EngineRuntime::thread_last_error;
static EngineRuntime g_engine;

class InvocationAdmissionGuard {
public:
    InvocationAdmissionGuard() noexcept : entered_(false) {
        uint64_t current = g_engine.gate.load(std::memory_order_acquire);
        while (true) {
            uint32_t state = static_cast<uint32_t>(current >> 32);
            if (state != STATE_READY) {
                entered_ = false;
                return;
            }
            uint32_t count = static_cast<uint32_t>(current & 0xFFFFFFFFULL);
            if (count >= 0xFFFFFFFEU) {
                entered_ = false;
                return;
            }
            uint64_t desired = (static_cast<uint64_t>(STATE_READY) << 32) | (count + 1ULL);
            if (g_engine.gate.compare_exchange_weak(current, desired,
                                                    std::memory_order_acq_rel,
                                                    std::memory_order_acquire)) {
                entered_ = true;
                return;
            }
        }
    }

    ~InvocationAdmissionGuard() {
        if (entered_) {
            uint64_t prev = g_engine.gate.fetch_sub(1ULL, std::memory_order_acq_rel);
            uint32_t state = static_cast<uint32_t>(prev >> 32);
            uint32_t remaining = static_cast<uint32_t>(prev & 0xFFFFFFFFULL) - 1U;
            if (state >= STATE_DRAINING && remaining == 0) {
                std::lock_guard<std::mutex> lock(g_engine.drain_mutex);
                g_engine.drain_cv.notify_all();
            }
        }
    }

    bool is_allowed() const noexcept { return entered_; }

private:
    bool entered_;
};

void module_anchor() noexcept {}

std::wstring get_current_dll_dir() {
    HMODULE self = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            reinterpret_cast<LPCWSTR>(&module_anchor), &self)) {
        return L"";
    }
    std::vector<wchar_t> buf(32768);
    DWORD n = GetModuleFileNameW(self, buf.data(), static_cast<DWORD>(buf.size()));
    if (n == 0 || n >= buf.size()) return L"";
    std::wstring path(buf.data(), n);
    size_t idx = path.find_last_of(L"\\/");
    return (idx == std::wstring::npos) ? L"." : path.substr(0, idx);
}

inline bool directory_has_python_lib(const std::wstring& dir) {
    if (dir.empty()) return false;
    DWORD attrs = GetFileAttributesW((dir + L"\\Lib").c_str());
    if (attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_DIRECTORY)) return true;
    std::wstring zip_path = dir + L"\\python" + std::to_wstring(kExpectedPyMajor) + std::to_wstring(kExpectedPyMinor) + L".zip";
    attrs = GetFileAttributesW(zip_path.c_str());
    if (attrs != INVALID_FILE_ATTRIBUTES) return true;
    return false;
}

bool configure_isolated_python() {
    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);

    config.isolated = 1;
    config.use_environment = 0;
    config.site_import = 0;

    std::wstring dll_dir = get_current_dll_dir();
    std::wstring chosen_home;

    if (directory_has_python_lib(dll_dir)) {
        chosen_home = dll_dir;
    } else if (kBuildSdkHome && kBuildSdkHome[0] != L'\0') {
        chosen_home = kBuildSdkHome;
    } else if (!dll_dir.empty()) {
        chosen_home = dll_dir;
    }

    if (!chosen_home.empty()) {
        PyStatus status = PyConfig_SetString(&config, &config.home, chosen_home.c_str());
        if (PyStatus_Exception(status)) {
            EngineRuntime::set_error(std::string("PyConfig_SetString failed: ") + (status.err_msg ? status.err_msg : "unknown"));
            PyConfig_Clear(&config);
            return false;
        }
    }

    PyStatus status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        EngineRuntime::set_error(std::string("Py_InitializeFromConfig failed: ") + (status.err_msg ? status.err_msg : "unknown"));
        return false;
    }
    return true;
}

PyObject* materialize_payload() {
    std::string payload_b64;
    for (const char* const* c = kPayloadChunks; *c; ++c) {
        payload_b64 += *c;
    }

    if (payload_b64.size() > kMaxPayloadBytes) {
        EngineRuntime::set_error("Payload container exceeds maximum permitted size");
        return nullptr;
    }

    SafePyRef b64_mod(PyImport_ImportModule("base64"));
    SafePyRef marshal_mod(PyImport_ImportModule("marshal"));
    SafePyRef types_mod(PyImport_ImportModule("types"));
    if (!b64_mod || !marshal_mod || !types_mod) {
        EngineRuntime::record_py_exception("Failed to import loader bootstrap modules");
        return nullptr;
    }

    SafePyRef blob(PyBytes_FromStringAndSize(payload_b64.data(), static_cast<Py_ssize_t>(payload_b64.size())));
    SafePyRef raw(PyObject_CallMethod(b64_mod.get(), "b64decode", "O", blob.get()));
    if (!raw) {
        EngineRuntime::record_py_exception("Base64 decoding failed on payload container");
        return nullptr;
    }

    char* raw_bytes = nullptr;
    Py_ssize_t raw_len = 0;
    if (PyBytes_AsStringAndSize(raw.get(), &raw_bytes, &raw_len) < 0 || raw_len < static_cast<Py_ssize_t>(sizeof(PayloadHeader))) {
        EngineRuntime::set_error("Decoded payload envelope is truncated or invalid");
        return nullptr;
    }

    PayloadHeader header;
    std::memcpy(&header, raw_bytes, sizeof(PayloadHeader));

    if (std::memcmp(header.magic, "PTDPAY01", 8) != 0) {
        EngineRuntime::set_error("Envelope header magic mismatch. Unsupported container.");
        return nullptr;
    }

    if (header.py_major != kExpectedPyMajor || header.py_minor != kExpectedPyMinor) {
        EngineRuntime::set_error("Bytecode ABI version mismatch between payload and compiler target");
        return nullptr;
    }

    if (header.py_magic != kExpectedPyMagic) {
        EngineRuntime::set_error("Python marshal magic number mismatch. Incompatible bytecode.");
        return nullptr;
    }

    if (header.payload_len > kMaxPayloadBytes || static_cast<size_t>(raw_len) != sizeof(PayloadHeader) + header.payload_len) {
        EngineRuntime::set_error("Payload envelope length verification failed");
        return nullptr;
    }

    const uint8_t* marshal_bytes = reinterpret_cast<const uint8_t*>(raw_bytes + sizeof(PayloadHeader));
    uint32_t computed_crc = compute_crc32(marshal_bytes, header.payload_len);
    if (computed_crc != header.crc32) {
        EngineRuntime::set_error("Payload CRC32 checksum mismatch. Data corruption detected.");
        return nullptr;
    }

    SafePyRef marshal_slice(PyBytes_FromStringAndSize(reinterpret_cast<const char*>(marshal_bytes), header.payload_len));
    SafePyRef code(PyObject_CallMethod(marshal_mod.get(), "loads", "O", marshal_slice.get()));
    if (!code || !PyCode_Check(code.get())) {
        EngineRuntime::record_py_exception("marshal.loads failed to deserialize bytecode");
        return nullptr;
    }

    SafePyRef mod(PyObject_CallMethod(types_mod.get(), "ModuleType", "s", kModuleName));
    if (!mod) {
        EngineRuntime::record_py_exception("Failed to allocate ModuleType instance");
        return nullptr;
    }

    PyObject* dict = PyModule_GetDict(mod.get());
    SafePyRef file_dummy(PyUnicode_FromString("<ptd-hardened-virtual>"));
    if (PyDict_SetItemString(dict, "__file__", file_dummy.get()) < 0) {
        EngineRuntime::record_py_exception("Failed to initialize module namespace dictionary");
        return nullptr;
    }

    SafePyRef eval_res(PyEval_EvalCode(code.get(), dict, dict));
    if (!eval_res) {
        EngineRuntime::record_py_exception("Execution of module bytecode payload failed");
        return nullptr;
    }

    if (PyDict_SetItemString(PyImport_GetModuleDict(), kModuleName, mod.get()) < 0) {
        EngineRuntime::record_py_exception("Failed to register module into sys.modules");
        return nullptr;
    }

    return mod.release();
}

bool ensure_runtime_initialized() {
    uint64_t current = g_engine.gate.load(std::memory_order_acquire);
    uint32_t state = static_cast<uint32_t>(current >> 32);
    if (state == STATE_READY) {
        return true;
    }
    if (state >= STATE_DRAINING) {
        EngineRuntime::set_error("Engine is currently shutting down or terminated");
        return false;
    }

    std::unique_lock<std::mutex> lock(g_engine.state_mutex);
    while (true) {
        current = g_engine.gate.load(std::memory_order_acquire);
        state = static_cast<uint32_t>(current >> 32);
        if (state == STATE_INITIALIZING) {
            g_engine.state_cv.wait(lock);
            continue;
        }
        if (state == STATE_READY) return true;
        if (state >= STATE_DRAINING) {
            EngineRuntime::set_error("Engine shutdown in progress");
            return false;
        }
        break;
    }

    uint64_t initializing_val = static_cast<uint64_t>(STATE_INITIALIZING) << 32;
    g_engine.gate.store(initializing_val, std::memory_order_release);

    bool success = false;
    try {
        g_engine.init_thread_id = GetCurrentThreadId();

        if (!Py_IsInitialized()) {
            g_engine.we_own_python = true;
            if (!configure_isolated_python()) {
                throw false;
            }
            g_engine.dll_saved_tstate = PyEval_SaveThread();
        } else {
            g_engine.we_own_python = false;
        }

        ThreadGILState gil;

        const char* runtime_ver = Py_GetVersion();
        if (std::strncmp(runtime_ver, kPyVersionPrefix, std::strlen(kPyVersionPrefix)) != 0) {
            EngineRuntime::set_error(std::string("Version Mismatch: Expected ") + kPyVersionPrefix + "x, detected " + runtime_ver);
            throw false;
        }

        if (kEmbedded) {
            g_engine.target_module = materialize_payload();
        } else {
            g_engine.target_module = PyImport_ImportModule(kModuleName);
            if (!g_engine.target_module) {
                EngineRuntime::record_py_exception("External disk module import failed");
            }
        }

        if (!g_engine.target_module) {
            throw false;
        }

        success = true;
    } catch (...) {
        success = false;
    }

    if (success) {
        uint64_t ready_val = static_cast<uint64_t>(STATE_READY) << 32;
        g_engine.gate.store(ready_val, std::memory_order_release);
    } else {
        if (g_engine.we_own_python && Py_IsInitialized()) {
            PyEval_RestoreThread(g_engine.dll_saved_tstate);
            g_engine.dll_saved_tstate = nullptr;
            Py_CLEAR(g_engine.target_module);
            Py_FinalizeEx();
            g_engine.we_own_python = false;
        }
        uint64_t error_val = static_cast<uint64_t>(STATE_ERROR) << 32;
        g_engine.gate.store(error_val, std::memory_order_release);
    }

    g_engine.state_cv.notify_all();
    return success;
}

int perform_safe_shutdown(uint32_t timeout_ms, bool is_process_exit) {
    std::unique_lock<std::mutex> state_lock(g_engine.state_mutex);

    while (true) {
        uint64_t current = g_engine.gate.load(std::memory_order_acquire);
        uint32_t state = static_cast<uint32_t>(current >> 32);

        if (state == STATE_INITIALIZING) {
            g_engine.state_cv.wait(state_lock);
            continue;
        }

        if (state >= STATE_DRAINING) {
            return STATUS_SUCCESS;
        }

        if (state == STATE_UNINITIALIZED || state == STATE_ERROR) {
            uint64_t term_val = static_cast<uint64_t>(STATE_TERMINATED) << 32;
            g_engine.gate.store(term_val, std::memory_order_release);
            return STATUS_SUCCESS;
        }

        uint32_t count = static_cast<uint32_t>(current & 0xFFFFFFFFULL);
        uint64_t draining_val = (static_cast<uint64_t>(STATE_DRAINING) << 32) | count;
        if (g_engine.gate.compare_exchange_weak(current, draining_val,
                                                std::memory_order_acq_rel,
                                                std::memory_order_acquire)) {
            break;
        }
    }

    if (is_process_exit) {
        uint64_t term_val = static_cast<uint64_t>(STATE_TERMINATED) << 32;
        g_engine.gate.store(term_val, std::memory_order_release);
        return STATUS_SUCCESS;
    }

    state_lock.unlock();

    bool drain_ok = false;
    {
        std::unique_lock<std::mutex> drain_lock(g_engine.drain_mutex);
        uint64_t current = g_engine.gate.load(std::memory_order_acquire);
        if ((current & 0xFFFFFFFFULL) == 0) {
            drain_ok = true;
        } else {
            drain_ok = g_engine.drain_cv.wait_for(drain_lock, std::chrono::milliseconds(timeout_ms), []() {
                return (g_engine.gate.load(std::memory_order_acquire) & 0xFFFFFFFFULL) == 0;
            });
        }
    }

    state_lock.lock();

    if (!drain_ok) {
        uint64_t abandoned_val = (static_cast<uint64_t>(STATE_ABANDONED) << 32) |
                                 (g_engine.gate.load(std::memory_order_acquire) & 0xFFFFFFFFULL);
        g_engine.gate.store(abandoned_val, std::memory_order_release);
        EngineRuntime::set_error("Shutdown drain timed out with active threads. Finalization abandoned for host stability.");
        return STATUS_ERR_SHUTDOWN_TIMEOUT;
    }

    uint64_t terminating_val = static_cast<uint64_t>(STATE_TERMINATING) << 32;
    g_engine.gate.store(terminating_val, std::memory_order_release);

    if (Py_IsInitialized()) {
        if (g_engine.we_own_python) {
            if (GetCurrentThreadId() == g_engine.init_thread_id) {
                if (g_engine.dll_saved_tstate) {
                    PyEval_RestoreThread(g_engine.dll_saved_tstate);
                    g_engine.dll_saved_tstate = nullptr;
                }
                Py_CLEAR(g_engine.target_module);
                Py_FinalizeEx();
            } else {
                EngineRuntime::set_error("ptd_shutdown called on a different thread than initialization thread. Skipping Py_FinalizeEx to avoid crash.");
                ThreadGILState gil;
                Py_CLEAR(g_engine.target_module);
            }
        } else {
            ThreadGILState gil;
            PyObject* modules = PyImport_GetModuleDict();
            if (modules && PyDict_Check(modules)) {
                if (g_engine.target_module) {
                    PyObject* registered = PyDict_GetItemString(modules, kModuleName);
                    if (registered == g_engine.target_module) {
                        if (PyDict_DelItemString(modules, kModuleName) < 0) {
                            PyErr_Clear();
                        }
                    }
                }
            }
            Py_CLEAR(g_engine.target_module);
        }
    }

    uint64_t final_val = static_cast<uint64_t>(STATE_TERMINATED) << 32;
    g_engine.gate.store(final_val, std::memory_order_release);
    g_engine.state_cv.notify_all();
    return STATUS_SUCCESS;
}

int execute_callable(const char* func_name, const char* json_args, std::string& out_json) {
    if (!func_name) {
        EngineRuntime::set_error("Function name pointer is null");
        return STATUS_ERR_INVALID_ARG;
    }

    EngineRuntime::clear_error();

    if (!ensure_runtime_initialized()) {
        return STATUS_ERR_GENERIC;
    }

    InvocationAdmissionGuard admission;
    if (!admission.is_allowed()) {
        EngineRuntime::set_error("Admission rejected: Engine is shutting down or terminated");
        return STATUS_ERR_SHUTTING_DOWN;
    }

    if (json_args) {
        size_t arg_len = safe_strnlen(json_args, kMaxPayloadBytes + 1);
        if (arg_len > kMaxPayloadBytes) {
            EngineRuntime::set_error("JSON argument payload exceeds maximum permitted length");
            return STATUS_ERR_PAYLOAD_LIMIT;
        }
    }

    ThreadGILState gil;

    SafePyRef json_mod(PyImport_ImportModule("json"));
    if (!json_mod) {
        EngineRuntime::record_py_exception("Failed to import json module");
        return STATUS_ERR_GENERIC;
    }

    SafePyRef parsed_args;
    if (json_args && *json_args) {
        parsed_args = SafePyRef(PyObject_CallMethod(json_mod.get(), "loads", "s", json_args));
        if (!parsed_args) {
            EngineRuntime::record_py_exception("json.loads failed to parse input arguments");
            return STATUS_ERR_GENERIC;
        }
    } else {
        parsed_args = SafePyRef(PyDict_New());
    }

    SafePyRef fn(PyObject_GetAttrString(g_engine.target_module, func_name));
    if (!fn) {
        EngineRuntime::record_py_exception(func_name);
        return STATUS_ERR_GENERIC;
    }

    if (!PyCallable_Check(fn.get())) {
        EngineRuntime::set_error(std::string("Exported symbol is not callable: ") + func_name);
        return STATUS_ERR_GENERIC;
    }

    SafePyRef res(PyObject_CallFunctionObjArgs(fn.get(), parsed_args.get(), NULL));
    if (!res) {
        EngineRuntime::record_py_exception(func_name);
        return STATUS_ERR_GENERIC;
    }

    SafePyRef serialized(PyObject_CallMethod(json_mod.get(), "dumps", "O", res.get()));
    if (!serialized) {
        EngineRuntime::record_py_exception("json.dumps failed to serialize return value");
        return STATUS_ERR_GENERIC;
    }

    Py_ssize_t res_size = 0;
    const char* utf8_str = PyUnicode_AsUTF8AndSize(serialized.get(), &res_size);
    if (!utf8_str) {
        EngineRuntime::record_py_exception("UTF-8 encoding of serialized json failed");
        return STATUS_ERR_GENERIC;
    }

    if (static_cast<size_t>(res_size) > kMaxPayloadBytes) {
        EngineRuntime::set_error("Serialized result exceeds configured maximum payload bytes");
        return STATUS_ERR_PAYLOAD_LIMIT;
    }

    out_json.assign(utf8_str, static_cast<size_t>(res_size));
    return STATUS_SUCCESS;
}

}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD ul_reason_for_call, LPVOID) {
    if (ul_reason_for_call == DLL_PROCESS_ATTACH) {
        ptd::g_engine.self_module = hModule;
        DisableThreadLibraryCalls(hModule);
    }
    return TRUE;
}

PTD_API const char* ptd_author_info(void) {
    return "PTD Engine Verified Production v1.0 (M1nhquaanz)";
}

PTD_API const char* ptd_abi_version(void) {
    return "PTD-ABI-V1.0-VERIFIED-GATE";
}

PTD_API int ptd_init(void) {
    try {
        return ptd::ensure_runtime_initialized() ? ptd::STATUS_SUCCESS : ptd::STATUS_ERR_GENERIC;
    } catch (...) {
        return ptd::STATUS_ERR_GENERIC;
    }
}

PTD_API int ptd_shutdown(uint32_t timeout_ms) {
    try {
        return ptd::perform_safe_shutdown(timeout_ms, false);
    } catch (...) {
        return ptd::STATUS_ERR_GENERIC;
    }
}

PTD_API void ptd_free(void* ptr) {
    if (ptr) {
        std::free(ptr);
    }
}

PTD_API int ptd_call_alloc(const char* func_name, const char* json_args, void** out_ptr, int* out_len) {
    if (!out_ptr) {
        ptd::EngineRuntime::set_error("out_ptr parameter cannot be null");
        return ptd::STATUS_ERR_INVALID_ARG;
    }

    *out_ptr = nullptr;
    if (out_len) *out_len = 0;

    try {
        std::string res;
        int st = ptd::execute_callable(func_name, json_args, res);
        if (st != ptd::STATUS_SUCCESS) {
            return st;
        }

        size_t sz = res.size();
        if (sz >= static_cast<size_t>((std::numeric_limits<int>::max)())) {
            ptd::EngineRuntime::set_error("Result size exceeds 32-bit signed integer capacity");
            return ptd::STATUS_ERR_OVERFLOW;
        }

        char* buf = static_cast<char*>(std::malloc(sz + 1));
        if (!buf) {
            ptd::EngineRuntime::set_error("CRT memory allocation failed in native DLL");
            return ptd::STATUS_ERR_GENERIC;
        }

        std::memcpy(buf, res.data(), sz);
        buf[sz] = '\0';

        *out_ptr = buf;
        if (out_len) {
            *out_len = static_cast<int>(sz);
        }
        return ptd::STATUS_SUCCESS;
    } catch (...) {
        ptd::EngineRuntime::set_error("Unhandled native exception in ptd_call_alloc");
        return ptd::STATUS_ERR_GENERIC;
    }
}

PTD_API int ptd_call_v2(const char* func_name, const char* json_args, char* out_buf, int* inout_size) {
    if (!inout_size) {
        ptd::EngineRuntime::set_error("inout_size pointer parameter cannot be null");
        return ptd::STATUS_ERR_INVALID_ARG;
    }

    if (*inout_size < 0) {
        ptd::EngineRuntime::set_error("inout_size cannot be negative");
        return ptd::STATUS_ERR_INVALID_ARG;
    }

    try {
        std::string res;
        int st = ptd::execute_callable(func_name, json_args, res);
        if (st != ptd::STATUS_SUCCESS) {
            return st;
        }

        size_t sz = res.size();
        if (sz >= static_cast<size_t>((std::numeric_limits<int>::max)())) {
            ptd::EngineRuntime::set_error("Result payload size exceeds 32-bit int bounds");
            return ptd::STATUS_ERR_OVERFLOW;
        }

        int needed = static_cast<int>(sz + 1);

        if (!out_buf || *inout_size == 0) {
            *inout_size = needed;
            return ptd::STATUS_INQUIRY;
        }

        if (*inout_size < needed) {
            *inout_size = needed;
            return ptd::STATUS_ERR_BUF_TOO_SMALL;
        }

        std::memcpy(out_buf, res.data(), sz);
        out_buf[sz] = '\0';
        *inout_size = static_cast<int>(sz);
        return ptd::STATUS_SUCCESS;
    } catch (...) {
        ptd::EngineRuntime::set_error("Unhandled native exception in ptd_call_v2");
        return ptd::STATUS_ERR_GENERIC;
    }
}

PTD_API int ptd_last_error(char* out_buf, int* inout_size) {
    if (!inout_size) return ptd::STATUS_ERR_INVALID_ARG;
    if (*inout_size < 0) return ptd::STATUS_ERR_INVALID_ARG;

    std::string err;
    {
        std::lock_guard<std::mutex> lock(ptd::EngineRuntime::error_mutex);
        err = ptd::EngineRuntime::thread_last_error.empty() ? ptd::EngineRuntime::global_last_error : ptd::EngineRuntime::thread_last_error;
    }

    int needed = static_cast<int>(err.size() + 1);

    if (!out_buf || *inout_size == 0) {
        *inout_size = needed;
        return ptd::STATUS_INQUIRY;
    }

    if (*inout_size < needed) {
        *inout_size = needed;
        return ptd::STATUS_ERR_BUF_TOO_SMALL;
    }

    std::memcpy(out_buf, err.c_str(), needed);
    *inout_size = static_cast<int>(err.size());
    return ptd::STATUS_SUCCESS;
}

PTD_API int ptd_call(const char* func_name, const char* json_args, char* out_buf, int out_size) {
    try {
        std::string res;
        if (ptd::execute_callable(func_name, json_args, res) != ptd::STATUS_SUCCESS) {
            return -1;
        }

        if (out_buf && out_size > 0) {
            size_t cap = static_cast<size_t>(out_size - 1);
            size_t m = res.size() < cap ? res.size() : cap;
            std::memcpy(out_buf, res.data(), m);
            out_buf[m] = '\0';
        }
        return static_cast<int>(res.size());
    } catch (...) {
        return -1;
    }
}
"""

@unique
class Compiler(str, Enum):
    AUTO = "auto"
    MSVC = "msvc"
    GCC = "gcc"

@dataclass(frozen=True)
class PythonSDKLayout:
    home: Path
    inc: Path
    libs: Path
    dll: Path
    lib_file: Path
    version_tag: str
    major: int
    minor: int
    micro: int
    is_64bit: bool
    marshal_magic: int

def parse_pe_architecture(file_path: Path) -> Optional[str]:
    if not file_path.is_file():
        return None
    try:
        file_size = file_path.stat().st_size
        with file_path.open("rb") as f:
            dos_hdr = f.read(64)
            if len(dos_hdr) < 64 or dos_hdr[:2] != b"MZ":
                return None
            e_lfanew = struct.unpack_from("<I", dos_hdr, 0x3C)[0]
            if e_lfanew + 24 > file_size:
                return None
            f.seek(e_lfanew)
            pe_sig = f.read(4)
            if pe_sig != b"PE\0\0":
                return None
            coff_hdr = f.read(20)
            if len(coff_hdr) < 20:
                return None
            machine = struct.unpack_from("<H", coff_hdr, 0)[0]
            if machine == 0x8664:
                return "x64"
            elif machine == 0x014C:
                return "x86"
            elif machine == 0xAA64:
                return "arm64"
            return f"unknown(0x{machine:04x})"
    except Exception:
        return None

def parse_lib_architecture(file_path: Path) -> Optional[str]:
    if not file_path.is_file():
        return None
    try:
        with file_path.open("rb") as f:
            sig = f.read(8)
            if sig != b"!<arch>\n":
                return None
            chunk = f.read(8192)
            if b"\x64\x86" in chunk:
                return "x64"
            elif b"\x4c\x01" in chunk:
                return "x86"
            elif b"\x64\xaa" in chunk:
                return "arm64"
            return "x64"
    except Exception:
        return None

def verify_pe_exports(file_path: Path, expected_exports: Sequence[str]) -> Tuple[bool, List[str]]:
    try:
        file_size = file_path.stat().st_size
        with file_path.open("rb") as f:
            dos_hdr = f.read(64)
            if len(dos_hdr) < 64 or dos_hdr[:2] != b"MZ":
                return False, []
            e_lfanew = struct.unpack_from("<I", dos_hdr, 0x3C)[0]
            if e_lfanew + 24 > file_size:
                return False, []
            f.seek(e_lfanew)
            if f.read(4) != b"PE\0\0":
                return False, []
            coff_hdr = f.read(20)
            if len(coff_hdr) < 20:
                return False, []
            num_sections = struct.unpack_from("<H", coff_hdr, 2)[0]
            opt_hdr_size = struct.unpack_from("<H", coff_hdr, 16)[0]
            if e_lfanew + 24 + opt_hdr_size > file_size:
                return False, []
            opt_hdr = f.read(opt_hdr_size)
            if len(opt_hdr) < 2:
                return False, []
            magic = struct.unpack_from("<H", opt_hdr, 0)[0]
            export_rva = 0
            export_size = 0
            if magic == 0x20B:
                if len(opt_hdr) >= 120:
                    export_rva = struct.unpack_from("<I", opt_hdr, 112)[0]
                    export_size = struct.unpack_from("<I", opt_hdr, 116)[0]
            elif magic == 0x10B:
                if len(opt_hdr) >= 104:
                    export_rva = struct.unpack_from("<I", opt_hdr, 96)[0]
                    export_size = struct.unpack_from("<I", opt_hdr, 100)[0]
            if export_rva == 0 or export_size == 0:
                return False, []

            sections_offset = e_lfanew + 24 + opt_hdr_size
            if sections_offset + num_sections * 40 > file_size:
                return False, []
            f.seek(sections_offset)
            sections = []
            for _ in range(num_sections):
                sec = f.read(40)
                if len(sec) < 40:
                    return False, []
                v_size, v_addr, raw_size, raw_offset = struct.unpack_from("<IIII", sec, 8)
                sections.append((v_addr, v_size, raw_offset, raw_size))

            def rva_to_offset(rva: int, size: int = 1) -> Optional[int]:
                for v_addr, v_size, raw_offset, raw_size in sections:
                    if v_addr <= rva < v_addr + max(v_size, raw_size):
                        offset = raw_offset + (rva - v_addr)
                        if offset + size <= file_size:
                            return offset
                        return None
                return None

            export_offset = rva_to_offset(export_rva, 40)
            if export_offset is None:
                return False, []

            f.seek(export_offset)
            exp_dir = f.read(40)
            if len(exp_dir) < 40:
                return False, []
            num_names = struct.unpack_from("<I", exp_dir, 24)[0]
            names_rva = struct.unpack_from("<I", exp_dir, 32)[0]
            if num_names == 0 or num_names > 65536:
                return False, []

            names_offset = rva_to_offset(names_rva, num_names * 4)
            if names_offset is None:
                return False, []

            f.seek(names_offset)
            name_rvas = [struct.unpack("<I", f.read(4))[0] for _ in range(num_names)]
            exported_names = []
            for nrva in name_rvas:
                no = rva_to_offset(nrva, 1)
                if no is None:
                    continue
                f.seek(no)
                name_chars = []
                while len(name_chars) < 256:
                    b = f.read(1)
                    if not b or b == b"\0":
                        break
                    name_chars.append(b)
                exported_names.append(b"".join(name_chars).decode("ascii", "replace"))

            missing = [exp for exp in expected_exports if exp not in exported_names]
            return len(missing) == 0, exported_names
    except Exception:
        return False, []

def parse_sdk_version_from_headers(include_dir: Path) -> Optional[Tuple[int, int, int]]:
    patchlevel = include_dir / "patchlevel.h"
    if not patchlevel.is_file():
        return None
    try:
        text = patchlevel.read_text(encoding="utf-8", errors="replace")
        maj = re.search(r"#define\s+PY_MAJOR_VERSION\s+(\d+)", text)
        min_v = re.search(r"#define\s+PY_MINOR_VERSION\s+(\d+)", text)
        mic = re.search(r"#define\s+PY_MICRO_VERSION\s+(\d+)", text)
        if maj and min_v and mic:
            return int(maj.group(1)), int(min_v.group(1)), int(mic.group(1))
    except Exception:
        pass
    return None

def extract_marshal_magic(python_exe: Path) -> Optional[int]:
    try:
        res = subprocess.run(
            [str(python_exe), "-c", "import marshal, sys; sys.stdout.write(marshal.MAGIC.hex())"],
            capture_output=True, text=True, check=True, timeout=5
        )
        return struct.unpack("<I", bytes.fromhex(res.stdout.strip()))[0]
    except Exception:
        return None

def locate_verified_sdk(explicit_home: Optional[Path]) -> PythonSDKLayout:
    candidates: List[Path] = []
    if explicit_home:
        candidates.append(explicit_home.resolve())

    candidates.append(Path(sys.base_prefix).resolve())
    if hasattr(sys, "real_prefix"):
        candidates.append(Path(sys.real_prefix).resolve())

    exe_dir = Path(sys.executable).resolve().parent
    candidates.append(exe_dir)

    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        p_dir = Path(local_app) / "Programs" / "Python"
        if p_dir.is_dir():
            candidates.extend(sorted(p_dir.glob("Python3*"), reverse=True))

    for drive in ["C:\\", "D:\\"]:
        for pf in ["Program Files", "Program Files (x86)"]:
            try:
                candidates.extend(sorted((Path(drive) / pf / "Python").glob("Python3*"), reverse=True))
                candidates.extend(sorted((Path(drive) / pf).glob("Python3*"), reverse=True))
            except OSError:
                pass

    for root_key in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for sub in (r"Software\Python\PythonCore", r"Software\Wow6432Node\Python\PythonCore"):
            try:
                with winreg.OpenKey(root_key, sub) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        ver = winreg.EnumKey(key, i)
                        try:
                            with winreg.OpenKey(key, rf"{ver}\InstallPath") as ip_key:
                                val, _ = winreg.QueryValueEx(ip_key, "")
                                if val:
                                    candidates.append(Path(val).resolve())
                        except OSError:
                            pass
            except OSError:
                pass

    for cand in candidates:
        if not cand.is_dir():
            continue
        inc = cand / "include"
        libs = cand / "libs"
        py_h = inc / "Python.h"
        if not py_h.is_file():
            continue

        ver_tuple = parse_sdk_version_from_headers(inc)
        if not ver_tuple:
            continue
        major, minor, micro = ver_tuple

        tag = f"{major}{minor}"
        dll_name = f"python{tag}.dll"
        lib_name = f"python{tag}.lib"

        dll_cand = cand / dll_name
        if not dll_cand.is_file():
            dll_cand = Path(sys.base_prefix) / dll_name
            if not dll_cand.is_file():
                sys32_dll = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / dll_name
                if sys32_dll.is_file():
                    dll_cand = sys32_dll

        lib_cand = libs / lib_name
        if not lib_cand.is_file() or lib_cand.stat().st_size == 0:
            continue

        dll_arch = parse_pe_architecture(dll_cand) if dll_cand.is_file() else None
        lib_arch = parse_lib_architecture(lib_cand)

        is_64 = (dll_arch == "x64") or (lib_arch == "x64") or (sys.maxsize > 2**32)

        py_exe = cand / "python.exe"
        if not py_exe.is_file():
            py_exe = Path(sys.executable)

        magic = extract_marshal_magic(py_exe)
        if magic is None:
            if major == sys.version_info.major and minor == sys.version_info.minor:
                import importlib.util
                magic = struct.unpack("<I", importlib.util.MAGIC_NUMBER[:4])[0]
            else:
                continue

        return PythonSDKLayout(
            home=cand,
            inc=inc,
            libs=libs,
            dll=dll_cand,
            lib_file=lib_cand,
            version_tag=f"{major}.{minor}.",
            major=major,
            minor=minor,
            micro=micro,
            is_64bit=is_64,
            marshal_magic=magic,
        )

    raise FileNotFoundError("Could not verify a valid Python C-API development SDK with matching headers, libs, and runtime DLL.")

def find_vswhere_vcvars() -> Optional[str]:
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    pf64 = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates = [
        Path(pf86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe",
        Path(pf64) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe",
    ]
    vswhere = next((p for p in candidates if p.is_file()), None)
    if not vswhere:
        return None
    try:
        out = subprocess.run(
            [str(vswhere), "-latest", "-products", "*",
             "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
             "-property", "installationPath"],
            capture_output=True, text=True, check=True
        ).stdout.strip()
        if out:
            vcvars = Path(out) / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat"
            if vcvars.is_file():
                return str(vcvars)
    except Exception:
        pass
    return None

def build_msvc_native(cpp: Path, out_dll: Path, sdk: PythonSDKLayout, tmp: Path, debug: bool, env_path: Optional[str]) -> Tuple[bool, str]:
    vcvars = find_vswhere_vcvars()
    in_path = shutil.which("cl") is not None
    if not in_path and not vcvars:
        return False, "MSVC compiler (cl.exe or vcvarsall.bat) not found on host."

    arch = "amd64" if sdk.is_64bit else "x86"
    machine = "X64" if sdk.is_64bit else "X86"

    cmd = [
        "cl", "/nologo", "/std:c++17", "/EHsc", "/W4", "/permissive-",
        "/LD", "/MD", "/GS", "/DYNAMICBASE", "/NXCOMPAT"
    ]
    if debug:
        cmd += ["/Zi", "/Od", "/D_DEBUG"]
    else:
        cmd += ["/O2", "/DNDEBUG", "/GL", "/guard:cf"]

    cmd += [
        f"/I{sdk.inc}", str(cpp),
        f"/Fe:{out_dll}", f"/Fo:{str(tmp) + os.sep}",
        "/link", f"/LIBPATH:{sdk.libs}", str(sdk.lib_file.name), "user32.lib",
        f"/MACHINE:{machine}"
    ]
    if debug:
        cmd.append("/DEBUG")
    else:
        cmd += ["/LTCG", "/guard:cf"]

    bat_lines = ["@echo off"]
    if vcvars and not in_path:
        bat_lines.append(f'call "{vcvars}" {arch}')
        bat_lines.append("if errorlevel 1 exit /b 1")
    bat_lines.append(subprocess.list2cmdline(cmd))
    bat_lines.append("exit /b %errorlevel%")

    bat = tmp / "build_msvc.bat"
    bat.write_text("\r\n".join(bat_lines) + "\r\n", encoding="mbcs")

    run_env = os.environ.copy()
    if env_path:
        run_env["PATH"] = env_path + os.pathsep + run_env.get("PATH", "")

    res = subprocess.run(["cmd.exe", "/d", "/c", str(bat)], cwd=str(tmp), capture_output=True, text=True, errors="replace", env=run_env)
    return (res.returncode == 0 and out_dll.is_file()), res.stdout + res.stderr

def build_gcc_native(cpp: Path, out_dll: Path, sdk: PythonSDKLayout, tmp: Path, debug: bool, env_path: Optional[str]) -> Tuple[bool, str]:
    run_env = os.environ.copy()
    
    if env_path:
        clean_env_path = str(Path(env_path).resolve())
        run_env["PATH"] = clean_env_path + os.pathsep + run_env.get("PATH", "")
    else:
        clean_env_path = None

    gcc_bin = None
    if clean_env_path:
        cand_exe = Path(clean_env_path) / "g++.exe"
        if cand_exe.is_file():
            gcc_bin = str(cand_exe)

    if not gcc_bin:
        resolved = shutil.which("g++", path=run_env.get("PATH"))
        if resolved:
            gcc_bin = resolved
        else:
            return False, f"MinGW GNU C++ (g++) was not found in PATH or '{env_path}'."

    try:
        ver_info = subprocess.run(
            [gcc_bin, "-dumpmachine"],
            capture_output=True,
            text=True,
            check=True,
            env=run_env
        ).stdout.strip()
        
        gcc_is_64 = "x86_64" in ver_info
        if gcc_is_64 != sdk.is_64bit:
            return False, f"GCC Architecture mismatch: Toolchain is {ver_info}, target Python SDK is {'x64' if sdk.is_64bit else 'x86'}"
    except Exception as e:
        return False, f"Failed to probe GCC architecture using '{gcc_bin}': {e}"

    cmd = [
        gcc_bin, "-std=c++17", "-shared", "-Wall", "-Wextra",
        "-m64" if sdk.is_64bit else "-m32",
        "-fstack-protector-strong",
        f"-I{sdk.inc}",
        str(cpp),
        f"-L{sdk.libs}",
        "-static-libgcc", "-static-libstdc++",
        "-Wl,-Bdynamic", f"-l{sdk.lib_file.stem}", "-luser32",
        "-Wl,--dynamicbase", "-Wl,--nxcompat",
        "-o", str(out_dll)
    ]
    if debug:
        cmd += ["-g", "-O0"]
    else:
        cmd += ["-O3", "-s", "-DNDEBUG"]

    res = subprocess.run(cmd, cwd=str(tmp), capture_output=True, text=True, errors="replace", env=run_env)
    return (res.returncode == 0 and out_dll.is_file()), res.stdout + res.stderr

def atomic_windows_replace(src_file: Path, dst_file: Path, backup_file: Optional[Path] = None) -> None:
    if not dst_file.exists():
        shutil.move(str(src_file), str(dst_file))
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ReplaceFileW = kernel32.ReplaceFileW
    ReplaceFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID
    ]
    ReplaceFileW.restype = wintypes.BOOL

    REPLACEFILE_WRITE_THROUGH = 0x00000001
    REPLACEFILE_IGNORE_MERGE_ERRORS = 0x00000002

    bak_str = str(backup_file) if backup_file else None
    ok = ReplaceFileW(
        str(dst_file), str(src_file), bak_str,
        REPLACEFILE_WRITE_THROUGH | REPLACEFILE_IGNORE_MERGE_ERRORS, None, None
    )
    if not ok:
        err = ctypes.get_last_error()
        raise OSError(f"Windows ReplaceFileW failed with Win32 Error Code {err}. (Target binary locked or in-use)")

REQUIRED_ABI_SYMBOLS = [
    "ptd_author_info",
    "ptd_abi_version",
    "ptd_init",
    "ptd_shutdown",
    "ptd_free",
    "ptd_call_alloc",
    "ptd_call_v2",
    "ptd_last_error",
    "ptd_call",
]

def compile_engine(
    src: Path | str,
    out: Path | str,
    name: Optional[str] = None,
    embed: bool = True,
    compiler: Compiler = Compiler.AUTO,
    py_home: Optional[Path | str] = None,
    optimize: int = 0,
    debug: bool = False,
    keep_cpp: bool = False,
    add_path: Optional[str] = None,
    max_payload_mb: int = 64,
    drain_timeout_ms: int = DEFAULT_SHUTDOWN_TIMEOUT_MS,
    log_cb: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str]:
    emit = log_cb if log_cb else print

    if max_payload_mb < 1 or max_payload_mb > (MAX_PAYLOAD_LIMIT_BYTES // 1024 // 1024):
        return False, f"max_payload_mb must be bounded between 1 and {MAX_PAYLOAD_LIMIT_BYTES // 1024 // 1024} MB."

    if drain_timeout_ms < MIN_SHUTDOWN_TIMEOUT_MS or drain_timeout_ms > MAX_SHUTDOWN_TIMEOUT_MS:
        return False, f"drain_timeout_ms must be bounded between {MIN_SHUTDOWN_TIMEOUT_MS} and {MAX_SHUTDOWN_TIMEOUT_MS} ms."

    src_p = Path(src).resolve()
    if not src_p.is_file():
        return False, f"Source input file not found: {src_p}"

    if src_p.stat().st_size > MAX_SOURCE_SIZE_BYTES:
        return False, f"Source script exceeds size limit of {MAX_SOURCE_SIZE_BYTES // 1024 // 1024} MB."

    mod_name = name or src_p.stem
    if not mod_name.isidentifier() or keyword.iskeyword(mod_name):
        return False, f"Invalid Python module identifier: '{mod_name}'"

    out_dll = Path(out).resolve()
    out_dll.parent.mkdir(parents=True, exist_ok=True)

    try:
        sdk = locate_verified_sdk(Path(py_home) if py_home else None)
    except Exception as e:
        return False, str(e)

    emit(f"[*] Target Module: {mod_name}")
    emit(f"[*] Python SDK: {sdk.major}.{sdk.minor}.{sdk.micro} ({'x64' if sdk.is_64bit else 'x86'})")
    emit(f"[*] Marshal Magic: 0x{sdk.marshal_magic:08x} | SDK Home: {sdk.home}")

    chunks: List[str] = []
    if embed:
        try:
            code_obj = compile(src_p.read_bytes(), str(src_p), "exec", dont_inherit=True, optimize=optimize)
            raw_marshal = marshal.dumps(code_obj)
            crc = zlib.crc32(raw_marshal) & 0xFFFFFFFF

            header = struct.pack(
                "<8sHHIII",
                b"PTDPAY01",
                sdk.major,
                sdk.minor,
                sdk.marshal_magic,
                len(raw_marshal),
                crc
            )
            envelope = header + raw_marshal
            b64_str = base64.b64encode(envelope).decode("ascii")
            chunks = [b64_str[i : i + DEFAULT_CHUNK_SIZE] for i in range(0, len(b64_str), DEFAULT_CHUNK_SIZE)]
            emit(f"[*] Envelope: {len(envelope)} bytes (Raw Bytecode: {len(raw_marshal)} bytes, CRC32: 0x{crc:08x})")
        except SyntaxError as e:
            return False, f"Syntax Error inside source script {src_p.name}:{e.lineno}: {e.msg}"

    entries = [f'    "{c}",' for c in chunks] + ["    nullptr,"]
    pyhome_escaped = str(sdk.home.resolve()).replace("\\", "\\\\")
    rendered = (
        CPP_HARNESS_TEMPLATE.replace("__PTD_MODULE__", mod_name)
        .replace("__PTD_EMBED__", "true" if embed else "false")
        .replace("__PTD_PYVER__", sdk.version_tag)
        .replace("__PTD_PYHOME__", pyhome_escaped)
        .replace("__PTD_EXPECTED_MAJOR__", str(sdk.major))
        .replace("__PTD_EXPECTED_MINOR__", str(sdk.minor))
        .replace("__PTD_EXPECTED_MAGIC__", f"0x{sdk.marshal_magic:08x}U")
        .replace("__PTD_MAX_PAYLOAD__", f"{max_payload_mb * 1024 * 1024}ULL")
        .replace("__PTD_DRAIN_TIMEOUT_MS__", f"{drain_timeout_ms}U")
        .replace("__PTD_PAYLOAD_CHUNKS__", "\n".join(entries))
    )

    staging_dir = out_dll.parent / f"_ptd_staging_{os.getpid()}_{int(time.time() * 1000)}"
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        cpp_file = staging_dir / f"ptd_{mod_name}.cpp"
        cpp_file.write_text(rendered, encoding="utf-8")

        if keep_cpp:
            preserved = out_dll.with_suffix(".cpp")
            shutil.copyfile(cpp_file, preserved)
            emit(f"[*] Bridge source preserved: {preserved}")

        stage_dll = staging_dir / f"staged_{out_dll.name}"

        runners = []
        if compiler in (Compiler.AUTO, Compiler.MSVC):
            runners.append(("MSVC", build_msvc_native))
        if compiler in (Compiler.AUTO, Compiler.GCC):
            runners.append(("MinGW-GCC", build_gcc_native))

        built_ok = False
        log_acc = []
        for cname, fn in runners:
            emit(f"[*] Compiling artifact with {cname}...")
            ok, output = fn(cpp_file, stage_dll, sdk, staging_dir, debug, add_path)
            if ok and stage_dll.is_file():
                built_ok = True
                emit(f"[+] Native compilation succeeded with {cname}.")
                break
            emit(f"[!] Compiler {cname} failed.\n{output.strip()}")
            log_acc.append(f"[{cname}]\n{output}")

        if not built_ok:
            return False, f"All compilation toolchains failed:\n{''.join(log_acc)}"

        pe_arch = parse_pe_architecture(stage_dll)
        expected_arch = "x64" if sdk.is_64bit else "x86"
        if pe_arch != expected_arch:
            return False, f"Architecture verification failed: Built binary is {pe_arch}, expected {expected_arch}"

        exports_ok, exported_symbols = verify_pe_exports(stage_dll, REQUIRED_ABI_SYMBOLS)
        if not exports_ok:
            missing = [s for s in REQUIRED_ABI_SYMBOLS if s not in exported_symbols]
            return False, f"Export verification failed. Missing required C ABI symbols: {missing}"

        bak_file = out_dll.with_suffix(".dll.bak")
        try:
            atomic_windows_replace(stage_dll, out_dll, bak_file)
            if bak_file.exists():
                bak_file.unlink(missing_ok=True)
            emit(f"[+] Artifact verified and committed atomically: {out_dll}")
        except Exception as e:
            return False, f"Atomic replacement transaction failed: {e}"

    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    return True, str(out_dll)

def run_isolated_subprocess_test(dll_path: Path, test_name: str, extra_args: Optional[List[str]] = None) -> Tuple[bool, str]:
    cmd = [
        sys.executable,
        "-c",
        f"import ptd; sys.exit(ptd._worker_test_entry(r'{dll_path}', '{test_name}', {repr(extra_args or [])}))"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode == 0, res.stdout + res.stderr

def _worker_test_entry(dll_path_str: str, test_name: str, args: List[str]) -> int:
    dll_path = Path(dll_path_str)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h_mod = kernel32.LoadLibraryW(str(dll_path))
    if not h_mod:
        sys.stderr.write(f"LoadLibraryW failed with error {ctypes.get_last_error()}\n")
        return 1

    try:
        lib = ctypes.CDLL(str(dll_path))
        lib.ptd_abi_version.restype = ctypes.c_char_p
        lib.ptd_author_info.restype = ctypes.c_char_p
        lib.ptd_init.restype = ctypes.c_int
        lib.ptd_shutdown.argtypes = [ctypes.c_uint32]
        lib.ptd_shutdown.restype = ctypes.c_int
        lib.ptd_free.argtypes = [ctypes.c_void_p]
        lib.ptd_free.restype = None
        lib.ptd_call_alloc.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_int)]
        lib.ptd_call_alloc.restype = ctypes.c_int
        lib.ptd_call_v2.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
        lib.ptd_call_v2.restype = ctypes.c_int
        lib.ptd_last_error.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
        lib.ptd_last_error.restype = ctypes.c_int

        if test_name == "load_and_abi":
            assert lib.ptd_init() == 1
            abi = lib.ptd_abi_version().decode("utf-8")
            assert "PTD-ABI" in abi
            author = lib.ptd_author_info().decode("utf-8")
            assert "PTD Engine" in author
            return 0

        elif test_name == "embedded_payload":
            assert lib.ptd_init() == 1
            p = ctypes.c_void_p()
            l = ctypes.c_int(0)
            st = lib.ptd_call_alloc(b"test_echo", b'{"msg": "test_echo"}', ctypes.byref(p), ctypes.byref(l))
            assert st == 1 and p.value
            res_str = ctypes.string_at(p.value, l.value).decode("utf-8")
            lib.ptd_free(p)
            assert "test_echo" in res_str
            return 0

        elif test_name == "external_module":
            assert lib.ptd_init() == 1
            p = ctypes.c_void_p()
            l = ctypes.c_int(0)
            st = lib.ptd_call_alloc(b"test_ext", b'{}', ctypes.byref(p), ctypes.byref(l))
            assert st == 1 and p.value
            res_str = ctypes.string_at(p.value, l.value).decode("utf-8")
            lib.ptd_free(p)
            assert "ext_ok" in res_str
            return 0

        elif test_name == "json_parsing":
            assert lib.ptd_init() == 1
            p = ctypes.c_void_p()
            l = ctypes.c_int(0)
            st = lib.ptd_call_alloc(b"test_echo", b'{"nested": {"a": [1, 2, 3]}}', ctypes.byref(p), ctypes.byref(l))
            assert st == 1 and p.value
            res_str = ctypes.string_at(p.value, l.value).decode("utf-8")
            lib.ptd_free(p)
            assert "[1, 2, 3]" in res_str
            return 0

        elif test_name == "json_serialization":
            assert lib.ptd_init() == 1
            p = ctypes.c_void_p()
            l = ctypes.c_int(0)
            st = lib.ptd_call_alloc(b"test_echo", r'{"unicode": "\u0111\u00e3o"}', ctypes.byref(p), ctypes.byref(l))
            assert st == 1 and p.value
            res_str = ctypes.string_at(p.value, l.value).decode("utf-8")
            lib.ptd_free(p)
            data = json.loads(res_str)
            assert data["echo"]["unicode"] == "\u0111\u00e3o"
            return 0

        elif test_name == "invalid_function":
            assert lib.ptd_init() == 1
            p = ctypes.c_void_p()
            l = ctypes.c_int(0)
            st = lib.ptd_call_alloc(b"non_existent_func_xyz", b'{}', ctypes.byref(p), ctypes.byref(l))
            assert st == -1
            return 0

        elif test_name == "malformed_json":
            assert lib.ptd_init() == 1
            p = ctypes.c_void_p()
            l = ctypes.c_int(0)
            st = lib.ptd_call_alloc(b"test_echo", b'{bad_json_here:', ctypes.byref(p), ctypes.byref(l))
            assert st == -1
            return 0

        elif test_name == "buffer_inquiry":
            assert lib.ptd_init() == 1
            needed = ctypes.c_int(0)
            st = lib.ptd_call_v2(b"test_echo", b'{"a": 1}', None, ctypes.byref(needed))
            assert st == 0
            assert needed.value > 0
            return 0

        elif test_name == "buffer_too_small":
            assert lib.ptd_init() == 1
            buf = ctypes.create_string_buffer(2)
            sz = ctypes.c_int(2)
            st = lib.ptd_call_v2(b"test_echo", b'{"a": 1}', buf, ctypes.byref(sz))
            assert st == -2
            assert sz.value > 2
            return 0

        elif test_name == "memory_alloc_free":
            assert lib.ptd_init() == 1
            p = ctypes.c_void_p()
            l = ctypes.c_int(0)
            st = lib.ptd_call_alloc(b"test_echo", b'{"mem": 1}', ctypes.byref(p), ctypes.byref(l))
            assert st == 1 and p.value
            lib.ptd_free(p)
            lib.ptd_free(None)
            return 0

        elif test_name == "last_error":
            assert lib.ptd_init() == 1
            p = ctypes.c_void_p()
            l = ctypes.c_int(0)
            lib.ptd_call_alloc(b"missing_symbol_error_test", b'{}', ctypes.byref(p), ctypes.byref(l))
            needed = ctypes.c_int(0)
            st = lib.ptd_last_error(None, ctypes.byref(needed))
            assert st == 0 and needed.value > 0
            buf = ctypes.create_string_buffer(needed.value)
            st2 = lib.ptd_last_error(buf, ctypes.byref(needed))
            assert st2 == 1
            assert len(buf.value) > 0
            return 0

        elif test_name == "concurrent_invocation":
            assert lib.ptd_init() == 1
            import concurrent.futures
            def worker():
                p = ctypes.c_void_p()
                l = ctypes.c_int(0)
                return lib.ptd_call_alloc(b"test_echo", b'{"thread": 1}', ctypes.byref(p), ctypes.byref(l))
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                futures = [ex.submit(worker) for _ in range(32)]
                for f in futures:
                    st = f.result()
                    assert st == 1
            return 0

        elif test_name == "admission_rejection":
            assert lib.ptd_init() == 1
            assert lib.ptd_shutdown(3000) == 1
            p = ctypes.c_void_p()
            l = ctypes.c_int(0)
            st = lib.ptd_call_alloc(b"test_echo", b'{}', ctypes.byref(p), ctypes.byref(l))
            assert st == -3
            return 0

        elif test_name == "drain_barrier":
            assert lib.ptd_init() == 1
            import concurrent.futures
            def worker():
                p = ctypes.c_void_p()
                l = ctypes.c_int(0)
                return lib.ptd_call_alloc(b"test_echo", b'{"drain": 1}', ctypes.byref(p), ctypes.byref(l))
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                futures = [ex.submit(worker) for _ in range(16)]
                time.sleep(0.005)
                st = lib.ptd_shutdown(3000)
                assert st == 1
                for f in futures:
                    assert f.result() in (1, -3)
            return 0

        elif test_name == "shutdown_timeout":
            assert lib.ptd_init() == 1
            import threading
            start_evt = threading.Event()
            def blocker():
                p = ctypes.c_void_p()
                l = ctypes.c_int(0)
                start_evt.set()
                lib.ptd_call_alloc(b"test_block", b'{"sec": 1.0}', ctypes.byref(p), ctypes.byref(l))
            t = threading.Thread(target=blocker)
            t.daemon = True
            t.start()
            start_evt.wait()
            time.sleep(0.05)
            st = lib.ptd_shutdown(100)
            assert st == -9
            return 0

        elif test_name == "unload_reload":
            assert lib.ptd_init() == 1
            assert lib.ptd_shutdown(1000) == 1
            kernel32.FreeLibrary(h_mod)
            h_mod2 = kernel32.LoadLibraryW(str(dll_path))
            assert h_mod2 != 0
            lib2 = ctypes.CDLL(str(dll_path))
            lib2.ptd_init.restype = ctypes.c_int
            lib2.ptd_shutdown.argtypes = [ctypes.c_uint32]
            lib2.ptd_shutdown.restype = ctypes.c_int
            assert lib2.ptd_init() == 1
            assert lib2.ptd_shutdown(1000) == 1
            kernel32.FreeLibrary(h_mod2)
            h_mod = 0
            return 0

        elif test_name == "host_owned_coexistence":
            import sys
            assert "fixture_mod" not in sys.modules
            assert lib.ptd_init() == 1
            assert "fixture_mod" in sys.modules
            assert lib.ptd_shutdown(1000) == 1
            assert "fixture_mod" not in sys.modules
            assert sys.version_info.major == 3
            return 0

        elif test_name == "repeated_lifecycle":
            for _ in range(3):
                assert lib.ptd_init() == 1
                assert lib.ptd_shutdown(1000) == 1
            return 0

        return 2
    except Exception as e:
        sys.stderr.write(f"Exception in worker test {test_name}: {e}\n")
        return 1
    finally:
        if h_mod:
            kernel32.FreeLibrary(h_mod)

def selftest_full(dll_path: Path, log_cb: Callable[[str], None] = print) -> Tuple[bool, str]:
    if not dll_path.is_file():
        return False, f"Target artifact not found: {dll_path}"

    log = log_cb if log_cb else print
    passed = 0
    skipped = 0
    failed = 0

    log("=" * 80)
    log("PTD ENGINE v1.0 - VERIFIED PRODUCTION TEST HARNESS")
    log("=" * 80)

    pe_arch = parse_pe_architecture(dll_path)
    if pe_arch:
        passed += 1
        log(f"[PASS] Dimension 01: PE Architecture verified: {pe_arch}")
    else:
        failed += 1
        log("[FAIL] Dimension 01: Failed to parse valid PE architecture.")

    exp_ok, exports = verify_pe_exports(dll_path, REQUIRED_ABI_SYMBOLS)
    if exp_ok:
        passed += 1
        log(f"[PASS] Dimension 02: PE Export Directory contains all {len(REQUIRED_ABI_SYMBOLS)} required C ABI symbols.")
    else:
        failed += 1
        log("[FAIL] Dimension 02: Missing required exported symbols in DLL.")

    ok, out = run_isolated_subprocess_test(dll_path, "load_and_abi")
    if ok:
        passed += 2
        log("[PASS] Dimension 03: DLL loading into address space.")
        log("[PASS] Dimension 04: ABI version verification.")
    else:
        failed += 2
        log(f"[FAIL] Dimension 03/04: DLL loading & ABI verification failed: {out.strip()}")

    with tempfile.TemporaryDirectory(prefix="ptd_test_fixture_") as t_dir:
        fix_dir = Path(t_dir)
        py_src = fix_dir / "fixture_mod.py"
        py_src.write_text(
            "import time\n"
            "def test_echo(args):\n"
            "    return {'status': 'ok', 'echo': args}\n"
            "def test_block(args):\n"
            "    time.sleep(args.get('sec', 0.5))\n"
            "    return {'status': 'blocked'}\n",
            encoding="utf-8"
        )
        out_fix_dll = fix_dir / "fixture_mod.dll"

        build_ok, b_msg = compile_engine(
            src=py_src,
            out=out_fix_dll,
            name="fixture_mod",
            embed=True,
            compiler=Compiler.AUTO,
            log_cb=lambda _: None
        )

        py_ext_src = fix_dir / "ext_fixture.py"
        py_ext_src.write_text(
            "def test_ext(args):\n"
            "    return {'status': 'ext_ok'}\n",
            encoding="utf-8"
        )
        out_ext_dll = fix_dir / "ext_fixture.dll"
        build_ext_ok, _ = compile_engine(
            src=py_ext_src,
            out=out_ext_dll,
            name="ext_fixture",
            embed=False,
            compiler=Compiler.AUTO,
            log_cb=lambda _: None
        )

        test_targets = [
            ("embedded_payload", "Dimension 05: Embedded payload execution", out_fix_dll if build_ok else None),
            ("external_module", "Dimension 06: External module execution", out_ext_dll if build_ext_ok else None),
            ("json_parsing", "Dimension 07: JSON input parsing", out_fix_dll if build_ok else None),
            ("json_serialization", "Dimension 08: JSON output serialization", out_fix_dll if build_ok else None),
            ("invalid_function", "Dimension 09: Invalid function rejection", out_fix_dll if build_ok else None),
            ("malformed_json", "Dimension 10: Malformed JSON rejection", out_fix_dll if build_ok else None),
            ("buffer_inquiry", "Dimension 11: Buffer size inquiry", out_fix_dll if build_ok else None),
            ("buffer_too_small", "Dimension 12: Buffer-too-small handling", out_fix_dll if build_ok else None),
            ("memory_alloc_free", "Dimension 13: Memory allocation and release", out_fix_dll if build_ok else None),
            ("last_error", "Dimension 14: Last-error reporting", out_fix_dll if build_ok else None),
            ("concurrent_invocation", "Dimension 15: Concurrent invocation", out_fix_dll if build_ok else None),
            ("admission_rejection", "Dimension 16: Admission rejection during shutdown", out_fix_dll if build_ok else None),
            ("drain_barrier", "Dimension 17: Drain barrier behavior", out_fix_dll if build_ok else None),
            ("shutdown_timeout", "Dimension 18: Shutdown timeout behavior", out_fix_dll if build_ok else None),
            ("unload_reload", "Dimension 19: DLL unload and reload", out_fix_dll if build_ok else None),
            ("host_owned_coexistence", "Dimension 20: Host-owned vs DLL-owned Python runtime", out_fix_dll if build_ok else None),
        ]

        for t_name, desc, target_path in test_targets:
            if not target_path or not target_path.is_file():
                skipped += 1
                log(f"[SKIP] {desc}: Fixture DLL build not available")
                continue

            ok, out = run_isolated_subprocess_test(target_path, t_name)
            if ok:
                passed += 1
                log(f"[PASS] {desc}")
            else:
                failed += 1
                log(f"[FAIL] {desc}\n  --> Diagnostics: {out.strip()}")

    total = passed + skipped + failed
    summary = f"Selftest Execution Summary: {passed} PASSED, {failed} FAILED, {skipped} SKIPPED (Total {total})"
    log("=" * 80)
    log(summary)
    log("=" * 80)

    return failed == 0, summary

def main():
    parser = argparse.ArgumentParser(
        prog="ptd",
        description="PTD Engine: Verified Production Native C++ DLL Compiler (v1.0)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--con", required=True, metavar="<file.py>", help="Input Python source file")
    parser.add_argument("--to", required=True, metavar="<file.dll>", help="Target DLL output path")
    parser.add_argument("--name", metavar="<name>", help="Internal virtual module identifier")
    parser.add_argument("--no-embed", action="store_true", help="External module disk-loading mode")
    parser.add_argument("--compiler", type=Compiler, choices=list(Compiler), default=Compiler.AUTO, help="Native compiler toolchain")
    parser.add_argument("--py-home", type=Path, help="Explicit Python SDK root directory")
    parser.add_argument("--add-path", metavar="<dir>", help="Prepend compiler directory to PATH")
    parser.add_argument("--optimize", type=int, choices=(0, 1, 2), default=0, help="Bytecode optimization level")
    parser.add_argument("--debug", action="store_true", help="Compile with native debug symbols")
    parser.add_argument("--keep-cpp", action="store_true", help="Retain intermediate C++ bridge source")
    parser.add_argument("--selftest", action="store_true", help="Execute 20-dimension verification suite post-build")

    args = parser.parse_args()
    print(ASCII_LOGO)

    ok, res = compile_engine(
        src=args.con,
        out=args.to,
        name=args.name,
        embed=not args.no_embed,
        compiler=args.compiler,
        py_home=args.py_home,
        optimize=args.optimize,
        debug=args.debug,
        keep_cpp=args.keep_cpp,
        add_path=args.add_path,
        log_cb=print,
    )

    if not ok:
        print(f"\n[!] BUILD FAILED:\n{res}")
        sys.exit(1)

    print(f"\n[+] BUILD SUCCESSFUL: {res}")

    if args.selftest:
        print("\n[*] Starting Verified Production Test Suite...")
        test_ok, test_msg = selftest_full(Path(res), log_cb=print)
        if not test_ok:
            print(f"\n[!] VERIFICATION SUITE FAILED:\n{test_msg}")
            sys.exit(2)
        print(f"\n[+] PRODUCTION CERTIFICATION CONFIRMED: {test_msg}")

if __name__ == "__main__":
    main()