# Frigatetools

Offline, LAN-only camera installer for [Frigate](https://frigate.video/) 0.17.x, for Ameridroid's turnkey NVR bundles. Those bundles ship with the latest Ubuntu release, either Ubuntu Server or Ubuntu Desktop. Hardware differs by bundle (the setup guide covers the `frigate` login, which Frigate 0.17.2 image to run, and live view). The installer probes camera RTSP streams on the local network (and can look for ONVIF devices on the LAN), then adds them to an existing Frigate `config.yml`. After Frigate itself is installed, the installer does not need internet access.

**Not validated on real hardware yet.** The checks in `tests/` stub Docker, ffprobe, and Frigate. Treat the camera profiles and the setup guide's camera-menu notes as unconfirmed until they are tried on the boards and cameras that ship in a bundle.

## Supported cameras

| Model | Profile key |
| --- | --- |
| Sonoff S2 (CAM-S2 / CAM Slim Gen2) | `sonoff-s2` |
| Reolink RLC-1224A | `rlc-1224a` |
| Reolink Duo 3 PoE | `duo-3-poe` |
| Reolink RLC-811A | `rlc-811a` |

## Quick start

Follow [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md). It covers Docker, cloning this repo to `~/frigate-installer`, copying the starter files, starting Frigate, and adding cameras:

```bash
cd ~/frigate
python3 ~/frigate-installer/add_camera_v2.py --config config/config.yml
```

`bundle_example.yml` is a placeholder batch manifest (documentation IP addresses only). Copy it somewhere private before putting real camera addresses or passwords in it, and do not commit that copy.

## Repository layout

```text
add_camera_v2.py          camera installer (interactive or --batch)
bundle_example.yml        example batch manifest; placeholders only
docs/SETUP_GUIDE.md       customer setup guide
docs/docker-compose.yml   starter Compose file (copy to ~/frigate/)
docs/config.yml           starter Frigate 0.17 config with no cameras
tests/                    local harness: run_tests.py, fakebin/, fakefrigate/, sample_config.yml
```

## Tests

Python 3, plus `ruamel.yaml` (the installer) and `pyyaml` (the harness):

```bash
python3 -m pip install ruamel.yaml pyyaml
python3 tests/run_tests.py
```

On Ubuntu, `sudo apt-get install python3-ruamel.yaml python3-yaml` works instead of pip.
