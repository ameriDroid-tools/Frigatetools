# ameriDroid Frigate bundle: build checklist

Internal checklist for ameriDroid staff who prepare a turnkey Frigate NVR bundle before it ships. Customers use [SETUP_GUIDE.md](SETUP_GUIDE.md). Hardware differs by bundle; this page stays general.

> [!NOTE]
> **Internet is needed only during this prep.** After the box ships, Frigate and the cameras run on the customer's LAN. The camera installer must already have its Python library on the box, because the customer may have no internet.

---

## 1. Before you start

- [ ] Confirm the packing list for this order. Board, power, storage, PoE switch, and cameras are not the same for every bundle.
- [ ] Use the latest Ubuntu Server or Debian. Both are headless (no desktop). Sign in at the text console, or with `ssh frigate@<BOX_IP>` once that account exists.
- [ ] Do the steps below as `frigate`, not as `root`. `~/frigate` and `$USER` have to belong to `frigate`.

## 2. Install the OS and the `frigate` account

- [ ] Install the latest Ubuntu Server or Debian. Do not install a desktop.
- [ ] Create the account the customer guide documents:
  - **Username:** `frigate`
  - **Password:** `Temp_123!`
- [ ] Leave that password as `Temp_123!`. The customer guide tells them to change it with `passwd` at first login. Do not change it during prep.
- [ ] `sudo` must work for `frigate` and must ask for the `frigate` password. On Ubuntu Server this is already true. On stock Debian, `sudo` is often missing and `frigate` is not allowed to use it. Use the fix in [SETUP_GUIDE.md Step 1](SETUP_GUIDE.md#step-1-get-the-box-online-and-up-to-date) (same steps under [Troubleshooting](SETUP_GUIDE.md#sudo-command-not-found-or-frigate-is-not-in-the-sudoers-file)): `su -` (the hyphen is required; it asks for the **root** password), then `apt-get install -y sudo` and `usermod -aG sudo frigate`, then `exit` twice and sign back in as `frigate`.
- [ ] Check: `sudo echo "sudo works"` prints `sudo works`.

## 3. Update the OS

```bash
sudo apt-get update && sudo apt-get -y upgrade
```

Optional, same as the customer guide: `sudo timedatectl set-timezone <Zone>` (`timedatectl list-timezones` lists them).

> [!TIP]
> If apt stalls at `0% [Waiting for headers]`, stop it and retry the same command with `-o Acquire::ForceIPv4=true`.

## 4. Install dependencies

> [!IMPORTANT]
> **Install `python3-ruamel.yaml` on the box before it ships.** `add_camera_v2.py` exits immediately without it. The customer guide assumes this package is already installed so they do not have to download it later.

```bash
sudo apt-get install -y python3 git python3-ruamel.yaml
```

`git` is required to clone this repo (same package the customer guide installs). `python3` and `python3-ruamel.yaml` are the installer's runtime.

Install Docker Engine and the Compose plugin the same way as [SETUP_GUIDE.md Step 2](SETUP_GUIDE.md#step-2-install-docker):

- [ ] `. /etc/os-release && echo $ID` prints `ubuntu` or `debian`. Use that page's apt repository (`download.docker.com/linux/ubuntu` or `download.docker.com/linux/debian`). Do not type a version number; the commands read it from the box.
- [ ] Install `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`, and `docker-compose-plugin`.
- [ ] `sudo usermod -aG docker frigate`, then log out and back in (or reboot) so the customer can run Docker without `sudo`.

Verify, still as `frigate`:

```bash
python3 -c "import ruamel.yaml; print('ruamel.yaml OK')"
docker compose version
sudo docker run hello-world
```

`ruamel.yaml OK`, a Compose version line, and `Hello from Docker!` are required before you continue.

## 5. Clone the repo and copy the starter files

```bash
git clone https://github.com/ameriDroid-tools/Frigatetools.git ~/frigate-installer
mkdir -p ~/frigate/config ~/frigate/storage
cp ~/frigate-installer/docs/docker-compose.yml ~/frigate/
cp ~/frigate-installer/docs/config.yml ~/frigate/config/config.yml
chmod 600 ~/frigate/config/config.yml
```

The layout is:

```text
~/frigate/
├── docker-compose.yml
├── config/
│   └── config.yml
└── storage/
```

- [ ] The settings file is named exactly `config.yml`. Frigate looks for that name first and otherwise creates `config.yaml`. The installer follows the same order. Do not leave both files in the folder.
- [ ] `./config` is a directory bind mount (`./config:/config`), not a single-file mount.

## 6. Hardware-specific settings (varies by bundle)

Details depend on the hardware for this order and are filled in per bundle. Until then, set only what you already know and leave the commented examples in the starter files.

- [ ] **Image.** On the box, `uname -m`. `x86_64` (Intel/AMD) uses `ghcr.io/blakeblackshear/frigate:0.17.2`. `aarch64` (ARM) uses `ghcr.io/blakeblackshear/frigate:0.17.2-standard-arm64`. Leave one `image:` line active in `~/frigate/docker-compose.yml`. Do not use `stable` or 0.18 or later. The installer only accepts Frigate 0.17.x.
- [ ] **Detector.** Varies by bundle. With no `detectors:` section, Frigate uses its CPU detector. See [object detectors](https://docs.frigate.video/configuration/object_detectors).
- [ ] **`hwaccel_args`.** Varies by bundle. See [hardware acceleration](https://docs.frigate.video/configuration/hardware_acceleration_video).
- [ ] **Device passthrough.** Varies by bundle (`devices:` / `privileged:` in `docker-compose.yml`). The commented examples in that file are USB Coral, PCIe Coral, VAAPI (`/dev/dri/renderD128`), and Hailo. Use only the lines that match this hardware.
- [ ] **Recordings.** Default is `./storage` mounted at `/media/frigate`. A bundle with a separate drive points that volume at the drive instead.
- [ ] **`shm_size`.** The starter file sets `512mb`. More cameras may need a larger value. See [Calculating required shm-size](https://docs.frigate.video/frigate/installation/#calculating-required-shm-size).

## 7. Start Frigate and smoke-test

```bash
cd ~/frigate
docker compose up -d
docker ps --filter name=frigate
```

- [ ] `STATUS` is `Up`. The image is the 0.17.2 tag chosen above. `restart: unless-stopped` is set in `docker-compose.yml`, so Frigate starts on boot.
- [ ] From another computer on the LAN, `https://<BOX_IP>:8971` loads (the browser warning about the certificate is expected). Port **5000** stays commented out.
- [ ] Do not delete this container after the check. The customer guide reads the first-start `admin` password from `docker logs frigate`.

Smoke-test the installer. This only lists ONVIF cameras and changes nothing. No cameras need to be plugged in. `No ONVIF devices answered` is normal on the bench; a missing-`ruamel.yaml` error is not.

```bash
python3 ~/frigate-installer/add_camera_v2.py --discover
```

## 8. Pre-ship checklist

- [ ] `python3 -c "import ruamel.yaml; print('ruamel.yaml OK')"` prints `ruamel.yaml OK`.
- [ ] `docker compose version` works without an error, as `frigate`.
- [ ] `docker ps --filter name=frigate` shows the container `Up`, and `restart: unless-stopped` is still set so it starts on boot.
- [ ] `~/frigate/config/config.yml` is the only settings file, mode `600`, and `cameras:` is still `{}` unless this order includes cameras that were added on purpose.
- [ ] The `frigate` password is still `Temp_123!`.
- [ ] If any secret was typed in this shell (a camera password, for example), clear it: `history -c` and `rm -f ~/.bash_history`.
- [ ] Shut the box down cleanly: `sudo shutdown -h now`.
