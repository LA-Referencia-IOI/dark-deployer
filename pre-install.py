#!/usr/bin/env python3
"""
pre-install.py — dARK Deployer server prerequisites checker and installer.

Detects the system configuration (OS, architecture, Python, Docker, build
tools), reports what is missing or incompatible, and — with user permission —
installs all required dependencies before running install.py.

Usage:
    python3 pre-install.py            # check, then offer to install
    python3 pre-install.py --check    # check only, no changes
"""

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path


# ─── Constants ────────────────────────────────────────────────────────────────

MIN_DOCKER_VERSION  = (20, 10)
COMPATIBLE_PYTHON   = (10, 11, 12)   # minor versions of Python 3.x supported by web3

OK          = "OK"
MISSING     = "MISSING"
INCOMPATIBLE = "INCOMPATIBLE"
WARNING     = "WARNING"

ICON = {OK: "[OK]  ", MISSING: "[MISS]", INCOMPATIBLE: "[OLD] ", WARNING: "[WARN]"}


# ─── Shell helpers ────────────────────────────────────────────────────────────

def _run(cmd: str) -> tuple[int, str, str]:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def _run_live(cmd: str) -> int:
    """Run a command with output visible to the user; return exit code."""
    return subprocess.run(cmd, shell=True).returncode


def _apt_get(packages: str) -> bool:
    rc = _run_live(f"sudo apt-get install -y --no-install-recommends {packages}")
    return rc == 0


# ─── Detection ────────────────────────────────────────────────────────────────

def detect_os() -> dict:
    info: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                info[k.strip()] = v.strip().strip('"')
    except FileNotFoundError:
        pass
    return {
        "id":       info.get("ID", platform.system().lower()),
        "version":  info.get("VERSION_ID", ""),
        "codename": info.get("VERSION_CODENAME", ""),
        "name":     info.get("PRETTY_NAME", platform.platform()),
    }


def detect_arch() -> str:
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())


def detect_python_versions() -> dict[str, str]:
    found: dict[str, str] = {}
    for minor in range(9, 15):
        rc, out, _ = _run(f"python3.{minor} --version 2>/dev/null")
        if rc == 0 and out:
            found[f"3.{minor}"] = out.replace("Python ", "").strip()
    rc, out, _ = _run("python3 --version 2>/dev/null")
    if rc == 0 and out:
        found["default"] = out.replace("Python ", "").strip()
    return found


def best_compatible_python(py_versions: dict) -> str | None:
    for minor in (12, 11, 10):
        if f"3.{minor}" in py_versions:
            return f"3.{minor}"
    return None


def detect_docker() -> dict:
    rc, out, _ = _run("docker --version 2>/dev/null")
    if rc != 0:
        return {"status": MISSING, "version": None}
    import re
    m = re.search(r"(\d+)\.(\d+)", out)
    if m:
        tup = (int(m.group(1)), int(m.group(2)))
        if tup < MIN_DOCKER_VERSION:
            return {"status": INCOMPATIBLE, "version": out, "tuple": tup}
    return {"status": OK, "version": out}


def detect_compose_v2() -> dict:
    rc, out, _ = _run("docker compose version 2>/dev/null")
    if rc != 0:
        return {"status": MISSING, "version": None}
    return {"status": OK, "version": out}


def detect_compose_v1() -> dict:
    rc, out, _ = _run("which docker-compose 2>/dev/null")
    if rc != 0:
        return {"present": False}
    return {"present": True, "path": out}


def detect_git() -> dict:
    rc, out, _ = _run("git --version 2>/dev/null")
    if rc != 0:
        return {"status": MISSING}
    return {"status": OK, "version": out}


def detect_gcc(arch: str) -> dict:
    for binary in ("gcc", f"{platform.machine()}-linux-gnu-gcc"):
        rc, _, _ = _run(f"{binary} --version 2>/dev/null")
        if rc == 0:
            return {"status": OK, "binary": binary}
    return {"status": MISSING}


def detect_curl() -> dict:
    rc, _, _ = _run("curl --version 2>/dev/null")
    return {"status": OK if rc == 0 else MISSING}


