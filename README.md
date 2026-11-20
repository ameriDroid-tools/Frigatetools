# Frigatetools

Offline, LAN-only camera installer for [Frigate](https://frigate.video/) 0.17.x, for ameriDroid's turnkey NVR bundles. Those bundles ship with the latest Ubuntu Server or Debian. Hardware differs by bundle (the setup guide covers the `frigate` login, which Frigate 0.17.2 image to run, and live view). The installer probes camera RTSP streams on the local network (and can look for ONVIF devices on the LAN), then adds them to an existing Frigate `config.yml`. After Frigate itself is installed, the installer does not need internet access.



## Supported cameras

| Model | Profile key |
| --- | --- |
| Sonoff S2 (CAM-S2 / CAM Slim Gen2) | `sonoff-s2` |
| Reolink RLC-1224A | `rlc-1224a` |
| Reolink Duo 3 PoE | `duo-3-poe` |
| Reolink RLC-811A | `rlc-811a` |

## Quick start

Follow [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) for customer setup. Staff who prepare a bundle before it ships use [docs/BUNDLE_SETUP.md](docs/BUNDLE_SETUP.md). The customer guide covers Docker, cloning this repo to `~/frigate-installer`, copying the starter files, starting Frigate, and adding cameras:

```bash
python3 ~/frigate-installer/add_camera_v2.py
```

Run that from any directory. Frigate must already be running. The installer finds `config.yml` (or `config.yaml` if that is the only file) from the container's `/config` bind mount. Pass `--config /path/to/config.yml` only when you want to override that.

`bundle_example.yml` is a placeholder batch manifest (documentation IP addresses only). Copy it somewhere private before putting real camera addresses or passwords in it, and do not commit that copy.

## Repository layout

```text
add_camera_v2.py          camera installer (interactive or --batch)
bundle_example.yml        example batch manifest; placeholders only
docs/SETUP_GUIDE.md       customer setup guide
docs/BUNDLE_SETUP.md      internal checklist for preparing a bundle before it ships
docs/docker-compose.yml   starter Compose file (copy to ~/frigate/)
docs/config.yml           starter Frigate 0.17 config with no cameras
```
