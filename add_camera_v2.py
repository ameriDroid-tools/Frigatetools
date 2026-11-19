#!/usr/bin/env python3
"""
Camera installer for Frigate 0.17.x (offline / LAN only).

Supported profiles (key, name):
    sonoff-s2    Sonoff S2            main /av_stream/ch0, optional sub /av_stream/ch1
    rlc-1224a    Reolink RLC-1224A    main H.265 first, H.264 fallback; sub H.264
    duo-3-poe    Reolink Duo 3 PoE    main H.265 first, H.264 fallback; sub H.264
    rlc-811a     Reolink RLC-811A     main H.264 first, H.265 fallback; sub H.264

Everything this script does stays on the local network: RTSP probes to
the camera, ONVIF WS-Discovery multicast on the LAN, and Docker commands
against the local Frigate container. No internet access is needed.

System dependencies on Ubuntu/Debian:
    sudo apt-get install python3-ruamel.yaml
    (Docker must be installed. Stream probing uses the ffprobe that ships
    inside the Frigate image; host ffprobe is only a fallback.)

Run (interactive wizard, one camera):
    python3 add_camera_v2.py
    python3 add_camera_v2.py --config config.yml --container frigate

List ONVIF devices on the LAN and exit:
    python3 add_camera_v2.py --discover

Batch (add a whole bundle from a manifest; one validation, one backup,
one write, optional single restart):
    python3 add_camera_v2.py --batch bundle.yml --config config.yml
    python3 add_camera_v2.py --batch bundle.yml --yes --restart

The existing configuration directory must be bind-mounted into the
running Frigate container. Single-file configuration mounts are rejected
because atomic file replacement can leave those mounts reading old data.

Camera credentials are stored in the configuration's go2rtc URLs. They
are never printed by this script.
"""

import argparse
import getpass
import io
import ipaddress
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

try:
    from ruamel.yaml import YAML
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString
except ImportError:
    print(
        "Missing dependency: ruamel.yaml\n"
        "Install it with:\n"
        "  sudo apt-get install python3-ruamel.yaml",
        file=sys.stderr,
    )
    sys.exit(1)


TARGET_MINOR = "0.17"

# Main/sub path candidates are tried in order; the first one that probes
# successfully is used. High-resolution Reolinks (12MP/16MP) encode the
# main stream as H.265 by default and their h264Preview_01_main path is
# often missing or downscaled, so H.265 is tried first for those. The
# 8MP RLC-811A usually serves H.264 on h264Preview_01_main, so it is
# tried first with H.265 as the fallback.
PROFILES = {
    "1": {
        "key": "sonoff-s2",
        "name": "Sonoff S2",
        "main": ["/av_stream/ch0"],
        "sub": ["/av_stream/ch1"],
        # Probed but not required; falls back to single-stream.
        "sub_optional": True,
    },
    "2": {
        "key": "rlc-1224a",
        "name": "Reolink RLC-1224A",
        "main": ["/h265Preview_01_main", "/h264Preview_01_main"],
        "sub": ["/h264Preview_01_sub"],
        "sub_optional": False,
    },
    "3": {
        "key": "duo-3-poe",
        "name": "Reolink Duo 3 PoE",
        "main": ["/h265Preview_01_main", "/h264Preview_01_main"],
        "sub": ["/h264Preview_01_sub"],
        "sub_optional": False,
    },
    "4": {
        "key": "rlc-811a",
        "name": "Reolink RLC-811A",
        "main": ["/h264Preview_01_main", "/h265Preview_01_main"],
        "sub": ["/h264Preview_01_sub"],
        "sub_optional": False,
    },
}

NAME_RE = re.compile(r"[a-z][a-z0-9_]*")
HEVC_CODECS = ("hevc", "h265")

# Environment variable used to hand the camera URL to `docker exec`.
URL_ENV = "FRIGATE_WIZARD_PROBE_URL"


# Executed inside the running Frigate container.
# Returns error locations/types, not configuration values.
VALIDATE_CODE = r"""
import json
import sys

MARKER = "__CAMERA_WIZARD__"
TARGET_MINOR = (0, 17)

def report(value):
    print(MARKER + json.dumps(value))

try:
    import yaml
    from frigate.config import FrigateConfig
    from frigate.version import VERSION

    version = str(VERSION)

    try:
        parts = version.split("-")[0].split(".")
        minor = (int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        minor = None

    if minor != TARGET_MINOR:
        report({
            "ok": False,
            "reason": "version",
            "version": version,
        })
        sys.exit(2)

    data = yaml.safe_load(sys.stdin.read())
    FrigateConfig.model_validate(data)

    report({
        "ok": True,
        "version": version,
    })

except Exception as exc:
    errors = []

    if hasattr(exc, "errors"):
        try:
            for error in exc.errors()[:20]:
                errors.append({
                    "location": ".".join(
                        str(part) for part in error.get("loc", [])
                    ),
                    "type": error.get("type", "validation_error"),
                })
        except Exception:
            pass

    report({
        "ok": False,
        "reason": "validation",
        "exception": type(exc).__name__,
        "errors": errors,
    })
    sys.exit(1)
"""


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def ask(prompt, default=None):
    # An empty-string default is not shown as "[]".
    suffix = f" [{default}]" if default not in (None, "") else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value if value else default


def yes_no(prompt, default=False):
    suffix = "Y/n" if default else "y/N"

    while True:
        answer = input(f"{prompt} [{suffix}]: ").strip().lower()

        if not answer:
            return default

        if answer in ("y", "yes"):
            return True

        if answer in ("n", "no"):
            return False

        print("Please enter yes or no.")


def number(prompt, default, minimum=0, maximum=3650):
    while True:
        value = ask(prompt, str(default))

        try:
            result = int(value)

            if minimum <= result <= maximum:
                return result
        except (TypeError, ValueError):
            pass

        print(f"Enter a whole number from {minimum} to {maximum}.")


def select(prompt, choices, default=None):
    while True:
        answer = ask(prompt, default)

        if answer in choices:
            return answer

        print("Choose one of: " + ", ".join(choices))


def mapping(parent, key):
    if parent.get(key) is None:
        parent[key] = {}

    if not isinstance(parent[key], MutableMapping):
        raise RuntimeError(
            f"Configuration section '{key}' must be a mapping."
        )

    return parent[key]


def printable(text, limit=60):
    """Sanitize LAN-supplied text (e.g. ONVIF scopes) before printing."""
    cleaned = "".join(c for c in str(text) if c.isprintable())
    return cleaned[:limit]


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