def detect_qemu(arch: str) -> dict:
    """On non-x86_64 hosts, check if QEMU binfmt support is configured for AMD64 images."""
    if arch == "amd64":
        return {"needed": False}
    rc, _, _ = _run("ls /proc/sys/fs/binfmt_misc/qemu-x86_64 2>/dev/null")
    if rc == 0:
        return {"needed": True, "status": OK}
    return {"needed": True, "status": MISSING}


def detect_python_packages(py_versions: dict) -> list[str]:
    """Return list of missing python3.12 support packages when 3.12 is present."""
    if "3.12" not in py_versions:
        return []
    missing = []
    for pkg in ("python3.12-venv", "python3.12-dev"):
        rc, out, _ = _run(f"dpkg-query -W -f='${{Status}}' {pkg} 2>/dev/null")
        if "install ok installed" not in out:
            missing.append(pkg)
    return missing


def has_sudo() -> bool:
    rc, _, _ = _run("sudo -n true 2>/dev/null")
    return rc == 0


# ─── Report ───────────────────────────────────────────────────────────────────

def print_report(
    os_info: dict,
    arch: str,
    py_versions: dict,
    docker: dict,
    compose_v2: dict,
    compose_v1: dict,
    git: dict,
    gcc: dict,
    curl: dict,
    missing_py_pkgs: list[str],
    qemu: dict,
) -> list[str]:
    """Print detection table and return the list of actions required."""

    compatible = best_compatible_python(py_versions)

    print("\n" + "=" * 60)
    print("  dARK Deployer — Server Prerequisites Report")
    print("=" * 60)

    print(f"\n  System")
    print(f"    OS           : {os_info['name']}")
    print(f"    Architecture : {arch}")

    print(f"\n  Python")
    default_ver = py_versions.get("default", "")
    if default_ver:
        default_minor = int(default_ver.split(".")[1]) if "." in default_ver else 0
        icon = OK if default_minor in COMPATIBLE_PYTHON else WARNING
        print(f"    {ICON[icon]} python3 (default) : {default_ver}")
    for ver_label in [f"3.{m}" for m in range(9, 15)]:
        if ver_label in py_versions:
            minor = int(ver_label.split(".")[1])
            icon = OK if minor in COMPATIBLE_PYTHON else WARNING
            print(f"    {ICON[icon]} python{ver_label}         : {py_versions[ver_label]}")
    if not compatible:
        print(f"    {ICON[MISSING]} No compatible Python 3.10–3.12 found")
    if missing_py_pkgs:
        for pkg in missing_py_pkgs:
            print(f"    {ICON[MISSING]} {pkg}")

    print(f"\n  Docker")
    print(f"    {ICON[docker['status']]} Docker Engine  : {docker.get('version') or 'not installed'}")
    print(f"    {ICON[compose_v2['status']]} Compose v2     : {compose_v2.get('version') or 'not installed'}")
    if compose_v1["present"]:
        print(f"    {ICON[WARNING]} Compose v1 (legacy) found at {compose_v1['path']}")

    print(f"\n  Tools")
    print(f"    {ICON[git['status']]} git            : {git.get('version') or 'not installed'}")
    print(f"    {ICON[curl['status']]} curl           : {'installed' if curl['status'] == OK else 'not installed'}")
    print(f"    {ICON[gcc['status']]} build-essential: {'installed' if gcc['status'] == OK else 'not installed'}")

    # Build action list
    actions = []
    if qemu["needed"]:
        icon = ICON[qemu["status"]]
        label = "configured" if qemu["status"] == OK else "not configured (needed for AMD64 Docker images)"
        print(f"    {icon} QEMU binfmt (AMD64 emulation): {label}")

    if curl["status"] == MISSING:
        actions.append("install_curl")
    if docker["status"] in (MISSING, INCOMPATIBLE):
        actions.append("install_docker")
    if compose_v2["status"] == MISSING:
        actions.append("install_compose_v2")
    if compose_v1["present"]:
        actions.append("remove_compose_v1")
    if git["status"] == MISSING:
        actions.append("install_git")
    if not compatible:
        actions.append("install_python312")
    elif missing_py_pkgs:
        actions.append("install_python312_extras")
    if gcc["status"] == MISSING:
        actions.append("install_build_essential")
    if qemu["needed"] and qemu["status"] == MISSING:
        actions.append("install_qemu")

    print("\n" + "-" * 60)
    if not actions:
        print("  All prerequisites are satisfied.")
    else:
        print("  Actions required:")
        labels = {
            "install_curl":              "Install curl",
            "install_docker":            "Install Docker Engine (official script)",
            "install_compose_v2":        "Install Docker Compose v2 plugin",
            "remove_compose_v1":         "Remove docker-compose v1 (legacy binary)",
            "install_git":               "Install git",
            "install_python312":         "Install Python 3.12 + venv + dev headers",
            "install_python312_extras":  "Install python3.12-venv and python3.12-dev",
            "install_build_essential":   "Install build-essential (gcc, make, …)",
            "install_qemu":              "Configure QEMU binfmt for AMD64 Docker images on ARM64",
        }
        for a in actions:
            print(f"    * {labels.get(a, a)}")
    print("-" * 60)

    return actions


