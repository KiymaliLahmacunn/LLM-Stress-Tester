"""
core/utils.py
-------------
Hardware detection and network port configuration.
"""

import functools
import os
import subprocess

# Known local LLM server ports.
# NOTE: Port 8000 is excluded because our own FastAPI server runs on it.
TARGET_PORTS = {
    "LM Studio": 1234,
    "Ollama": 11434,
    "vLLM/FastAPI": 8080,
    "Oobabooga": 5000,
}

OUR_SERVER_PORT = int(
    os.getenv("PORT", 8000)
)  # Used to skip self-scan in model discovery


@functools.lru_cache(maxsize=1)
def detect_hardware() -> tuple[str, str]:
    """
    Detect GPU or CPU hardware. Result is cached for the process lifetime
    since hardware doesn't change between test runs.

    Returns:
        (display_name, hex_color) tuple.
    """
    # Try NVIDIA GPU first
    try:
        out = subprocess.check_output(
            "nvidia-smi -L", shell=True, text=True, stderr=subprocess.DEVNULL
        )
        if "NVIDIA" in out:
            gpu = out.split(":")[1].split("(")[0].strip()
            gpu = gpu.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "")
            return gpu, "#c0392b"
    except Exception:
        pass

    # Fall back to CPU via Windows registry
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        )
        name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        winreg.CloseKey(key)

        # Clean up the name
        for noise in ("(R)", "(TM)", "(tm)", " CPU"):
            name = name.replace(noise, "")
        if "@" in name:
            name = name.split("@")[0].strip()
        if "with Radeon" in name:
            name = name.split("with Radeon")[0].strip()
        if "Processor" in name:
            name = name.replace("Processor", "").strip()
        if len(name) > 30:
            name = name[:28] + "..."
        return name, "#2980b9"
    except ImportError:
        # winreg not available (non-Windows platform)
        import platform

        return platform.processor() or "Local CPU", "#2980b9"
    except Exception:
        return "Local CPU", "#2980b9"
