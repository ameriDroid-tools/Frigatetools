#!/usr/bin/env python3
"""Local tests for add_camera_v2.py with stubbed docker/ffprobe/Frigate."""
import importlib.util, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
import yaml

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "add_camera_v2.py"
MANIFEST = HERE.parent / "bundle_example.yml"
PASSWORD = "S3cr3t:p@ss/w#rd"          # special chars -> percent-encoding
PLAIN = "EXAMPLE-NOT-A-REAL-PASSWORD"
results = []

def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)

def setup(extra_env=None):
    work = Path(tempfile.mkdtemp(prefix="camtest_"))
    cfgdir = work / "config"; cfgdir.mkdir()
    shutil.copy(HERE / "sample_config.yml", cfgdir / "config.yml")
    os.chmod(cfgdir / "config.yml", 0o600)
    env = dict(os.environ)
    env.update({
        "PATH": f"{HERE/'fakebin'}:{env['PATH']}",
        "FAKE_DOCKER_LOG": str(work / "docker.log"),
        "FAKE_CFG_DIR": str(cfgdir),
        "FAKE_FRIGATE_PATH": str(HERE / "fakefrigate"),
        "BUNDLE_CAM_PASSWORD": PASSWORD,
    })
    env.update(extra_env or {})
    return work, cfgdir / "config.yml", env

