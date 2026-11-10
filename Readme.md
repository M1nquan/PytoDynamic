# PTD Engine (Python-To-Dynamic) • Industrial Standard v1.0

PTD Engine is an industrial toolchain designed to compile Python (`.py`) scripts into native Windows dynamic link libraries (`.dll`) exposing a standardized **C-ABI Export Contract**. The project includes **PTD Studio**, a native dark-themed workstation UI available via an installer for visual build management, telemetry streaming, and PE verification.

---

## 📌 1. PTD Engine CLI (`ptd`)

**PTD Engine** solves Python-to-Native interoperability (C, C++, Rust, Go, C#) by packing full Python runtime logic directly inside a self-contained shared library:

* **In-Memory Bytecode Envelope (`PTDPAY01`)**: Compiles and compresses Python bytecode with strict **CRC32** verification, embedded directly in the DLL PE binary.
* **Isolated CPython Runtime**: Initializes an isolated runtime sandbox with automatic multithreaded GIL state management (`PyGILState_Ensure` / `PyGILState_Release`).
* **RCU Gate Barrier**: Enforces thread-safe admission draining (`Drain Timeout`) to prevent race conditions and memory leaks on host shutdown.
* **Standardized C-ABI Export Contract**:
  * `ptd_init()`: Initializes the isolated Python runtime.
  * `ptd_shutdown(timeout_ms)`: Drains active invocations and terminates safely.
  * `ptd_call_alloc(func_name, json_args, out_ptr, out_len)`: Executes an exported Python callable using JSON payloads.
  * `ptd_free(ptr)`: Releases CRT memory allocated by the DLL.
  * `ptd_last_error(buf, inout_sz)`: Retrieves thread-local and global error telemetry.

### 💻 Command-Line Usage

Compile any Python script into a native DLL using the `ptd` command:

```powershell
# Standard compilation using MinGW GCC
ptd --con <script.py> --to <output.dll> --compiler gcc --add-path "C:\path\to\mingw64\bin"

# Example:
ptd --con idk.py --to idk.dll --name idk --compiler gcc --add-path "C:\winlibs-x86_64-posix-seh-gcc-16.2.0-mingw-w64ucrt-14.0.0-r3\mingw64\bin"
```

Common flags:
* `--con <file.py>`: Input Python source file (required).
* `--to <file.dll>`: Target DLL output path (required).
* `--name <ident>`: Internal module identifier (defaults to script stem).
* `--compiler <auto|gcc|msvc>`: Toolchain selection (default: `auto`).
* `--add-path <dir>`: Prepend compiler binary directory (`bin`) to PATH.
* `--optimize <0|1|2>`: Bytecode optimization level.
* `--debug`: Emit native debug symbols (`/Zi` or `-g`).
* `--selftest`: Run the automated 20-dimension verification suite post-build.

---

## 🖥️ 2. PTD Studio UI

**PTD Studio** provides a dedicated, native developer workstation interface for building, analyzing, and verifying artifacts without touching the command line.

* **Drag-and-Drop Workflow**: Drag `.py` scripts to configure source and target paths automatically.
* **Auto-Discovery**: Automatically probes system registry and directories for active Python SDKs (`include/` and `libs/`) and C++ toolchains.
* **Build Telemetry Console**: Real-time diagnostic terminal streaming compiler steps and linker flags.
* **PE / ABI Inspector**: Inspects PE export tables to verify all 9 C-ABI contracts.
* **20-Dimension Verification Matrix**: Executes isolated sub-process tests covering multi-threading, JSON serialization, and memory lifecycle safety.

### 📦 Installation

Download and run the latest standalone setup wizard from GitHub Releases:

1. Go to the [Releases](https://github.com/M1nquan/ptd/releases) page.
2. Download `PTD-Studio-Setup-v1.0.exe`.
3. Run the installer to install PTD Studio and register `ptd` into your system `PATH`.

---

## 🛠️ 3. Requirements

Before building libraries with PTD Engine, ensure your system meets the following prerequisites:

### 1. Operating System
* **Windows 10 / 11** (64-bit `x64` or 32-bit `x86`).

### 2. Python Environment
* **Python 3.10, 3.11, or 3.12** (Official CPython releases from [python.org](https://www.python.org/) recommended).
* Ensure **Development Headers** (`include/` and `libs/`) were checked during Python installation.

### 3. C++ Toolchain (Choose One)
* **MinGW-w64 GCC (Recommended)**:
  * WinLibs Standalone Build (UCRT / POSIX / SEH): [winlibs.com](https://winlibs.com/)
  * Requires `g++.exe` in `PATH` or passed via `--add-path`.
* **Microsoft Visual C++ (MSVC)**:
  * Visual Studio 2019/2022 or Visual Studio Build Tools with the **Desktop development with C++** workload.

---

## 📄 License

Distributed under the **GNU License**.

*Authored by **M1nhquaanz** — 2026.*