# ─── Installers ───────────────────────────────────────────────────────────────

def do_install_curl() -> bool:
    print("\n[INSTALL] Installing curl...")
    return _apt_get("curl ca-certificates")


def do_install_docker() -> bool:
    print("\n[INSTALL] Installing Docker Engine via get.docker.com ...")
    rc = _run_live("curl -fsSL https://get.docker.com | sudo sh")
    if rc != 0:
        print("[ERROR] Docker installation failed.")
        return False
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "ubuntu"
    _run_live(f"sudo usermod -aG docker {user}")
    print(f"[INFO] User '{user}' added to 'docker' group. Re-login for it to take effect.")
    return True


def do_install_compose_v2() -> bool:
    print("\n[INSTALL] Installing Docker Compose v2 plugin...")
    return _apt_get("docker-compose-plugin")


def do_remove_compose_v1() -> bool:
    print("\n[REMOVE] Removing docker-compose v1 (legacy)...")
    _run_live("sudo apt-get remove -y docker-compose 2>/dev/null || true")
    _run_live("sudo pip3 uninstall -y docker-compose 2>/dev/null || true")
    _run_live("sudo rm -f /usr/local/bin/docker-compose /usr/bin/docker-compose")
    return True


def do_install_git() -> bool:
    print("\n[INSTALL] Installing git...")
    return _apt_get("git")


def do_install_python312(os_info: dict) -> bool:
    print("\n[INSTALL] Installing Python 3.12...")
    if os_info["id"] not in ("ubuntu", "debian"):
        print(f"[ERROR] Automatic Python 3.12 install is not supported for OS '{os_info['id']}'.")
        print("        Install python3.12 manually and re-run this script.")
        return False

    # Check whether python3.12 is available in the current repos
    rc, _, _ = _run("apt-cache show python3.12 2>/dev/null")
    if rc != 0:
        print("[INFO] python3.12 not found in standard repos — adding deadsnakes PPA...")
        _apt_get("software-properties-common")
        rc2 = _run_live("sudo add-apt-repository -y ppa:deadsnakes/ppa")
        if rc2 != 0:
            print("[ERROR] Could not add deadsnakes PPA.")
            return False
        _run_live("sudo apt-get update -q")

    return _apt_get("python3.12 python3.12-venv python3.12-dev")


def do_install_python312_extras() -> bool:
    print("\n[INSTALL] Installing python3.12-venv and python3.12-dev...")
    return _apt_get("python3.12-venv python3.12-dev")


def do_install_build_essential() -> bool:
    print("\n[INSTALL] Installing build-essential...")
    return _apt_get("build-essential")