def _norm(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def resolve_profile(value):
    """Accept a menu number, profile key, or model name."""
    wanted = _norm(value)

    for number_key, profile in PROFILES.items():
        names = (
            number_key,
            profile["key"],
            profile["name"],
            profile["name"].replace("Reolink", ""),
            profile["name"].replace("Sonoff", ""),
        )

        if wanted and wanted in {_norm(n) for n in names}:
            return profile

    raise RuntimeError(
        f"Unknown camera model '{printable(value)}'. Use one of: "
        + ", ".join(p["key"] for p in PROFILES.values())
    )


def path_order(candidates, chosen=None):
    """Chosen path first, then the profile's other candidates."""
    if chosen is None:
        return list(candidates)

    if chosen in candidates:
        return [chosen] + [c for c in candidates if c != chosen]

    return [chosen]


# ---------------------------------------------------------------------------
# Docker / container
# ---------------------------------------------------------------------------

def inspect_container(container, config_path):
    try:
        result = subprocess.run(
            ["docker", "inspect", "--type", "container", container],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Docker container inspection timed out.")

    if result.returncode != 0:
        raise RuntimeError(
            "Cannot inspect the container. "
            "Check its name and your Docker permissions."
        )

    try:
        info = json.loads(result.stdout)[0]
    except (ValueError, IndexError, TypeError, KeyError):
        raise RuntimeError("Docker returned invalid container information.")

    if not info.get("State", {}).get("Running"):
        raise RuntimeError(
            "The Frigate container must be running "
            "for stream probing and configuration validation."
        )

    matches = []

    for mount in info.get("Mounts") or []:
        if mount.get("Type") != "bind":
            continue

        if not mount.get("Source") or not mount.get("Destination"):
            continue

        source = Path(mount["Source"]).resolve()

        if source == config_path:
            raise RuntimeError(
                "The configuration is mounted as a single file. "
                "Mount its directory instead before using this wizard's "
                "atomic configuration replacement."
            )

        if not source.is_dir():
            continue

        try:
            relative = config_path.relative_to(source)
        except ValueError:
            continue

        destination = (
            Path(mount["Destination"]) / relative
        ).as_posix()

        matches.append((len(source.parts), destination))

    if not matches:
        raise RuntimeError(
            "The selected configuration is not inside a bind-mounted "
            "directory of this container. "
            "Check --config and --container. "
            "Run this wizard on the Docker host."
        )

    matches.sort(reverse=True)
    return matches[0][1]


# ---------------------------------------------------------------------------
# RTSP URLs and probing
# ---------------------------------------------------------------------------

def parse_address(value):
    """Return an IP formatted for a URL host, or raise ValueError."""
    address = ipaddress.ip_address(str(value).strip())

    if address.version == 6:
        # Zone IDs ("%eth0") must be percent-encoded inside URLs.
        return "[" + str(address).replace("%", "%25") + "]"

    return str(address)


def rtsp_url(host, port, username, password, path):
    if not path:
        raise ValueError("An RTSP path is required.")

    if path.lower().startswith(("rtsp://", "rtsps://")):
        raise ValueError(
            "Enter only the path, for example /av_stream/ch0. "
            "The wizard adds the address and credentials."
        )

    if not path.startswith("/") or any(c.isspace() for c in path):
        raise ValueError(
            "RTSP paths must start with '/' and contain no whitespace."
        )

    if "#" in path:
        raise ValueError("Encode '#' in the path as %23.")

    auth = ""

    if username:
        auth = (
            f"{quote(username, safe='')}:"
            f"{quote(password or '', safe='')}@"
        )
    elif password:
        raise ValueError("A password requires a username.")

    return f"rtsp://{auth}{host}:{port}{path}"


def redact_diagnostic(text, url):
    """Remove the camera URL and credentials from ffprobe output."""
    diagnostic = (text or "").strip()
    parsed = urlsplit(url)

    diagnostic = diagnostic.replace(url, "<camera URL>")

    secrets = [
        parsed.password,
        unquote(parsed.password or ""),
        parsed.username,
        unquote(parsed.username or ""),
    ]

    for secret in sorted(
        {value for value in secrets if value},
        key=len,
        reverse=True,
    ):
        diagnostic = diagnostic.replace(secret, "<redacted>")

    diagnostic = re.sub(
        r"""rtsps?://[^\s'"]+""",
        "<camera URL>",
        diagnostic,
        flags=re.IGNORECASE,
    )

    return "\n".join(diagnostic.splitlines()[:8])


FFPROBE_ARGS = [
    "-hide_banner",
    "-v", "error",
    "-rtsp_transport", "tcp",
    "-show_entries",
    "stream=codec_type,codec_name,width,height",
    "-of", "json",
]

# Shell wrapper run inside the container. The URL is read from an
# environment variable instead of the `docker exec` argv; see Prober.
# $0 is the ffprobe path, remaining positional args are ffprobe options.
CONTAINER_PROBE_SH = (
    'p="$0"; '
    'if command -v timeout >/dev/null 2>&1; then '
    'exec timeout 18 "$p" "$@" "$' + URL_ENV + '"; '
    'else exec "$p" "$@" "$' + URL_ENV + '"; fi'
)

CONTAINER_FIND_SH = (
    'command -v ffprobe 2>/dev/null; '
    'for f in /usr/lib/ffmpeg/*/bin/ffprobe; do '
    '[ -x "$f" ] && echo "$f"; done; true'
)


def _ffmpeg_major(version_output):
    match = re.search(r"version\s+n?(\d+)\.", version_output or "")
    return int(match.group(1)) if match else None


class Prober:
    """
    Runs ffprobe against camera RTSP URLs.

    Preferred: the ffprobe inside the Frigate container (via docker exec).
    This removes the host ffmpeg dependency and tests what actually
    matters: whether the container itself can reach the camera.

    Credential exposure tradeoff (documented on purpose):
    - Passing the URL as a `docker exec` argument would put the password
      in the docker CLI's argv, visible to every local user via `ps`.
      Instead the URL goes into the docker CLI's environment and is
      forwarded with `docker exec -e NAME` (name only, no value on the
      command line). A process's environment is only readable by the
      same user or root (/proc/<pid>/environ).
    - Inside the container, a small `sh -c` wrapper expands the variable
      into ffprobe's argv, because ffprobe cannot read its input URL
      from stdin or a file without changing how the probe behaves.
      Container processes appear in the host's process table, so for the
      few seconds a probe runs the URL is still visible to a host user
      running `ps`. That is the same exposure the original host-side
      ffprobe had, and it is short-lived. On a single-purpose NVR
      appliance this is an acceptable residual risk; on a shared
      multi-user host, run the wizard when untrusted users are not
      logged in.
    - The environment of a `docker exec` process is visible to root
      inside the container and to anyone with Docker access, who can
      already read config.yml (which stores these credentials anyway).
    Diagnostics are always redacted before printing.
    """

    def __init__(self, container):
        self.container = container
        self.mode = None  # "container" or "host"
        self.ffprobe = None
        self.major = None

    def setup(self):
        try:
            result = subprocess.run(
                ["docker", "exec", self.container,
                 "sh", "-c", CONTAINER_FIND_SH],
                capture_output=True,
                text=True,
                timeout=20,
            )
            candidates = [
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip().startswith("/")
            ] if result.returncode == 0 else []
        except (subprocess.TimeoutExpired, OSError):
            candidates = []

        # Order: ffprobe on PATH first, then the highest versioned
        # /usr/lib/ffmpeg/<ver>/bin/ffprobe (Frigate's bundled builds).
        on_path = [
            c for c in candidates if not c.startswith("/usr/lib/ffmpeg/")
        ]

        def version_key(path):
            match = re.search(r"/usr/lib/ffmpeg/([^/]+)/", path)
            ver = match.group(1) if match else ""
            return [int(x) if x.isdigit() else 0 for x in ver.split(".")]

        bundled = sorted(
            (c for c in candidates if c.startswith("/usr/lib/ffmpeg/")),
            key=version_key,
            reverse=True,
        )

        for path in list(dict.fromkeys(on_path + bundled)):
            try:
                check = subprocess.run(
                    ["docker", "exec", self.container, path, "-version"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            except (subprocess.TimeoutExpired, OSError):
                continue

            if check.returncode == 0:
                self.mode = "container"
                self.ffprobe = path
                self.major = _ffmpeg_major(check.stdout)
                print(f"Stream probes run inside the container ({path}).")
                return

        host = shutil.which("ffprobe")

        if host:
            self.mode = "host"
            self.ffprobe = host
            print(
                "\nWARNING: no usable ffprobe was found inside the "
                f"'{self.container}' container. Falling back to the "
                "host's ffprobe. A successful host probe does NOT prove "
                "the container can reach the camera (Docker networking "
                "may differ), and the camera URL is visible in the host "
                "process list while each probe runs."
            )
            return

        raise RuntimeError(
            "No ffprobe available: none found inside the Frigate "
            "container and none installed on the host. Install ffmpeg "
            "on the host (sudo apt-get install ffmpeg) or check the "
            "container image."
        )

    def probe(self, url):
        """Probe an RTSP stream over TCP and return video/audio details."""
        env = None

        if self.mode == "container":
            args = list(FFPROBE_ARGS)

            # FFmpeg 5+ (Frigate ships 7.x) treats -timeout as the RTSP
            # socket I/O timeout in microseconds. On FFmpeg 4 it meant
            # "listen", so it is only added when the version is known.
            if self.major and self.major >= 5:
                args = ["-timeout", "10000000"] + args

            command = [
                "docker", "exec",
                "-e", URL_ENV,  # value comes from env, not argv
                self.container,
                "sh", "-c", CONTAINER_PROBE_SH,
                self.ffprobe,
            ] + args
            env = dict(os.environ)
            env[URL_ENV] = url
        else:
            command = [self.ffprobe] + FFPROBE_ARGS + [url]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=25,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return None, "ffprobe timed out"
        except OSError:
            return None, "could not start ffprobe"

        if result.returncode == 124 and self.mode == "container":
            return None, "ffprobe timed out after 18 seconds"

        if result.returncode != 0:
            diagnostic = redact_diagnostic(result.stderr, url)

            return None, (
                diagnostic
                or f"ffprobe exited with status {result.returncode}"
            )

        try:
            streams = json.loads(result.stdout).get("streams", [])

            video = next(
                s
                for s in streams
                if s.get("codec_type") == "video"
                and (s.get("width") or 0) > 0
                and (s.get("height") or 0) > 0
            )

            audio = [
                s.get("codec_name", "unknown")
                for s in streams
                if s.get("codec_type") == "audio"
            ]

            return {
                "width": int(video["width"]),
                "height": int(video["height"]),
                "codec": video.get("codec_name", "unknown"),
                "audio": audio,
            }, None

        except (ValueError, KeyError, StopIteration, TypeError,
                AttributeError):
            diagnostic = redact_diagnostic(result.stderr, url)
            message = "no usable video stream was found"

            if diagnostic:
                message += "\n" + diagnostic

            return None, message


def describe(details):
    audio = ", ".join(details["audio"]) or "none"
    return (
        f"{details['codec']}, {details['width']}x{details['height']}, "
        f"audio: {audio}"
    )


def probe_paths(prober, host, port, username, password, paths, label):
    """
    Try each RTSP path in order; first success wins.
    Returns (path, url, details, failures).
    """
    failures = []

    for index, path in enumerate(paths):
        url = rtsp_url(host, port, username, password, path)

        note = " (fallback)" if index else ""
        print(f"  Testing {label} stream {path}{note} over RTSP/TCP...")
        details, error = prober.probe(url)

        if details:
            print(f"    OK: {describe(details)}")
            return path, url, details, failures

        failures.append((path, error))
        print("    Failed:")

        for line in error.splitlines():
            print(f"      {line}")

    return None, None, None, failures


# ---------------------------------------------------------------------------
# ONVIF WS-Discovery (stdlib only, LAN multicast, no auth)
# ---------------------------------------------------------------------------

WSD_ADDR = ("239.255.255.250", 3702)

WSD_PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl"
 xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
<e:Header>
<w:MessageID>uuid:{msg_id}</w:MessageID>
<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
<w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
</e:Header>
<e:Body><d:Probe><d:Types>{types}</d:Types></d:Probe></e:Body>
</e:Envelope>"""


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_probe_match(payload, sender_ip):
    """Return a list of {ip, xaddrs, name, hardware} from one response."""
    found = []

    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return found

    for match in root.iter():
        if _local(match.tag) != "ProbeMatch":
            continue

        xaddrs, scopes = "", ""

        for child in match:
            if _local(child.tag) == "XAddrs":
                xaddrs = child.text or ""
            elif _local(child.tag) == "Scopes":
                scopes = child.text or ""

        ips = []

        for xaddr in xaddrs.split():
            try:
                host = urlsplit(xaddr).hostname
                address = ipaddress.ip_address(host)
            except (ValueError, TypeError):
                continue

            if address.version == 4:
                ips.append(str(address))

        name = hardware = ""

        for scope in scopes.split():
            lowered = scope.lower()
            if "onvif.org/name/" in lowered:
                name = unquote(scope.split("/name/", 1)[-1])
            elif "onvif.org/hardware/" in lowered:
                hardware = unquote(scope.split("/hardware/", 1)[-1])

        found.append({
            "ip": ips[0] if ips else sender_ip,
            "xaddrs": xaddrs.split()[:3],
            "name": printable(name, 40),
            "hardware": printable(hardware, 40),
        })

    return found


def ws_discover(timeout=3.0, interface=None):
    """Multicast an ONVIF Probe and collect ProbeMatch replies."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    devices = {}

    try:
        # TTL 2: enough for the local segment, not meant to be routed.
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

        if interface:
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_MULTICAST_IF,
                socket.inet_aton(interface),
            )
            sock.bind((interface, 0))
        else:
            sock.bind(("", 0))

        for types in ("dn:NetworkVideoTransmitter", "tds:Device"):
            message = WSD_PROBE.format(
                msg_id=uuid.uuid4(), types=types
            ).encode("utf-8")

            # UDP is lossy; send each probe twice.
            for _ in range(2):
                sock.sendto(message, WSD_ADDR)

        deadline = time.monotonic() + timeout

        while len(devices) < 256:
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                break

            sock.settimeout(remaining)

            try:
                payload, sender = sock.recvfrom(65535)
            except (socket.timeout, OSError):
                break

            for device in parse_probe_match(payload, sender[0]):
                existing = devices.get(device["ip"])

                if not existing or (
                    not existing["name"] and device["name"]
                ):
                    devices[device["ip"]] = device
    finally:
        sock.close()

    return sorted(
        devices.values(),
        key=lambda d: ipaddress.ip_address(d["ip"]),
    )


def print_devices(devices):
    if not devices:
        print(
            "No ONVIF devices answered. Check that ONVIF is enabled on "
            "the camera, that it is on the same network segment, and "
            "that a firewall is not blocking UDP 3702 / multicast. "
            "Use --discover-interface <host IP> on multi-NIC hosts."
        )
        return

    print(f"Found {len(devices)} ONVIF device(s):")

    for index, device in enumerate(devices, 1):
        label = " ".join(
            part for part in (device["hardware"], device["name"]) if part
        ) or "unknown model"
        print(f"  {index:2}. {device['ip']:15}  {label}")


def run_discovery(timeout, interface):
    print(f"Searching the LAN for ONVIF cameras ({timeout:g}s)...")

    try:
        return ws_discover(timeout=timeout, interface=interface)
    except OSError as exc:
        print(f"  Discovery failed: {type(exc).__name__}")
        return []


# ---------------------------------------------------------------------------
# Shared camera model (used by both interactive and batch mode)
# ---------------------------------------------------------------------------

@dataclass
class CameraOptions:
    retention: dict
    event_mode: str = "motion"
    detection_enabled: bool = True
    fps: int = 5
    objects: list = None
    audio_enabled: bool = False
    live_transcode: bool = False
    transcode_hw: str = "auto"


@dataclass
class CameraPlan:
    name: str
    profile: dict
    host: str
    port: int
    main_path: str
    main_url: str
    main_details: dict
    sub_path: str = None
    sub_url: str = None
    sub_details: dict = None
    options: CameraOptions = None
    notes: list = field(default_factory=list)


def reserved_streams(name):
    """go2rtc stream names a camera may create."""
    return [name, f"{name}_sub", f"{name}_h264"]


def check_name_free(name, cameras, streams, pending=()):
    if not NAME_RE.fullmatch(name or ""):
        raise RuntimeError(
            f"Invalid camera name '{printable(name)}'. Use lowercase "
            "letters, numbers, and underscores; start with a letter."
        )

    if name in cameras:
        raise RuntimeError(
            f"Camera '{name}' already exists. "
            "This add-only wizard will not overwrite it."
        )

    for stream_name in reserved_streams(name):
        if stream_name in streams or stream_name in pending:
            raise RuntimeError(
                f"go2rtc stream '{stream_name}' already exists "
                "or is reserved by another new camera. "
                "Choose a different camera name."
            )


def build_record(days, event_mode):
    return {
        "enabled": any(days.values()),
        "continuous": {"days": days["continuous"]},
        "motion": {"days": days["motion"]},
        "alerts": {
            "retain": {"days": days["alerts"], "mode": event_mode},
        },
        "detections": {
            "retain": {"days": days["detections"], "mode": event_mode},
        },
    }


def is_hevc(details):
    return (details or {}).get("codec") in HEVC_CODECS


def transcode_source(name, hw, with_audio):
    """
    go2rtc ffmpeg source that re-encodes the camera's native stream to
    H.264 for browsers without H.265 support.

    "ffmpeg:<stream>#video=h264" takes another go2rtc stream *by name*
    as input, so go2rtc keeps a single RTSP connection to the camera.
    "#hardware" asks go2rtc to auto-pick a hardware encoder (falling back
    to software if none works); "#hardware=<engine>" forces one, e.g.
    vaapi, v4l2m2m, cuda. Browsers need Opus for WebRTC audio, so audio
    is transcoded to Opus when the camera has an audio track. go2rtc only
    runs the transcode while someone is watching this stream.
    """
    source = f"ffmpeg:{name}#video=h264"

    if with_audio:
        source += "#audio=opus"

    if hw and hw != "none":
        source += "#hardware" if hw == "auto" else f"#hardware={hw}"

    return DoubleQuotedScalarString(source)


def build_camera(plan):
    """Return (camera_config, new_go2rtc_streams) for a probed camera."""
    opts = plan.options
    name = plan.name
    new_streams = {}

    # The native main stream uses the camera name so go2rtc stream
    # naming matches the Frigate camera name. It never contains a
    # transcode source, so Frigate's record input always receives the
    # camera's original bitstream.
    new_streams[name] = [plan.main_url]

    if plan.sub_url:
        new_streams[f"{name}_sub"] = [plan.sub_url]

    live = None

    if opts.live_transcode and is_hevc(plan.main_details):
        # Why a separate stream instead of appending the ffmpeg source to
        # streams[name]: go2rtc merges all sources of one stream and picks
        # tracks per consumer. Keeping the transcode in its own stream
        # ("<name>_h264") guarantees Frigate's record input
        # (rtsp://127.0.0.1:8554/<name>) never receives the re-encoded
        # H.264 and never starts an encoder. Live view is pointed at the
        # H.264 stream via cameras.<name>.live.streams (first = default).
        live_name = f"{name}_h264"
        new_streams[live_name] = [
            transcode_source(
                name,
                opts.transcode_hw,
                bool(plan.main_details["audio"]),
            )
        ]
        live = {"Main H.264 (transcoded)": live_name}

        if plan.sub_url:
            live["Sub stream"] = f"{name}_sub"

        live["Main native H.265"] = name

    local_main = f"rtsp://127.0.0.1:8554/{name}"

    if plan.sub_url:
        inputs = [
            {
                "path": local_main,
                "input_args": "preset-rtsp-restream",
                "roles": ["record"],
            },
            {
                "path": f"rtsp://127.0.0.1:8554/{name}_sub",
                "input_args": "preset-rtsp-restream",
                "roles": ["detect"],
            },
        ]
        detect_details = plan.sub_details
    else:
        # Single stream serves both roles (Sonoff S2 without ch1).
        inputs = [
            {
                "path": local_main,
                "input_args": "preset-rtsp-restream",
                "roles": ["detect", "record"],
            },
        ]
        detect_details = plan.main_details

    camera = {
        "enabled": True,
        "ffmpeg": {
            "inputs": inputs,
            "output_args": {
                "record": (
                    "preset-record-generic-audio-aac"
                    if opts.audio_enabled
                    else "preset-record-generic"
                ),
            },
        },
        "detect": {
            "enabled": opts.detection_enabled,
            "width": detect_details["width"],
            "height": detect_details["height"],
            "fps": opts.fps,
        },
        "record": build_record(opts.retention, opts.event_mode),
    }

    if live:
        camera["live"] = {"streams": live}

    if opts.objects is not None:
        camera["objects"] = {"track": list(opts.objects)}

    return camera, new_streams


def add_plan(plan, cameras, streams):
    camera, new_streams = build_camera(plan)

    for stream_name in new_streams:
        if stream_name in streams:
            raise RuntimeError(
                f"go2rtc stream '{stream_name}' already exists."
            )

    for stream_name, sources in new_streams.items():
        streams[stream_name] = sources

    cameras[plan.name] = camera
    return camera, new_streams


def print_plan_summary(plan, new_streams):
    opts = plan.options
    detect = plan.sub_details if plan.sub_url else plan.main_details

    print(f"Camera:       {plan.name}")
    print(f"Model:        {plan.profile['name']}")
    print(f"Address:      {plan.host}:{plan.port}")
    print(
        "Streams:      "
        + (
            "main + sub"
            if plan.sub_url
            else "single stream shared by detect and record"
        )
    )
    print(
        f"Main video:   {plan.main_details['codec']} "
        f"{plan.main_details['width']}x{plan.main_details['height']} "
        f"({plan.main_path})"
    )
    print(
        f"Detect video: {detect['width']}x{detect['height']}"
        + (f" ({plan.sub_path})" if plan.sub_url else "")
    )
    print(
        "Live view:    "
        + (
            f"H.264 transcode via go2rtc stream '{plan.name}_h264'"
            if f"{plan.name}_h264" in new_streams
            else "native stream"
        )
    )
    print(
        "Detection:    "
        + (
            f"enabled, {opts.fps} fps"
            if opts.detection_enabled
            else "disabled"
        )
    )
    print(
        "Objects:      "
        + (", ".join(opts.objects) if opts.objects else
           "not changed/inherited")
    )
    record_enabled = any(opts.retention.values())
    print("Recording:    " + ("enabled" if record_enabled else "disabled"))

    for category, days in opts.retention.items():
        print(f"  {category:12} {days} days")

    if opts.retention["alerts"] or opts.retention["detections"]:
        print(f"Review segments retained: {opts.event_mode}")

    print("Record audio: " + ("AAC" if opts.audio_enabled else "disabled"))

    for note in plan.notes:
        print(f"Note:         {note}")


def dump_yaml(yaml, config):
    output = io.StringIO()
    yaml.dump(config, output)
    return output.getvalue()


# ---------------------------------------------------------------------------
# Validation and saving
# ---------------------------------------------------------------------------

def validate_in_frigate(container, proposed_yaml):
    print(
        "\nValidating with the installed Frigate "
        "configuration model..."
    )

    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                container,
                "python3",
                "-c",
                VALIDATE_CODE,
            ],
            input=proposed_yaml,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "Frigate configuration validation timed out. "
            "Nothing was saved."
        )

    marker = "__CAMERA_WIZARD__"

    reports = [
        line[len(marker):]
        for line in result.stdout.splitlines()
        if line.startswith(marker)
    ]

    if not reports:
        raise RuntimeError(
            "Could not run the container's configuration validator. "
            "Nothing was saved. Raw output is hidden "
            "to protect credentials."
        )

    try:
        report = json.loads(reports[-1])
    except ValueError:
        raise RuntimeError(
            "The container validator returned an invalid response. "
            "Nothing was saved."
        )

    if not report.get("ok") or result.returncode != 0:
        if report.get("reason") == "version":
            raise RuntimeError(
                f"Installed Frigate version is {report.get('version')}; "
                f"this script supports Frigate {TARGET_MINOR}.x only "
                "(configuration keys differ between minor versions). "
                "Nothing was saved."
            )

        for error in report.get("errors", []):
            location = error.get("location") or "<root>"
            print(f"  {location}: {error.get('type')}")

        exception = report.get("exception", "unknown error")

        raise RuntimeError(
            "Frigate configuration validation failed "
            f"({exception}). Nothing was saved."
        )

    print(f"  Valid for Frigate {report.get('version')}.")


def backup_config(path, contents):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = path.with_name(f"{path.name}.bak_{stamp}")

    fd = os.open(
        backup,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )

    with os.fdopen(fd, "wb") as handle:
        handle.write(contents)
        handle.flush()
        os.fsync(handle.fileno())

    return backup


def atomic_write(path, contents, original_stat):
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )

    try:
        with os.fdopen(fd, "wb") as handle:
            current = os.fstat(handle.fileno())

            if (
                current.st_uid != original_stat.st_uid
                or current.st_gid != original_stat.st_gid
            ):
                os.fchown(
                    handle.fileno(),
                    original_stat.st_uid,
                    original_stat.st_gid,
                )

            os.fchmod(
                handle.fileno(),
                stat.S_IMODE(original_stat.st_mode),
            )

            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary, path)

        # Persist the rename itself (directory entry) across power loss.
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_config(config_path, original, original_stat, proposed_yaml):
    if config_path.read_bytes() != original:
        raise RuntimeError(
            "The configuration changed while the wizard was running. "
            "Nothing was saved. Rerun the wizard."
        )

    backup = backup_config(config_path, original)
    print(f"\nBackup created: {backup}")

    try:
        atomic_write(
            config_path,
            proposed_yaml.encode("utf-8"),
            original_stat,
        )
    except OSError:
        raise RuntimeError(
            "Could not replace the configuration file. "
            "Check directory permissions and file ownership. "
            f"The backup is available at: {backup}"
        )

    print(f"Saved: {config_path}")

    if stat.S_IMODE(original_stat.st_mode) & 0o077:
        print(
            "\nSecurity note: existing config permissions allow "
            "group or other access. This file contains credentials. "
            "Restrict access as appropriate for your deployment."
        )

    return backup