def run(args, env, stdin=None):
    p = subprocess.run([sys.executable, str(SCRIPT)] + args, env=env,
                       input=stdin, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr

def no_secret(text):
    from urllib.parse import quote
    return all(s not in text for s in (PASSWORD, quote(PASSWORD, safe=""), PLAIN, "w#rd"))

def manifest_with(work, cameras_patch=None, text=None):
    data = yaml.safe_load(MANIFEST.read_text()) if text is None else yaml.safe_load(text)
    if cameras_patch: data["cameras"] = cameras_patch
    p = work / "m.yml"; p.write_text(yaml.safe_dump(data)); os.chmod(p, 0o600)
    return p

# 1. Full batch from the example manifest -------------------------------
work, cfg, env = setup()
rc, out = run(["--batch", str(MANIFEST), "--config", str(cfg), "--yes", "--restart"], env)
(work / "out1.txt").write_text(out)
check("batch: exit 0", rc == 0)
check("batch: no password in output", no_secret(out))
log = (work / "docker.log").read_text()
check("batch: no password in docker argv", no_secret(log))
check("batch: probes ran inside container", "Stream probes run inside the container" in out)
check("batch: one validation call", log.count('"python3", "-c"') == 1)
check("batch: one restart", log.count('["restart"') == 1)
backups = list(cfg.parent.glob("config.yml.bak_*"))
check("batch: exactly one backup", len(backups) == 1)
check("batch: backup equals original", backups and backups[0].read_text() == (HERE/"sample_config.yml").read_text())
check("batch: mode preserved 0600", oct(cfg.stat().st_mode & 0o777) == "0o600")
raw = cfg.read_text()
check("batch: comment preserved", "# keep this comment" in raw)
c = yaml.safe_load(raw)
s, cams = c["go2rtc"]["streams"], c["cameras"]
from urllib.parse import quote
enc = quote(PASSWORD, safe="")
check("driveway: native main is h265 path", s["driveway"] == [f"rtsp://admin:{enc}@192.0.2.11:554/h265Preview_01_main"])
check("driveway: transcode stream", s["driveway_h264"] == ["ffmpeg:driveway#video=h264#audio=opus#hardware"])
check("driveway: record input is native restream",
      cams["driveway"]["ffmpeg"]["inputs"][0] == {"path": "rtsp://127.0.0.1:8554/driveway", "input_args": "preset-rtsp-restream", "roles": ["record"]})
check("driveway: detect on sub 896x512",
      cams["driveway"]["ffmpeg"]["inputs"][1]["roles"] == ["detect"] and cams["driveway"]["detect"]["width"] == 896 and cams["driveway"]["detect"]["height"] == 512)
check("driveway: live.streams default = h264", list(cams["driveway"]["live"]["streams"].values())[0] == "driveway_h264")
check("backyard: h265 failed -> fell back to h264 main", s["backyard"][0].endswith("/h264Preview_01_main"))
check("backyard: no transcode for h264", "backyard_h264" not in s and "live" not in cams["backyard"])
check("backyard: review retention", cams["backyard"]["record"]["alerts"]["retain"]["days"] == 14 and cams["backyard"]["record"]["motion"]["days"] == 0 and cams["backyard"]["detect"]["enabled"] is True)
check("garage: continuous 3 days + audio aac",
      cams["garage"]["record"]["continuous"]["days"] == 3 and cams["garage"]["ffmpeg"]["output_args"]["record"] == "preset-record-generic-audio-aac")
check("porch: sonoff optional sub used", s["porch_sub"][0].endswith("/av_stream/ch1") and cams["porch"]["detect"]["width"] == 640)
check("porch: plaintext pw + custom retention",
      s["porch"][0].startswith(f"rtsp://admin:{PLAIN}@") and cams["porch"]["record"]["motion"]["days"] == 5 and cams["porch"]["objects"]["track"] == ["person"])
check("existing camera untouched", cams["existing_cam"]["detect"]["width"] == 1280 and s["existing_cam"] == ["rtsp://user:oldpw@192.0.2.50:554/stream1"])
check("failed-probe diagnostic was redacted (seen in output)", "<camera URL>" in out and "<redacted>" in out)
print("---- driveway YAML ----")
print(yaml.safe_dump({"go2rtc": {"streams": {k: v for k, v in s.items() if k.startswith("driveway")}}, "cameras": {"driveway": cams["driveway"]}}, sort_keys=False))

# 2. Version reject -------------------------------------------------------
work, cfg, env = setup({"FAKE_FRIGATE_VERSION": "0.18.0"})
before = cfg.read_text()
rc, out = run(["--batch", str(MANIFEST), "--config", str(cfg), "--yes"], env)
check("0.18 rejected, exit 1", rc == 1 and "supports Frigate 0.17.x only" in out)
check("0.18: config unchanged, no backup", cfg.read_text() == before and not list(cfg.parent.glob("*.bak_*")))
work, cfg, env = setup({"FAKE_FRIGATE_VERSION": "0.17.0"})
rc, out = run(["--batch", str(MANIFEST), "--config", str(cfg), "--yes"], env)
check("0.17.0 accepted", rc == 0 and "Valid for Frigate 0.17.0" in out)

# 3. Sonoff without ch1 + host ffprobe fallback ---------------------------
work, cfg, env = setup({"FAKE_NO_CONTAINER_FFPROBE": "1"})
m = manifest_with(work, [{"name": "shed", "model": "Sonoff S2", "ip": "192.0.2.15"}])
rc, out = run(["--batch", str(m), "--config", str(cfg), "--yes"], env)
c = yaml.safe_load(cfg.read_text())
check("sonoff no ch1: single stream detect+record", rc == 0 and "shed_sub" not in c["go2rtc"]["streams"]
      and c["cameras"]["shed"]["ffmpeg"]["inputs"][0]["roles"] == ["detect", "record"] and c["cameras"]["shed"]["detect"]["width"] == 1920)
check("host ffprobe fallback warns", "Falling back to the host's ffprobe" in out)
check("sonoff: no password in output", no_secret(out))

# 4. Collision and probe failure leave config untouched -------------------
work, cfg, env = setup()
before = cfg.read_text()
m = manifest_with(work, [{"name": "existing_cam", "model": "rlc-811a", "ip": "192.0.2.13"}])
rc, out = run(["--batch", str(m), "--config", str(cfg), "--yes"], env)
check("collision rejected", rc == 1 and "already exists" in out and cfg.read_text() == before)
m = manifest_with(work, [{"name": "a", "model": "rlc-811a", "ip": "192.0.2.13"}, {"name": "a_sub", "model": "rlc-811a", "ip": "192.0.2.13"}])
rc, out = run(["--batch", str(m), "--config", str(cfg), "--yes"], env)
check("intra-manifest stream collision rejected", rc == 1 and "reserved" in out)
m = manifest_with(work, [{"name": "ghost", "model": "rlc-1224a", "ip": "192.0.2.99"}])
rc, out = run(["--batch", str(m), "--config", str(cfg), "--yes"], env)
check("probe failure aborts, unchanged", rc == 1 and "Nothing was saved" in out and cfg.read_text() == before)
check("probe failure: no password in output", no_secret(out))
m = manifest_with(work, [{"name": "x", "model": "rlc-811a", "ip": "192.0.2.13", "password_env": "NOPE_UNSET"}])
rc, out = run(["--batch", str(m), "--config", str(cfg), "--yes"], env)
check("missing password_env reported", rc == 1 and "NOPE_UNSET" in out)

# 5. Batch without --yes: decline -> no write -------------------------------
work, cfg, env = setup()
before = cfg.read_text()
rc, out = run(["--batch", str(MANIFEST), "--config", str(cfg)], env, stdin="n\n")
check("batch confirm declined -> unchanged", rc == 0 and "Cancelled" in out and cfg.read_text() == before)

# 6. Interactive wizard (no-auth camera so getpass is not needed) ---------
work, cfg, env = setup()
answers = "\n".join(["y", "2", "front", "n", "192.0.2.11", "", "", "", "", "2", "", "", "", "", "", "", "y", "n"]) + "\n"
rc, out = run(["--config", str(cfg), "--container", "frigate"], env, stdin=answers)
(work / "out_interactive.txt").write_text(out)
c = yaml.safe_load(cfg.read_text())
check("interactive: exit 0", rc == 0)
check("interactive: camera + transcode written",
      "front" in c["cameras"] and c["go2rtc"]["streams"].get("front_h264") == ["ffmpeg:front#video=h264#audio=opus#hardware"]
      and c["go2rtc"]["streams"]["front"] == ["rtsp://192.0.2.11:554/h265Preview_01_main"])

# 7. Discovery parsing ----------------------------------------------------
spec = importlib.util.spec_from_file_location("ac", SCRIPT); ac = importlib.util.module_from_spec(spec); spec.loader.exec_module(ac)
sample = b"""<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
<s:Body><d:ProbeMatches><d:ProbeMatch><d:Types>dn:NetworkVideoTransmitter</d:Types>
<d:Scopes>onvif://www.onvif.org/name/RLC-1224A onvif://www.onvif.org/hardware/RLC-1224A%20PoE onvif://www.onvif.org/type/video_encoder</d:Scopes>
<d:XAddrs>http://192.0.2.11:8000/onvif/device_service http://[fe80::1]/onvif</d:XAddrs></d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"""
d = ac.parse_probe_match(sample, "192.0.2.200")
check("discovery: parsed IP/name/hardware", d == [{"ip": "192.0.2.11", "xaddrs": ["http://192.0.2.11:8000/onvif/device_service", "http://[fe80::1]/onvif"], "name": "RLC-1224A", "hardware": "RLC-1224A PoE"}])
check("discovery: garbage ignored", ac.parse_probe_match(b"<not xml", "1.2.3.4") == [])
rc, out = run(["--discover", "--discover-timeout", "1"], env)
check("--discover runs and exits 0", rc == 0 and ("ONVIF" in out))

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