def do_install_qemu() -> bool:
    print("\n[INSTALL] Configuring QEMU binfmt for AMD64 Docker images on ARM64...")

    # Install packages — qemu-user-static registers binfmt entries on install
    _apt_get("qemu-user-static binfmt-support")

    # Check if the apt post-install hook already registered it
    rc, _, _ = _run("test -f /proc/sys/fs/binfmt_misc/qemu-x86_64 2>/dev/null")
    if rc == 0:
        print("[OK] QEMU binfmt for AMD64 is active (registered by qemu-user-static).")
        return True

    # Fallback: Docker-based registration. Try without sudo first (user in docker
    # group), then with sudo (works when the group change hasn't taken effect yet).
    print("[INFO] Attempting Docker-based binfmt registration...")
    for docker_cmd in ("docker", "sudo docker"):
        rc = _run_live(
            f"{docker_cmd} run --privileged --rm tonistiigi/binfmt --install amd64"
        )
        if rc == 0:
            break

    # Final check — the Docker run may have registered it even if its exit code was
    # non-zero (image output differences between versions)
    rc_check, _, _ = _run("test -f /proc/sys/fs/binfmt_misc/qemu-x86_64 2>/dev/null")
    if rc_check == 0:
        print("[OK] QEMU binfmt for AMD64 is active.")
        return True

    print("[WARN] QEMU binfmt registration failed. AMD64 Docker images may not run on this host.")
    print("       Fix manually after re-login:")
    print("         sudo docker run --privileged --rm tonistiigi/binfmt --install all")
    return False


# ─── Prompt helpers ───────────────────────────────────────────────────────────

def _ask_confirm(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            answer = input(f"\n{question} {hint}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Enter y or n.")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check and install dARK Deployer server prerequisites."
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Check only — print report without making any changes.",
    )
    args = parser.parse_args()

    os_info  = detect_os()
    arch     = detect_arch()
    py_vers  = detect_python_versions()
    docker   = detect_docker()
    comp_v2  = detect_compose_v2()
    comp_v1  = detect_compose_v1()
    git      = detect_git()
    gcc      = detect_gcc(arch)
    curl     = detect_curl()
    py_pkgs  = detect_python_packages(py_vers)
    qemu     = detect_qemu(arch)

    actions = print_report(
        os_info, arch, py_vers, docker, comp_v2, comp_v1,
        git, gcc, curl, py_pkgs, qemu,
    )

    if not actions:
        print("\nServer is ready. Run:  python3 install.py\n")
        return

    if args.check:
        print("\n[--check] No changes made.\n")
        sys.exit(1)

    if not has_sudo():
        print("\n[ERROR] sudo access is required to install dependencies.")
        print("        Ensure your user has sudo privileges and re-run.\n")
        sys.exit(1)

    if not _ask_confirm(
        "Allow this script to configure this server for dARK Deployer?",
        default=True,
    ):
        print("\n[INFO] Setup cancelled. No changes made.\n")
        return

    print("\n" + "=" * 60)
    print("  Installing dependencies")
    print("=" * 60)

    # Single apt update before any installs
    apt_actions = {
        "install_curl", "install_compose_v2", "install_git",
        "install_python312", "install_python312_extras", "install_build_essential",
        "install_qemu",
    }
    if any(a in actions for a in apt_actions) and os_info["id"] in ("ubuntu", "debian"):
        print("\n[INFO] Updating package index...")
        _run_live("sudo apt-get update -q")

    errors: list[str] = []

    runners = {
        "install_curl":             (do_install_curl,              "curl"),
        "install_docker":           (lambda: do_install_docker(),  "Docker Engine"),
        "install_compose_v2":       (do_install_compose_v2,        "Docker Compose v2"),
        "remove_compose_v1":        (do_remove_compose_v1,         None),
        "install_git":              (do_install_git,               "git"),
        "install_python312":        (lambda: do_install_python312(os_info), "Python 3.12"),
        "install_python312_extras": (do_install_python312_extras,  "python3.12-dev/venv"),
        "install_build_essential":  (do_install_build_essential,   "build-essential"),
        "install_qemu":             (do_install_qemu,              "QEMU binfmt"),
    }

    for action in actions:
        fn, label = runners[action]
        ok = fn()
        if label and not ok:
            errors.append(label)

    print("\n" + "=" * 60)
    if errors:
        print(f"  [WARN] Some installations failed: {', '.join(errors)}")
        print("  Review the output above and fix the errors manually.")
        sys.exit(1)
    else:
        print("  All dependencies installed successfully.")

    # Re-check Docker Compose v2 after install (Docker install includes it)
    if "install_docker" in actions:
        rc, _, _ = _run("docker compose version 2>/dev/null")
        if rc != 0:
            # Docker install script may not include compose plugin — try apt
            print("\n[INFO] Compose v2 not detected after Docker install — installing plugin...")
            _apt_get("docker-compose-plugin")

    print("\n  Next step:")
    print("    python3 install.py")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