def restart_container(container, backup):
    try:
        result = subprocess.run(
            ["docker", "restart", container],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print(
            "Restart command timed out. "
            "Its final state is unknown. "
            "Check the container before taking further action."
        )
        print(f"Backup remains available: {backup}")
        return

    if result.returncode != 0:
        print(
            "Docker reported a restart failure. "
            "Check the container status."
        )
        print("The saved configuration remains in place.")
        print(f"Backup remains available: {backup}")
        return

    print(
        "\nDocker accepted the restart. "
        "Check Frigate's UI and logs to confirm "
        "recording, detection, and live playback."
    )
    print(
        "Stream probes and schema validation do not verify "
        "runtime decoding, hardware acceleration, or detector load."
    )


def print_general_notes(plans):
    if any(
        is_hevc(p.main_details) and not p.options.live_transcode
        for p in plans
    ):
        print(
            "\nAt least one main stream is H.265 without a live "
            "transcode. Recording uses it natively, but live playback "
            "depends on browser/device H.265 support."
        )

    if any(
        p.options.live_transcode and is_hevc(p.main_details)
        for p in plans
    ):
        print(
            "\nH.264 live transcodes run in go2rtc only while someone "
            "watches; each viewer session costs encode CPU/GPU. "
            "Recording and detection use the native streams."
        )

    for plan in plans:
        if (
            plan.options.detection_enabled
            and not plan.sub_url
            and plan.main_details["width"] >= 1920
        ):
            print(
                f"\n'{plan.name}' is single-stream: detection decodes "
                "its full-resolution stream."
            )

    print(
        "\nExisting detector hardware, hardware acceleration, "
        "review rules, and unrelated cameras are not changed."
    )
    print(
        "Existing review rules determine which tracked objects "
        "produce alerts versus detections."
    )
    print(
        "Recording retention categories can overlap; footage "
        "may be retained by more than one category."
    )
    print(
        "Disabling recording audio does not remove audio "
        "from the go2rtc live source."
    )


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(config_arg):
    config_path = Path(config_arg).expanduser().resolve()

    if not config_path.is_file():
        raise RuntimeError(
            f"Existing configuration not found: {config_path}"
        )

    original = config_path.read_bytes()
    original_stat = config_path.stat()

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.allow_duplicate_keys = False

    text = original.decode("utf-8")

    # ruamel emits one sequence style for the whole file. Match the
    # existing style ("key:\n  - item", as in Frigate's docs) when the
    # file uses it, so saving does not reformat every list.
    if re.search(r"^( *)[^\s#-][^\n]*:[ \t]*\n\1  +- ", text, re.M):
        yaml.indent(mapping=2, sequence=4, offset=2)

    config = yaml.load(text)

    if config is None:
        raise RuntimeError("The configuration file is empty.")

    if not isinstance(config, MutableMapping):
        raise RuntimeError("The configuration must be a YAML mapping.")

    return config_path, original, original_stat, yaml, config


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------

def camera_address(discovered=None):
    if discovered:
        print_devices(discovered)

        while True:
            choice = ask(
                "Pick a device number, or type an IP address", ""
            )

            if choice and choice.isdigit() and (
                1 <= int(choice) <= len(discovered)
            ):
                return parse_address(discovered[int(choice) - 1]["ip"])

            try:
                return parse_address(choice)
            except ValueError:
                print("Enter a listed number or an IPv4/IPv6 address.")

    while True:
        value = ask("Camera IP address", "")

        try:
            return parse_address(value)
        except ValueError:
            print("Enter an IPv4 or IPv6 address without a port.")


def tested_stream(prober, label, candidates, host, port, username,
                  password):
    """Prompt for a path (default = profile's first candidate) and probe
    it, automatically trying the profile's alternate paths."""
    while True:
        print(
            "\nEnter only the RTSP path. "
            "Do not enter the full rtsp:// URL."
        )
        path = ask(f"{label} RTSP path", candidates[0])

        try:
            rtsp_url(host, port, username, password, path)
        except ValueError as exc:
            print(exc)
            continue

        found_path, url, details, _ = probe_paths(
            prober, host, port, username, password,
            path_order(candidates, path), label.lower(),
        )

        if details:
            return found_path, url, details

        print(
            "\nCheck the camera credentials, RTSP port, and path."
            "\nClose VLC or other viewers in case the camera limits "
            "simultaneous connections."
            "\nFor Reolink, verify RTSP is enabled in camera settings."
            "\nThis test uses TCP; VLC may use a different transport."
        )

        if not yes_no(
            "Retry the stream test? You can correct the path.",
            default=True,
        ):
            raise RuntimeError(
                "Cancelled: an unverified stream will not be saved. "
                "If credentials or the IP are wrong, rerun the wizard."
            )


def usable_optional_sub(sub_details, main_details):
    """An optional sub stream only helps if it is smaller than main."""
    return bool(sub_details) and (
        sub_details["width"] * sub_details["height"]
        < main_details["width"] * main_details["height"]
    )


def optional_sub(prober, profile, host, port, username, password,
                 main_details):
    """Probe an optional sub stream; return (path, url, details) or Nones."""
    print("\nChecking for an optional sub stream for detection...")
    path, url, details, _ = probe_paths(
        prober, host, port, username, password,
        profile["sub"], "optional sub",
    )

    if usable_optional_sub(details, main_details):
        print("  Using it for detection.")
        return path, url, details

    if details:
        print(
            "  It is not lower resolution than the main stream; "
            "using single-stream mode."
        )
    else:
        print("  Not available; using single-stream mode.")

    return None, None, None


def recording_options():
    print("\nRecording policy")
    print("  0. Disabled")
    print("  1. Continuous recording")
    print("  2. Motion-only recording")
    print("  3. Review alerts/detections only")
    print("  4. Custom combination")

    choice = select("Choose", ("0", "1", "2", "3", "4"), "2")

    days = {"continuous": 0, "motion": 0, "alerts": 0, "detections": 0}

    if choice == "1":
        days["continuous"] = number("Continuous retention days", 7)

    elif choice == "2":
        days["motion"] = number("Motion retention days", 7)

    elif choice == "3":
        days["alerts"] = number("Alert retention days", 14)
        days["detections"] = number("Detection retention days", 7)

    elif choice == "4":
        print("Use 0 to disable retention for a category.")
        days["continuous"] = number("Continuous retention days", 0)
        days["motion"] = number("Motion retention days", 7)
        days["alerts"] = number("Alert retention days", 14)
        days["detections"] = number("Detection retention days", 7)

    event_mode = "motion"

    if days["alerts"] or days["detections"]:
        print("\nReview-event recording retention:")
        print("  1. Keep motion segments within review events")
        print("  2. Keep all segments within review events")

        mode = select("Choose", ("1", "2"), "1")
        event_mode = "motion" if mode == "1" else "all"

    return days, event_mode


def interactive(args):
    if not shutil.which("docker"):
        raise RuntimeError("Required executable missing: docker")

    config_path, original, original_stat, yaml, config = load_config(
        args.config
    )

    cameras = mapping(config, "cameras")
    go2rtc = mapping(config, "go2rtc")
    streams = mapping(go2rtc, "streams")

    print("==========================================")
    print(f" Frigate {TARGET_MINOR}.x Camera Installer")
    print("==========================================")

    container = args.container or ask("Frigate container name", "frigate")
    container_path = inspect_container(container, config_path)

    print(f"\nHost configuration:      {config_path}")
    print(f"Container configuration: {container_path}")
    print(f"Container:               {container}")

    if not yes_no(
        "Is this the configuration file Frigate actually uses?",
        default=False,
    ):
        print("Cancelled. No files changed.")
        return

    print("\nSelect camera model:")

    for key, profile in PROFILES.items():
        print(f"  {key}. {profile['name']}")

    profile = PROFILES[select("Choose", tuple(PROFILES))]

    while True:
        name = ask("Camera name, e.g. driveway", "")

        if NAME_RE.fullmatch(name or ""):
            break

        print(
            "Use lowercase letters, numbers, and underscores. "
            "Start with a letter."
        )

    check_name_free(name, cameras, streams)

    discovered = None

    if yes_no(
        "\nSearch the LAN for ONVIF cameras to pick the IP from a list?",
        default=True,
    ):
        discovered = run_discovery(
            args.discover_timeout, args.discover_interface
        )

        if not discovered:
            print_devices([])
            print("Enter the IP manually.")

    host = camera_address(discovered)

    port = number("RTSP port", 554, minimum=1, maximum=65535)

    username = ask("Camera username; blank for no authentication", "")
    password = getpass.getpass("Camera password: ") if username else ""

    prober = Prober(container)
    prober.setup()

    main_path, main_url, main_details = tested_stream(
        prober, "Main", profile["main"], host, port, username, password,
    )

    sub_path = sub_url = sub_details = None

    if profile["sub_optional"]:
        sub_path, sub_url, sub_details = optional_sub(
            prober, profile, host, port, username, password, main_details,
        )
    elif profile["sub"]:
        sub_path, sub_url, sub_details = tested_stream(
            prober, "Sub", profile["sub"], host, port, username, password,
        )

    retention, event_mode = recording_options()
    review_recording = bool(retention["alerts"] or retention["detections"])

    if review_recording:
        print("\nObject detection is enabled for review-event recording.")
        detection_enabled = True
    else:
        detection_enabled = yes_no("\nEnable object detection?", default=True)

    fps = 5
    objects = None

    if detection_enabled:
        fps = number("Detection frames per second", 5, minimum=1, maximum=30)

        print(
            "Object labels must be supported by "
            "your configured detector/model."
        )

        while True:
            labels = ask("Objects to track, comma-separated", "person,car")
            objects = list(dict.fromkeys(
                label.strip() for label in labels.split(",") if label.strip()
            ))

            if objects:
                break

            print("Enter at least one object label.")

    audio_enabled = False

    if any(retention.values()):
        if main_details["audio"]:
            audio_enabled = yes_no(
                "Include audio in recordings?", default=False
            )
        else:
            print(
                "\nNo audio track was found in the main stream. "
                "Audio recording will be disabled."
            )

    live_transcode = False

    if is_hevc(main_details):
        print(
            "\nThe main stream is H.265. Recording uses it natively, "
            "but many browsers cannot play H.265 live."
        )
        live_transcode = yes_no(
            "Add an H.264 transcoded live-view stream in go2rtc "
            "(recording stays native)?",
            default=True,
        )

    plan = CameraPlan(
        name=name, profile=profile, host=host, port=port,
        main_path=main_path, main_url=main_url, main_details=main_details,
        sub_path=sub_path, sub_url=sub_url, sub_details=sub_details,
        options=CameraOptions(
            retention=retention,
            event_mode=event_mode,
            detection_enabled=detection_enabled,
            fps=fps,
            objects=objects,
            audio_enabled=audio_enabled,
            live_transcode=live_transcode,
            transcode_hw=args.live_transcode_hw,
        ),
    )

    _, new_streams = add_plan(plan, cameras, streams)
    proposed_yaml = dump_yaml(yaml, config)

    print("\n================ SUMMARY ================")
    print_plan_summary(plan, new_streams)
    print(f"Config file:  {config_path}")
    print(f"Container:    {container}")
    print("Credentials:  hidden here; stored in go2rtc source URLs")
    print("=========================================")

    print_general_notes([plan])

    if not yes_no("\nValidate and save this camera?", default=False):
        print("Cancelled. No files changed.")
        return

    validate_in_frigate(container, proposed_yaml)
    backup = save_config(config_path, original, original_stat, proposed_yaml)

    if not yes_no(
        "\nRestart Frigate now? All cameras will briefly disconnect.",
        default=False,
    ):
        print("Configuration saved. Restart Frigate when ready.")
        return

    restart_container(container, backup)


# ---------------------------------------------------------------------------
# Batch mode
# ---------------------------------------------------------------------------

CAMERA_KEYS = {
    "name", "model", "ip", "port", "username", "username_env",
    "password", "password_env", "record", "retention_days",
    "alert_days", "detection_days", "retention", "review_retain_mode",
    "detect", "fps", "objects", "audio", "live_transcode",
    "main_path", "sub_path",
}

RECORD_MODES = {
    "disabled": "disabled", "off": "disabled", "none": "disabled",
    "continuous": "continuous", "motion": "motion",
    "review": "review", "alerts": "review", "custom": "custom",
}


class ManifestError(RuntimeError):
    pass


def _int(entry, key, default, minimum, maximum, where):
    value = entry.get(key, default)

    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(f"{where}: '{key}' must be a whole number.")

    if not minimum <= value <= maximum:
        raise ManifestError(
            f"{where}: '{key}' must be from {minimum} to {maximum}."
        )

    return value


def _bool(entry, key, default, where):
    value = entry.get(key, default)

    if not isinstance(value, bool):
        raise ManifestError(f"{where}: '{key}' must be true or false.")

    return value


def _str(entry, key, where, required=False):
    value = entry.get(key)

    if value is None:
        if required:
            raise ManifestError(f"{where}: '{key}' is required.")
        return None

    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ManifestError(f"{where}: '{key}' must be a string.")

    return str(value)


def _secret(entry, key, where):
    """Read key or key_env. Messages never include the value."""
    env_key = f"{key}_env"

    if entry.get(key) is not None and entry.get(env_key) is not None:
        raise ManifestError(
            f"{where}: use either '{key}' or '{env_key}', not both."
        )

    if entry.get(env_key) is not None:
        var = _str(entry, env_key, where)

        if var not in os.environ:
            raise ManifestError(
                f"{where}: environment variable '{printable(var)}' "
                f"(from {env_key}) is not set."
            )

        return os.environ[var]

    value = entry.get(key)

    if value is None:
        return ""

    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ManifestError(f"{where}: '{key}' must be a string.")

    return str(value)


def batch_retention(entry, where):
    raw_mode = entry.get("record", "motion")

    # YAML 1.1 parsers turn a bare `off`/`no` into False.
    if raw_mode is False or raw_mode is None:
        raw_mode = "disabled"

    mode = RECORD_MODES.get(str(raw_mode).lower())

    if mode is None:
        raise ManifestError(
            f"{where}: 'record' must be one of "
            "disabled, continuous, motion, review, custom."
        )

    days = {"continuous": 0, "motion": 0, "alerts": 0, "detections": 0}

    if mode == "continuous":
        days["continuous"] = _int(entry, "retention_days", 7, 1, 3650, where)
    elif mode == "motion":
        days["motion"] = _int(entry, "retention_days", 7, 1, 3650, where)
    elif mode == "review":
        days["alerts"] = _int(entry, "alert_days", 14, 0, 3650, where)
        days["detections"] = _int(entry, "detection_days", 7, 0, 3650, where)
    elif mode == "custom":
        days.update(continuous=0, motion=7, alerts=14, detections=7)

    overrides = entry.get("retention")

    if overrides is not None:
        if not isinstance(overrides, dict):
            raise ManifestError(f"{where}: 'retention' must be a mapping.")

        for key in overrides:
            if key not in days:
                raise ManifestError(
                    f"{where}: unknown retention category "
                    f"'{printable(key)}'."
                )
            days[key] = _int(overrides, key, 0, 0, 3650, where)

    review_mode = str(entry.get("review_retain_mode", "motion")).lower()

    if review_mode not in ("motion", "all"):
        raise ManifestError(
            f"{where}: 'review_retain_mode' must be motion or all."
        )

    return days, review_mode


def batch_objects(entry, where):
    value = entry.get("objects", ["person", "car"])

    if isinstance(value, str):
        value = value.split(",")

    if not isinstance(value, list) or not all(
        isinstance(v, str) for v in value
    ):
        raise ManifestError(
            f"{where}: 'objects' must be a list of labels."
        )

    objects = list(dict.fromkeys(v.strip() for v in value if v.strip()))

    if not objects:
        raise ManifestError(f"{where}: 'objects' is empty.")

    return objects


def parse_manifest_entry(raw, defaults, index, transcode_hw):
    where = f"cameras[{index}]"

    if not isinstance(raw, dict):
        raise ManifestError(f"{where} must be a mapping.")

    entry = dict(defaults)
    entry.update(raw)

    # Camera-level password/username override env defaults and vice versa.
    for key in ("password", "username"):
        if key in raw and f"{key}_env" not in raw:
            entry.pop(f"{key}_env", None)
        if f"{key}_env" in raw and key not in raw:
            entry.pop(key, None)

    unknown = set(entry) - CAMERA_KEYS

    if unknown:
        raise ManifestError(
            f"{where}: unknown key(s): "
            + ", ".join(sorted(printable(k) for k in unknown))
        )

    name = _str(entry, "name", where, required=True)
    where = f"camera '{printable(name)}'"

    profile = resolve_profile(_str(entry, "model", where, required=True))

    try:
        host = parse_address(_str(entry, "ip", where, required=True))
    except ValueError:
        raise ManifestError(f"{where}: 'ip' must be an IPv4/IPv6 address.")

    port = _int(entry, "port", 554, 1, 65535, where)
    username = _secret(entry, "username", where)
    password = _secret(entry, "password", where)

    if password and not username:
        raise ManifestError(f"{where}: a password requires a username.")

    retention, event_mode = batch_retention(entry, where)
    detect = _bool(entry, "detect", True, where)
    notes = []

    if (retention["alerts"] or retention["detections"]) and not detect:
        detect = True
        notes.append("detection forced on for review-event recording")

    fps = _int(entry, "fps", 5, 1, 30, where)
    objects = batch_objects(entry, where) if detect else None

    main_paths = profile["main"]
    sub_paths = profile["sub"]

    for key in ("main_path", "sub_path"):
        value = _str(entry, key, where)

        if value is not None:
            try:
                rtsp_url(host, port, "", "", value)
            except ValueError as exc:
                raise ManifestError(f"{where}: '{key}': {exc}")

            if key == "main_path":
                main_paths = path_order(profile["main"], value)
            else:
                sub_paths = path_order(profile["sub"], value)

    return {
        "name": name,
        "profile": profile,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "main_paths": main_paths,
        "sub_paths": sub_paths,
        "notes": notes,
        "options": CameraOptions(
            retention=retention,
            event_mode=event_mode,
            detection_enabled=detect,
            fps=fps,
            objects=objects,
            audio_enabled=_bool(entry, "audio", False, where),
            live_transcode=_bool(entry, "live_transcode", True, where),
            transcode_hw=transcode_hw,
        ),
    }


def load_manifest(path_arg):
    path = Path(path_arg).expanduser()

    if not path.is_file():
        raise RuntimeError(f"Manifest not found: {path}")

    loader = YAML(typ="safe")

    try:
        data = loader.load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        # Parser messages can quote file content (passwords).
        raise RuntimeError(
            f"Manifest is not valid YAML ({type(exc).__name__}). "
            "Details suppressed to avoid exposing credentials."
        )

    if not isinstance(data, dict) or not isinstance(
        data.get("cameras"), list
    ) or not data["cameras"]:
        raise RuntimeError(
            "Manifest must be a mapping with a non-empty 'cameras' list."
        )

    defaults = data.get("defaults") or {}

    if not isinstance(defaults, dict):
        raise RuntimeError("Manifest 'defaults' must be a mapping.")

    if "name" in defaults or "ip" in defaults:
        raise RuntimeError("'name' and 'ip' cannot be set in defaults.")

    has_plain_password = "password" in defaults or any(
        isinstance(c, dict) and "password" in c for c in data["cameras"]
    )

    if has_plain_password and stat.S_IMODE(path.stat().st_mode) & 0o077:
        print(
            "Security note: the manifest contains plaintext passwords and "
            "is readable by group/other. Consider chmod 600 or "
            "password_env."
        )

    return data, defaults


def validate_hw(hw):
    if not re.fullmatch(r"[a-z0-9_]+", hw or ""):
        raise RuntimeError(
            "live transcode hardware must be 'auto', 'none', or a go2rtc "
            "engine name such as vaapi, v4l2m2m, cuda."
        )


def probe_entry(prober, entry):
    """Probe one manifest camera. Returns a CameraPlan or None."""
    print(
        f"\n[{entry['name']}] {entry['profile']['name']} "
        f"at {entry['host']}:{entry['port']}"
    )
    creds = (
        entry["host"], entry["port"],
        entry["username"], entry["password"],
    )

    main_path, main_url, main_details, _ = probe_paths(
        prober, *creds, entry["main_paths"], "main"
    )

    if not main_details:
        return None

    sub_path = sub_url = sub_details = None
    optional = entry["profile"]["sub_optional"]

    if entry["sub_paths"]:
        sub_path, sub_url, sub_details, _ = probe_paths(
            prober, *creds, entry["sub_paths"],
            "optional sub" if optional else "sub",
        )

        if optional:
            if not sub_details:
                entry["notes"].append(
                    "optional sub stream unavailable; single-stream mode"
                )
            elif not usable_optional_sub(sub_details, main_details):
                entry["notes"].append(
                    "optional sub stream not lower resolution; "
                    "single-stream mode"
                )
                sub_path = sub_url = sub_details = None
        elif not sub_details:
            return None

    options = entry["options"]

    if options.audio_enabled and (
        not main_details["audio"] or not any(options.retention.values())
    ):
        options.audio_enabled = False
        entry["notes"].append(
            "record audio disabled (no audio track or recording off)"
        )

    if not is_hevc(main_details):
        options.live_transcode = False

    return CameraPlan(
        name=entry["name"], profile=entry["profile"],
        host=entry["host"], port=entry["port"],
        main_path=main_path, main_url=main_url, main_details=main_details,
        sub_path=sub_path, sub_url=sub_url, sub_details=sub_details,
        options=options, notes=entry["notes"],
    )


def batch(args):
    if not shutil.which("docker"):
        raise RuntimeError("Required executable missing: docker")

    data, defaults = load_manifest(args.batch)

    hw = args.live_transcode_hw or str(
        data.get("live_transcode_hardware", "auto")
    )
    validate_hw(hw)

    config_path, original, original_stat, yaml, config = load_config(
        args.config
    )

    cameras = mapping(config, "cameras")
    go2rtc = mapping(config, "go2rtc")
    streams = mapping(go2rtc, "streams")

    # Validate the whole manifest (names, collisions, credentials) before
    # touching the network, so typos fail fast.
    entries = []
    pending = set()

    for index, raw in enumerate(data["cameras"]):
        entry = parse_manifest_entry(raw, defaults, index, hw)
        check_name_free(entry["name"], cameras, streams, pending)
        pending.update(reserved_streams(entry["name"]))
        entries.append(entry)

    container = str(args.container or data.get("container") or "frigate")

    print("==========================================")
    print(f" Frigate {TARGET_MINOR}.x Camera Installer (batch)")
    print("==========================================")

    container_path = inspect_container(container, config_path)
    prober = Prober(container)
    prober.setup()

    plans = []
    failed = []

    for entry in entries:
        plan = probe_entry(prober, entry)

        if plan:
            plans.append(plan)
        else:
            failed.append(entry["name"])

    if failed:
        raise RuntimeError(
            "Stream probe failed for: " + ", ".join(failed)
            + ". Nothing was saved. Fix the manifest or camera and rerun."
        )

    all_new = {}

    for plan in plans:
        _, new_streams = add_plan(plan, cameras, streams)
        all_new[plan.name] = new_streams

    proposed_yaml = dump_yaml(yaml, config)
    validate_in_frigate(container, proposed_yaml)

    print("\n================ SUMMARY ================")

    for plan in plans:
        print_plan_summary(plan, all_new[plan.name])
        print("-----------------------------------------")

    print(f"Cameras to add:          {len(plans)}")
    print(f"Host configuration:      {config_path}")
    print(f"Container configuration: {container_path}")
    print(f"Container:               {container}")
    print("Credentials:             hidden here; stored in go2rtc URLs")
    print("=========================================")

    print_general_notes(plans)

    if not args.yes and not yes_no(
        f"\nWrite these {len(plans)} camera(s) to the configuration "
        "Frigate uses (shown above)?",
        default=False,
    ):
        print("Cancelled. No files changed.")
        return

    backup = save_config(config_path, original, original_stat, proposed_yaml)

    manifest_restart = data.get("restart") is True

    if args.restart:
        do_restart = True
    elif args.yes:
        do_restart = manifest_restart
    else:
        do_restart = yes_no(
            "\nRestart Frigate now? All cameras will briefly disconnect.",
            default=manifest_restart,
        )

    if do_restart:
        restart_container(container, backup)
    else:
        print("Configuration saved. Restart Frigate when ready.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=f"Add cameras to Frigate {TARGET_MINOR}.x (offline)."
    )
    parser.add_argument(
        "--config",
        default="config.yml",
        help="Existing Frigate configuration; default: config.yml",
    )
    parser.add_argument(
        "--container",
        help="Running Frigate container name; prompted if omitted "
             "(batch: manifest 'container', else 'frigate').",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="List ONVIF devices found on the LAN and exit.",
    )
    parser.add_argument(
        "--discover-timeout",
        type=float,
        default=3.0,
        help="Seconds to wait for ONVIF replies; default 3.",
    )
    parser.add_argument(
        "--discover-interface",
        metavar="HOST_IP",
        help="Send discovery from this host IPv4 address (multi-NIC).",
    )
    parser.add_argument(
        "--batch",
        metavar="MANIFEST",
        help="Add all cameras listed in a YAML manifest.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Batch: skip the confirmation prompt.",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Batch: restart Frigate once after saving.",
    )
    parser.add_argument(
        "--live-transcode-hw",
        default=None,
        help="go2rtc encoder for the H.265->H.264 live view: auto "
             "(default), none (software), or an engine name "
             "(vaapi, v4l2m2m, cuda, ...).",
    )

    args = parser.parse_args()

    if args.discover_interface:
        try:
            ipaddress.IPv4Address(args.discover_interface)
        except ValueError:
            raise RuntimeError(
                "--discover-interface must be an IPv4 address."
            )

    if not 0.5 <= args.discover_timeout <= 30:
        raise RuntimeError("--discover-timeout must be 0.5 to 30 seconds.")

    if args.discover:
        print_devices(run_discovery(
            args.discover_timeout, args.discover_interface
        ))
        return

    if (args.yes or args.restart) and not args.batch:
        raise RuntimeError("--yes and --restart only apply to --batch.")

    if args.batch:
        batch(args)
    else:
        args.live_transcode_hw = args.live_transcode_hw or "auto"
        validate_hw(args.live_transcode_hw)
        interactive(args)


if __name__ == "__main__":
    try:
        main()

    except (KeyboardInterrupt, EOFError):
        print(
            "\nStopped. If saving had already completed, "
            "it is not undone."
        )
        sys.exit(130)

    except Exception as exc:
        if isinstance(exc, RuntimeError):
            print(f"\nERROR: {exc}", file=sys.stderr)
        else:
            # YAML/parser and subprocess errors can include
            # configuration values or credential-bearing URLs.
            print(
                f"\nERROR: {type(exc).__name__}. "
                "Details suppressed to avoid exposing credentials. "
                "Check file permissions, YAML syntax, "
                "and Docker access.",
                file=sys.stderr,
            )

        sys.exit(1)
