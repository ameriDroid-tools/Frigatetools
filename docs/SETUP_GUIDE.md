# ameriDroid Frigate NVR Bundle: Setup Guide

Welcome! This guide takes you from "box just arrived" to "cameras recording" with your ameriDroid Frigate bundle.

You will:

1. Get the box online and up to date
2. Install Docker
3. Download the ameriDroid Frigate files (this repository)
4. Start Frigate
5. Log in to the Frigate web page
6. Prepare your cameras
7. Add your cameras with the ameriDroid camera installer

> [!NOTE]
> **Internet is needed during setup only.** Steps 1 to 4 download software. After setup, Frigate and your cameras run entirely on your home network. Nothing has to reach the internet for recording, detection, or viewing at home.

> [!NOTE]
> **What's in the box depends on the bundle.** Board, power supply, storage, PoE switch, and which cameras are included are not the same for every offering. Use the packing list or product page that came with your order.

---

## Contents

- [Before you start](#before-you-start)
- [Change the default password](#change-the-default-password)
- [Step 1: Get the box online and up to date](#step-1-get-the-box-online-and-up-to-date)
- [Step 2: Install Docker](#step-2-install-docker)
- [Step 3: Download the ameriDroid Frigate files](#step-3-download-the-ameridroid-frigate-files)
- [Step 4: Start Frigate](#step-4-start-frigate)
- [Step 5: Log in to Frigate](#step-5-log-in-to-frigate)
- [Step 6: Prepare your cameras](#step-6-prepare-your-cameras)
- [Step 7: Add cameras with the installer](#step-7-add-cameras-with-the-installer)
- [Option: add a whole bundle at once (batch mode)](#option-add-a-whole-bundle-at-once-batch-mode)
- [Installer reference](#installer-reference)
- [Troubleshooting](#troubleshooting)
- [Security](#security)

---

## Before you start

You need:

- The ameriDroid box, plugged into your router with an Ethernet cable and powered on
- A way to type commands on the box. **Ubuntu Server** has no desktop, so use SSH from another computer, or attach a keyboard and monitor. **Ubuntu Desktop:** open a Terminal on the box
- Your cameras (see [supported cameras](#supported-cameras)) and their power (PoE switch/injector or power adapters)
- Your phone, for the Reolink and/or eWeLink app
- Access to your home router's settings page (to reserve IP addresses)

### Supported cameras

The camera installer knows these models:

| Model | Menu number | Model key (for batch mode) |
| --- | --- | --- |
| Sonoff S2 (CAM-S2 / CAM Slim Gen2) | 1 | `sonoff-s2` |
| Reolink RLC-1224A | 2 | `rlc-1224a` |
| Reolink Duo 3 PoE | 3 | `duo-3-poe` |
| Reolink RLC-811A | 4 | `rlc-811a` |

### How to type the commands in this guide

Every grey box is a command. Copy the whole box, paste it into a terminal, and press **Enter**. When a command starts with `sudo`, Ubuntu may ask for **your login password**. Nothing shows on screen while you type it. That's normal.

The bundle ships with the latest Ubuntu release, in one of two editions.

- **Ubuntu Server** has no desktop. Set it up over SSH from another computer, or with a keyboard and monitor attached to the box. After you sign in, you are already at a command prompt.
- **Ubuntu Desktop** has a graphical desktop. Open a Terminal (press the Super key, type `Terminal`, and press Enter).

The commands in this guide are the same on both editions.

The Ubuntu account on the bundle is:

- **Username:** `frigate`
- **Password:** `Temp_123!`

**Ubuntu Server** has no desktop. From another computer on your home network:

```bash
ssh frigate@<BOX_IP>
```

Replace `<BOX_IP>` with the box's address (your router's device list, or the first address from `hostname -I` in Step 1 if you are already at a keyboard and monitor). You can also attach a keyboard and monitor and sign in at the text login with the same username and password.

**Ubuntu Desktop:** sign in at the graphical login with that username and password, then open a Terminal.

---

## Change the default password

> [!IMPORTANT]
> **Do this immediately after the first login**, before the other steps. `Temp_123!` is printed in this guide, so it is not a secret. Anyone who can reach the box could sign in with it until you change it.

```bash
passwd
```

Type `Temp_123!` when asked for the current password, then type a new password twice. Nothing shows on screen while you type. Remember the new password; this guide cannot recover it.

---

## Step 1: Get the box online and up to date

1. Find the box's IP address on your network (you'll need it later):

   ```bash
   hostname -I
   ```

   The first address shown (something like `192.168.1.20`) is usually the one you want. Write it down.

2. Install the latest updates:

   ```bash
   sudo apt-get update && sudo apt-get -y upgrade
   ```

3. Optional but recommended: set your time zone so recordings show the right time. Replace `America/Chicago` with your own zone (list them with `timedatectl list-timezones`):

   ```bash
   sudo timedatectl set-timezone America/Chicago
   ```

> [!TIP]
> Reserve a fixed IP address for the box in your router (see [Give everything a fixed IP address](#give-everything-a-fixed-ip-address)). Otherwise the Frigate web address may change after a router restart.

---

## Step 2: Install Docker

Frigate runs inside Docker. These steps follow Docker's official apt-repository instructions for [Ubuntu](https://docs.docker.com/engine/install/ubuntu/). They are the same on Ubuntu Server and Ubuntu Desktop.

### 2a. Remove any old or unofficial Docker packages

Safe to run even if none are installed (it may say "no packages found"):

```bash
sudo apt-get remove -y $(dpkg --get-selections docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc 2>/dev/null | cut -f1)
```

### 2b. Add Docker's Ubuntu repository

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt-get update
```

### 2c. Install Docker

```bash
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Check that it works. You should see "Hello from Docker!":

```bash
sudo docker run hello-world
```

> [!NOTE]
> **Alternative:** if the repository steps above fail on your system, Docker also offers a one-step convenience script. Docker recommends it mainly for testing, so use it only as a fallback:
>
> ```bash
> curl -fsSL https://get.docker.com -o get-docker.sh
> sudo sh get-docker.sh
> ```

### 2d. Let your user run Docker without `sudo`

The camera installer talks to Docker, so add yourself to the `docker` group:

```bash
sudo usermod -aG docker $USER
```

> [!IMPORTANT]
> **Log out and log back in** (or simply reboot with `sudo reboot`) so this takes effect. If you're using SSH, disconnect and reconnect.

After logging back in, this should work **without** `sudo`:

```bash
docker ps
```

You should see a header line (`CONTAINER ID   IMAGE ...`) and no "permission denied" error.

---

## Step 3: Download the ameriDroid Frigate files

This downloads the camera installer, the Frigate settings files, and this guide.

1. Install Git and the one extra Python library the installer needs:

   ```bash
   sudo apt-get install -y git python3-ruamel.yaml
   ```

2. Download the ameriDroid files to a folder named `frigate-installer` in your home folder:

   ```bash
   git clone https://github.com/ameriDroid-tools/Frigatetools.git ~/frigate-installer
   ```

   This clones the repository into `~/frigate-installer`. It contains `add_camera_v2.py` and `bundle_example.yml` at the top level, plus `docs/docker-compose.yml` and `docs/config.yml`. The copy commands below use those paths.

3. Create the Frigate folders and copy in the starter files:

   ```bash
   mkdir -p ~/frigate/config ~/frigate/storage
   cp ~/frigate-installer/docs/docker-compose.yml ~/frigate/
   cp ~/frigate-installer/docs/config.yml ~/frigate/config/config.yml
   chmod 600 ~/frigate/config/config.yml
   ```

You now have:

```text
~/frigate/
├── docker-compose.yml     how Docker runs Frigate
├── config/
│   └── config.yml         Frigate's settings (cameras get added here)
└── storage/               recordings, snapshots, exports
```

> [!WARNING]
> Keep the file named exactly **`config.yml`** inside `~/frigate/config/`. If Frigate can't find `config.yml`, it creates a *different* file named `config.yaml`, and the camera installer would then be editing the wrong file.

> [!NOTE]
> **Hardware varies by bundle.** Detector, video-decoding acceleration, device passthrough, shared memory, and where recordings are stored are already set on a shipped bundle, and they differ between offerings. The starter files here leave those lines commented, with examples, for anyone setting them from the [Frigate docs](https://docs.frigate.video/). Pick the Frigate image in Step 4. `shm_size` stays `512mb` unless more cameras need more shared memory ([Calculating required shm-size](https://docs.frigate.video/frigate/installation/#calculating-required-shm-size)).

---

## Step 4: Start Frigate

```bash
cd ~/frigate
docker compose up -d
```

The first start downloads Frigate (around a gigabyte or more), so give it a few minutes.

Check that it's running:

```bash
docker ps --filter name=frigate
```

The `STATUS` column should say `Up ...`. To watch Frigate's log while it starts (press **Ctrl+C** to stop watching; Frigate keeps running):

```bash
docker logs -f frigate
```

> [!NOTE]
> Frigate stays on **0.17.x**. The camera installer rejects other versions. Do not change the image to `stable` or to 0.18 or later; `stable` now points at a newer Frigate.
>
> Which 0.17.2 tag to use depends on the CPU. On the box, run:
>
> ```bash
> uname -m
> ```
>
> - `x86_64` (Intel/AMD): `ghcr.io/blakeblackshear/frigate:0.17.2`
> - `aarch64` (ARM): `ghcr.io/blakeblackshear/frigate:0.17.2-standard-arm64`
>
> `docker-compose.yml` has both lines. Leave the one that matches `uname -m` active and comment out the other. A shipped bundle should already have the matching line set.

Frigate starts with **no cameras**. That's expected. You'll add them in Step 7.

---

## Step 5: Log in to Frigate

1. The first time Frigate starts, it creates an `admin` user with a random password and prints it in the log. Show it with:

   ```bash
   docker logs frigate 2>&1 | grep -A1 "User: admin"
   ```

   You'll see something like:

   ```text
   ***    User: admin                                   ***
   ***    Password: 3f9c1d...   ***
   ```

2. On any computer or phone on your home network, open:

   ```text
   https://<BOX_IP>:8971
   ```

   Replace `<BOX_IP>` with the address from Step 1, for example `https://192.168.1.20:8971`.

3. Your browser will warn that the connection is "not private" or the certificate is not trusted. That's expected: Frigate uses a self-made security certificate. Choose **Advanced**, then **Proceed / Accept the risk and continue**.

4. Log in as `admin` with the password from the log.

5. **Change the admin password right away:** in Frigate, go to **Settings > Users**. Frigate requires at least 12 characters.

> [!TIP]
> The password only appears in the log of Frigate's very first start. If the command above shows nothing (for example because the container was recreated since), or you forgot the password, see [I'm locked out of the Frigate web page](#im-locked-out-of-the-frigate-web-page).

> [!NOTE]
> Frigate's web page may offer its own "add camera" wizard. For the cameras in this bundle, use the ameriDroid camera installer in Step 7 instead. It's set up and tested for these exact models.

---

## Step 6: Prepare your cameras

Do this for each camera **before** running the camera installer. You need each camera to:

- have its own password set,
- have **RTSP** turned on (this is how Frigate gets video), and
- have **ONVIF** turned on (lets the installer find the camera on your network automatically).

### Reolink (RLC-1224A, Duo 3 PoE, RLC-811A)

1. Connect the camera to your network (PoE switch or PoE injector) and wait a minute or two for it to boot.
2. Open the **Reolink app** on your phone (or the Reolink Client on a computer), add the camera, and follow the prompts to **create the camera's admin password**. Write it down; the installer needs it.
3. Turn on RTSP and ONVIF:
   - **Reolink app:** open the camera's settings, then **Network > Advanced > Server Settings** (on some app versions: **More Features > Advanced Network Settings > Server Settings**).
   - **Reolink Client:** **Device Settings > Network > Advanced > Server Settings**.
   - **Web browser:** go to `http://<CAMERA_IP>`, log in, click the gear icon, then **Network > Advanced > Server Settings > Set Up**.

   Switch on **RTSP** (leave the port at **554**) and **ONVIF** (default port **8000**), then **Save**.
4. While you're in the settings, check for a firmware update.

> [!NOTE]
> Menu names vary a little between Reolink app and firmware versions. If you can't find "Server Settings", look for "Port Settings" under **Network > Advanced**. (Reolink's guide: [How to Configure Reolink Ports Settings](https://support.reolink.com/articles/900000621783-How-to-Configure-Reolink-Ports-Settings/).)

### Sonoff S2 (CAM-S2)

1. Set up the camera in the **eWeLink app** following Sonoff's instructions, and connect it to your Wi-Fi.
2. Update the camera firmware and the app. Sonoff lists **camera firmware 1.1.5 or later** and **eWeLink app 5.18.0 or later** for RTSP/ONVIF support.
3. In the eWeLink app, open the camera, then **Device Settings > More Settings > RTSP** (called "ONVIF/RTSP" on some Sonoff models). Enter your eWeLink password if asked, and turn it on.
4. That screen shows an **RTSP username and password**. These are what the camera installer needs, **not** your eWeLink account login. You can change them; Sonoff allows only letters and numbers in the password.

### Give everything a fixed IP address

Frigate connects to each camera by its IP address. If your router hands a camera a new address later, Frigate loses it. In your router's settings, find **DHCP reservation** (sometimes called "static lease", "address reservation", or "fixed IP") and reserve an address for:

- each camera, and
- the ameriDroid box itself.

Restart each camera after reserving so it picks up its reserved address. Write down each camera's IP address.

> [!TIP]
> Using the router's DHCP reservation is easier and safer than typing a static IP into each camera.

---

## Step 7: Add cameras with the installer

The installer walks you through one camera at a time. It:

- tests the camera's video stream from **inside** Frigate (so it proves Frigate can actually reach the camera),
- picks the right stream settings for your camera model,
- checks the new settings with Frigate itself before saving anything,
- makes a backup of your settings file, and
- never prints your camera password on screen.

### Start the installer

```bash
cd ~/frigate
python3 ~/frigate-installer/add_camera_v2.py --config config/config.yml
```

Frigate must be running (Step 4) when you do this.

### What it asks, and what to answer

| Question | What to do |
| --- | --- |
| `Frigate container name [frigate]` | Press **Enter**. |
| `Is this the configuration file Frigate actually uses?` | It shows the file on the box and inside Frigate. If they are `~/frigate/config/config.yml` and `/config/config.yml`, type **y**. |
| `Select camera model` | Type the number for your camera (1 to 4). |
| `Camera name, e.g. driveway` | A short name using **lowercase letters, numbers and underscores**, starting with a letter. Examples: `driveway`, `back_yard`, `garage2`. |
| `Search the LAN for ONVIF cameras...?` | Press **Enter** (yes). Pick your camera's number from the list, or type its IP address. |
| `RTSP port [554]` | Press **Enter**. |
| `Camera username` | Reolink: `admin` (or the user you created). Sonoff: the RTSP username from the eWeLink app. |
| `Camera password` | Type it. Nothing shows on screen while you type. |
| `Main RTSP path` / `Sub RTSP path` | Press **Enter** to accept the suggested path. The installer automatically tries the model's backup path if the first one fails. |
| `Recording policy` | **2** (motion-only, the default) is a good start. See below. |
| `... retention days` | How many days to keep footage. Press **Enter** for the default. |
| `Enable object detection?` | Press **Enter** (yes). |
| `Detection frames per second [5]` | Press **Enter**. |
| `Objects to track` | Press **Enter** for `person,car`, or type your own list, such as `person,car,dog`. |
| `Include audio in recordings?` | Only asked if the camera sends audio. Your choice; default is no. |
| `Add an H.264 transcoded live-view stream...?` | Only asked when the main stream is H.265 (often the RLC-1224A and Duo 3). **Enter** (yes) adds an H.264 copy so more browsers can play it. On a low-power box, type **n** so that 12MP/16MP stream is not re-encoded. See [Live view encoder](#live-view-encoder). |
| `Validate and save this camera?` | Check the summary, then type **y**. |
| `Restart Frigate now?` | Type **y** to start using the camera now. All cameras disconnect briefly during the restart. |

**Recording policy choices:**

| Choice | What it keeps |
| --- | --- |
| 0. Disabled | No recordings (live view and detection only) |
| 1. Continuous recording | Everything, around the clock (uses the most disk space) |
| 2. Motion-only recording | Footage whenever there's motion **(default)** |
| 3. Review alerts/detections only | Only footage around detected objects (turns detection on automatically) |
| 4. Custom combination | Set days for continuous, motion, alerts and detections separately (0 turns a category off) |

If you chose alerts or detections, it also asks whether to keep only the motion parts of those events (default) or everything in them.

### Example session

Here is what adding a Reolink RLC-1224A looks like. Your addresses and numbers will differ.

```text
$ python3 ~/frigate-installer/add_camera_v2.py --config config/config.yml
==========================================
 Frigate 0.17.x Camera Installer
==========================================
Frigate container name [frigate]:

Host configuration:      /home/you/frigate/config/config.yml
Container configuration: /config/config.yml
Container:               frigate
Is this the configuration file Frigate actually uses? [y/N]: y

Select camera model:
  1. Sonoff S2
  2. Reolink RLC-1224A
  3. Reolink Duo 3 PoE
  4. Reolink RLC-811A
Choose: 2
Camera name, e.g. driveway: driveway

Search the LAN for ONVIF cameras to pick the IP from a list? [Y/n]:
Searching the LAN for ONVIF cameras (3s)...
Found 2 ONVIF device(s):
   1. 192.168.1.50     RLC-1224A
   2. 192.168.1.51     RLC-811A
Pick a device number, or type an IP address: 1
RTSP port [554]:
Camera username; blank for no authentication: admin
Camera password:
Stream probes run inside the container (...ffprobe).

Enter only the RTSP path. Do not enter the full rtsp:// URL.
Main RTSP path [/h265Preview_01_main]:
  Testing main stream /h265Preview_01_main over RTSP/TCP...
    OK: hevc, 4512x2512, audio: aac

Enter only the RTSP path. Do not enter the full rtsp:// URL.
Sub RTSP path [/h264Preview_01_sub]:
  Testing sub stream /h264Preview_01_sub over RTSP/TCP...
    OK: h264, 896x512, audio: aac

Recording policy
  0. Disabled
  1. Continuous recording
  2. Motion-only recording
  3. Review alerts/detections only
  4. Custom combination
Choose [2]:
Motion retention days [7]:

Enable object detection? [Y/n]:
Detection frames per second [5]:
Object labels must be supported by your configured detector/model.
Objects to track, comma-separated [person,car]:
Include audio in recordings? [y/N]:

The main stream is H.265. Recording uses it natively, but many browsers cannot play H.265 live.
Add an H.264 transcoded live-view stream in go2rtc (recording stays native)? [Y/n]:

================ SUMMARY ================
Camera:       driveway
Model:        Reolink RLC-1224A
Address:      192.168.1.50:554
Streams:      main + sub
Main video:   hevc 4512x2512 (/h265Preview_01_main)
Detect video: 896x512 (/h264Preview_01_sub)
Live view:    H.264 transcode via go2rtc stream 'driveway_h264'
Detection:    enabled, 5 fps
Objects:      person, car
Recording:    enabled
  continuous   0 days
  motion       7 days
  alerts       0 days
  detections   0 days
Record audio: disabled
Config file:  /home/you/frigate/config/config.yml
Container:    frigate
Credentials:  hidden here; stored in go2rtc source URLs
=========================================

H.264 live transcodes run in go2rtc only while someone watches; each viewer session costs encode CPU/GPU. Recording and detection use the native streams.
...

Validate and save this camera? [y/N]: y

Validating with the installed Frigate configuration model...
  Valid for Frigate 0.17.2.

Backup created: /home/you/frigate/config/config.yml.bak_20261119_140512_123456
Saved: /home/you/frigate/config/config.yml

Restart Frigate now? All cameras will briefly disconnect. [y/N]: y

Docker accepted the restart. Check Frigate's UI and logs to confirm recording, detection, and live playback.
Stream probes and schema validation do not verify runtime decoding, hardware acceleration, or detector load.
```

Run the installer again for each camera. After the restart, open `https://<BOX_IP>:8971`. Your new camera should appear within a minute.

### Good to know

- **Nothing is saved until you say yes** at "Validate and save this camera?". If you quit before then (press **Ctrl+C**), your settings are untouched.
- **It only adds cameras.** It won't change or overwrite a camera that already exists, and it refuses a name already in use. To change or remove a camera later, see [Changing or removing a camera](#changing-or-removing-a-camera).
- **Every save makes a backup** next to the settings file, named like `config.yml.bak_20261119_140512_123456`.
- **Sonoff S2:** the installer checks for the camera's smaller second stream (`/av_stream/ch1`) and uses it for detection if it works. If not, it uses the one main stream for everything, which works but uses more processing power.
- **Reolink RLC-1224A and Duo 3:** the installer tries the H.265 main stream first, then H.264. **RLC-811A:** H.264 first, then H.265. Recording always uses the camera's own stream. An H.264 live-view copy, if you added one, is encoded only while someone is watching that copy.

---

## Option: add a whole bundle at once (batch mode)

Batch mode adds several cameras from one list file (a "manifest"). It tests every camera first and saves **all or nothing**: if any camera fails its stream test, nothing is saved. It makes one backup, one save, and at most one Frigate restart.

### 1. Make your manifest

Copy the example and edit it:

```bash
cp ~/frigate-installer/bundle_example.yml ~/frigate/my_cameras.yml
chmod 600 ~/frigate/my_cameras.yml
nano ~/frigate/my_cameras.yml
```

(In `nano`, save with **Ctrl+O** then **Enter**; exit with **Ctrl+X**.)

The example's IP addresses (`192.0.2.x`) are placeholders and will never match a real camera. Replace them with your cameras' addresses. A simple manifest looks like this:

```yaml
container: frigate
restart: false

defaults:
  port: 554
  username: admin
  password_env: BUNDLE_CAM_PASSWORD   # password comes from this variable, not the file
  record: motion
  retention_days: 7
  detect: true
  fps: 5
  objects: [person, car]

cameras:
  - name: driveway
    model: rlc-1224a
    ip: 192.168.1.50

  - name: garage
    model: rlc-811a
    ip: 192.168.1.51

  - name: porch
    model: sonoff-s2
    ip: 192.168.1.52
    username: rtsp_user              # Sonoff has its own RTSP login...
    password_env: SONOFF_PASSWORD    # ...so give it its own password variable
```

**Manifest settings**

| Setting | Where | Meaning |
| --- | --- | --- |
| `container` | top | Frigate container name (default `frigate`; `--container` overrides it) |
| `restart` | top | `true` restarts Frigate once after saving (also `--restart`) |
| `live_transcode_hardware` | top | Encoder for the H.264 live-view copy: `auto` (default; hardware if go2rtc finds it, otherwise software), `none` (software), or an engine name such as `vaapi`, `v4l2m2m`, `cuda`. `--live-transcode-hw` overrides it. |
| `defaults` | top | Settings applied to every camera unless that camera sets its own |
| `name` | camera | Required. Lowercase letters, numbers, underscores; starts with a letter. Not allowed in `defaults`. |
| `model` | camera | Required. `sonoff-s2`, `rlc-1224a`, `duo-3-poe`, `rlc-811a` (or menu number 1 to 4, or the model name) |
| `ip` | camera | Required. The camera's IP address. Not allowed in `defaults`. |
| `port` | either | RTSP port (default `554`) |
| `username` / `username_env` | either | Camera username, or the name of an environment variable holding it |
| `password` / `password_env` | either | Camera password, or (better) the name of an environment variable holding it. Use one or the other, not both. |
| `record` | either | `disabled`, `continuous`, `motion` (default), `review`, or `custom` |
| `retention_days` | either | Days to keep, for `continuous` or `motion` (default 7) |
| `alert_days`, `detection_days` | either | For `review` (defaults 14 and 7) |
| `retention` | either | For `custom`: any of `continuous`, `motion`, `alerts`, `detections` with a number of days |
| `review_retain_mode` | either | `motion` (default) or `all` |
| `detect` | either | `true`/`false` object detection (forced on for review recording) |
| `fps` | either | Detection frames per second, 1 to 30 (default 5) |
| `objects` | either | List of things to detect (default `[person, car]`) |
| `audio` | either | `true` to record audio, if the camera has it (default `false`) |
| `live_transcode` | either | `true` (default) adds the H.264 live-view copy when the main stream is H.265. `false` skips it. The installer also forces this off when the probed main stream is not H.265. |
| `main_path`, `sub_path` | camera | Override the RTSP path (advanced; the model's backup path is still tried) |

### 2. Set the password, then run

Type the camera password into a variable, so it never lands in the file or your command history (nothing shows while you type):

```bash
read -rsp "Camera password: " BUNDLE_CAM_PASSWORD && export BUNDLE_CAM_PASSWORD && echo
```

If you used a second variable (like `SONOFF_PASSWORD` above), set it the same way:

```bash
read -rsp "Sonoff RTSP password: " SONOFF_PASSWORD && export SONOFF_PASSWORD && echo
```

Run the batch:

```bash
cd ~/frigate
python3 ~/frigate-installer/add_camera_v2.py --batch my_cameras.yml --config config/config.yml
```

It tests every camera, validates everything with Frigate, shows a summary, and then asks before writing and before restarting.

**Unattended** (no questions; saves and restarts automatically):

```bash
python3 ~/frigate-installer/add_camera_v2.py --batch my_cameras.yml --config config/config.yml --yes --restart
```

> [!WARNING]
> If you put plain passwords in the manifest (`password: ...`), keep it private with `chmod 600 ~/frigate/my_cameras.yml`. The installer warns you if the file is readable by other users.

---

## Installer reference

```text
python3 add_camera_v2.py [options]
```

| Option | What it does |
| --- | --- |
| `--config FILE` | Frigate settings file to edit. Default: `config.yml` in the current folder. In this guide: `config/config.yml` from `~/frigate`. |
| `--container NAME` | Frigate container name. Interactive mode asks if omitted; batch mode uses the manifest's `container`, otherwise `frigate`. |
| `--discover` | Just list ONVIF cameras found on the network, then exit. Changes nothing. |
| `--discover-timeout SECONDS` | How long to wait for cameras to answer (0.5 to 30; default 3). |
| `--discover-interface HOST_IP` | The box's own IPv4 address to search from, for boxes with more than one network port. |
| `--batch MANIFEST` | Add all cameras listed in a manifest file. |
| `--yes` | Batch only: don't ask before saving. |
| `--restart` | Batch only: restart Frigate once after saving. |
| `--live-transcode-hw ENGINE` | Encoder for the H.264 live-view copy of an H.265 main stream: `auto` (default), `none` (software), or an engine name (`vaapi`, `v4l2m2m`, `cuda`, ...). |

Examples:

```bash
# See which cameras are on the network
python3 ~/frigate-installer/add_camera_v2.py --discover

# Wait longer for slow cameras, from a specific network port
python3 ~/frigate-installer/add_camera_v2.py --discover --discover-timeout 10 --discover-interface 192.168.1.20
```

### Live view encoder

Leave this at **`auto`**. That is the installer's default (`--live-transcode-hw auto`, and `live_transcode_hardware: auto` in a manifest). For an H.265 main stream it tells go2rtc to use a hardware encoder when one works and otherwise software (`ffmpeg:<name>#video=h264#hardware`, plus `#audio=opus` when that stream has audio). `none` forces software (no `#hardware`). A name such as `vaapi`, `v4l2m2m`, or `cuda` forces that engine.

The question is asked only when the probed main stream is H.265. Answering **yes** (the default) adds go2rtc stream `<name>_h264` and a live-view menu. The first entry, which Frigate plays by default, is **Main H.264 (transcoded)**. Then **Sub stream** (`<name>_sub`) when a sub stream exists, then **Main native H.265**. The encoder runs only while someone is watching the transcoded entry.

On a low-power box, re-encoding a 12MP or 16MP Reolink main stream (RLC-1224A or Duo 3 PoE) is often too heavy when `auto` falls back to software. Skip it: type **n** at the question, or set `live_transcode: false` in the manifest. Recording still uses the native main stream, and detection still uses the smaller sub stream. The installer then does **not** create `<name>_h264` and does **not** add the live-view menu, so the page plays the go2rtc stream named after the camera (the native main stream). A browser that cannot play H.265 may show no picture. The sub stream is still in go2rtc as `<name>_sub` and is what detection uses; the installer only lists **Sub stream** in the on-screen menu when the H.264 copy is also enabled. If you did enable that copy, choose **Sub stream** in the player instead of **Main H.264 (transcoded)** so that viewer does not start the main-stream encoder.

### Requirements the installer checks

- Run it **on the box itself** (where Docker and Frigate run).
- **Frigate must be running.** It uses Frigate's own tools to test streams and validate settings.
- **Frigate must be version 0.17.x.** Other versions are refused, and nothing is saved.
- The settings **folder** must be mounted into Frigate (`./config:/config`, as in the provided `docker-compose.yml`). A single-file mount like `./config.yml:/config/config.yml` is refused.
- `python3-ruamel.yaml` must be installed (Step 3).

---

## Troubleshooting

### "Missing dependency: ruamel.yaml"

```bash
sudo apt-get install -y python3-ruamel.yaml
```

### "permission denied" from Docker, or "Cannot inspect the container. Check its name and your Docker permissions."

- You probably haven't logged out and back in since Step 2d. Log out and back in (or reboot), then try `docker ps`.
- Check you're in the group: `groups` should list `docker`. If not, repeat Step 2d.
- Check the container name: `docker ps --format '{{.Names}}'` should show `frigate`.

### "The Frigate container must be running..."

```bash
cd ~/frigate && docker compose up -d
docker ps --filter name=frigate
```

If it keeps stopping, read the end of the log for the reason: `docker logs --tail 50 frigate`.

### "The selected configuration is not inside a bind-mounted directory..." or "...mounted as a single file"

The installer was pointed at a different file from the one Frigate uses, or `docker-compose.yml` was changed. Run the installer from `~/frigate` with `--config config/config.yml`, and make sure `docker-compose.yml` still has `- ./config:/config`. If you change `docker-compose.yml`, apply it with `docker compose up -d`.

### "Existing configuration not found"

Check the file is there: `ls -l ~/frigate/config/`. If you only see `config.yaml` (with an "a"), Frigate created that because `config.yml` was missing. Stop Frigate, put the starter file back, and start again:

```bash
cd ~/frigate && docker compose down
cp ~/frigate-installer/docs/config.yml ~/frigate/config/config.yml && chmod 600 ~/frigate/config/config.yml
rm ~/frigate/config/config.yaml
docker compose up -d
```

### Discovery finds nothing ("No ONVIF devices answered")

- Make sure **ONVIF is enabled** on the camera (Step 6).
- Camera and box must be on the **same network**. Guest Wi-Fi networks and separate VLANs usually block discovery.
- A firewall on the box can block the replies. Check with `sudo ufw status`. If it says `active`, the simplest fix is to just **type the camera's IP address** when asked; discovery is only a convenience.
- Wait longer: `--discover-timeout 10`.
- Box has more than one network port? Add `--discover-interface <BOX_IP>`.
- You can always skip the search and type the IP. Find it in your router's list of connected devices, or in the Reolink/eWeLink app.

### The stream test fails

The installer shows the camera's error message (with your password hidden) and offers to retry. Common causes:

| Message contains | Likely cause | Fix |
| --- | --- | --- |
| `401` / `Unauthorized` | Wrong username or password | Re-check credentials. Sonoff uses its **RTSP** login from the eWeLink app, not your eWeLink account. |
| `404` / `Not Found` | Wrong path, or that stream isn't offered | Press **Enter** to accept the suggested path; make sure you picked the right camera model. |
| `Connection refused` | RTSP is turned off, or wrong port | Turn on RTSP (Step 6); use port 554 unless you changed it. |
| `timed out` | Wrong IP, camera offline, or Frigate can't reach it | See "Frigate can't reach the camera" below. |

Also:

- **Close other viewers** (VLC, the Reolink app's live view, another NVR). Cameras limit how many connections they allow.
- The test uses RTSP over TCP. VLC may use a different method, so "it works in VLC" doesn't always mean it works here.
- If you answer "no" to retry, nothing is saved. Fix the problem and run the installer again.

### Frigate can't reach the camera (timeouts)

The stream test runs **inside** the Frigate container, the same way Frigate itself will connect.

- From the box, check the camera answers: `ping -c 3 <CAMERA_IP>` (press Ctrl+C if it hangs).
- If ping works but the test times out, check that RTSP is on and the camera isn't on a separate network/VLAN that the box can't reach.
- If the camera's IP changed, reserve it in your router (see [fixed IP address](#give-everything-a-fixed-ip-address)).

### "WARNING: no usable ffprobe was found inside the 'frigate' container"

The installer fell back to testing from the box instead of from inside Frigate. That test doesn't prove Frigate can reach the camera. This shouldn't happen with the official Frigate image; check that `docker-compose.yml` still uses the ameriDroid-provided image. If it says no ffprobe is available at all, install it with `sudo apt-get install -y ffmpeg`.

### "Installed Frigate version is ...; this script supports Frigate 0.17.x only"

`docker-compose.yml` was changed to a different Frigate version. Set the image back to the 0.17 tag ameriDroid provides, then run `docker compose pull && docker compose up -d`.

### "Frigate configuration validation failed"

Frigate rejected the combined settings, and nothing was saved. The installer lists which settings had problems. This usually means something already in `config.yml` (edited by hand) is invalid. Check Frigate's log (`docker logs --tail 50 frigate`) or restore the last good backup (see below).

### "Could not replace the configuration file. Check directory permissions and file ownership."

The `~/frigate/config` folder or `config.yml` is owned by another user (often root). Give it back to your user, then run the installer again:

```bash
sudo chown "$USER": ~/frigate/config ~/frigate/config/config.yml
```

### "The configuration changed while the wizard was running"

Something else (for example Frigate's own settings editor) saved the file while the installer was running. Nothing was saved. Just run the installer again.

### The camera was added but live view is black or won't play

- Give Frigate a minute after a restart.
- H.265 cameras (often the RLC-1224A and Duo 3): if you answered **yes** to the H.264 live-view question, the stream menu lists "Main H.264 (transcoded)", "Sub stream", and "Main native H.265". "Main H.264 (transcoded)" is the one most browsers can play. If that picture stutters on a low-power box, choose **Sub stream** instead, or add the camera again and answer **no** so the main stream is not re-encoded ([Live view encoder](#live-view-encoder)). Answering **no** leaves no stream menu; the page then plays the native main stream, which many browsers cannot decode.
- Recordings from H.265 cameras are saved in H.265. Some browsers can't play H.265; if a recording won't play, try a different browser.
- Check **System** in Frigate's web page, and `docker logs --tail 50 frigate`, for errors.

### Undo a change (restore a backup)

Every save left a backup next to `config.yml`. List them (newest last):

```bash
ls -1 ~/frigate/config/config.yml.bak_*
```

Copy the one you want back and restart Frigate (replace the file name with yours):

```bash
cp ~/frigate/config/config.yml.bak_20261119_140512_123456 ~/frigate/config/config.yml
docker restart frigate
```

### Changing or removing a camera

The installer only adds cameras. To remove one, edit `~/frigate/config/config.yml` (for example `nano ~/frigate/config/config.yml`) and delete:

- the camera's block under `cameras:`, and
- its entries under `go2rtc:` > `streams:` (named `<name>`, `<name>_sub` and `<name>_h264`).

Then `docker restart frigate`. To change a camera's settings with the installer, remove it first and add it again.

### I'm locked out of the Frigate web page

Add this to `~/frigate/config/config.yml`:

```yaml
auth:
  reset_admin_password: true
```

Restart (`docker restart frigate`), then get the new password from the log:

```bash
docker logs frigate 2>&1 | grep -A1 "Reset admin password"
```

Log in, set a new password under **Settings > Users**, then remove those two lines from `config.yml` and restart again.

---

## Security

- **`config.yml` contains your camera passwords** (inside the `go2rtc` stream addresses). Keep it private:

  ```bash
  chmod 600 ~/frigate/config/config.yml
  ```

  The installer keeps the file's permissions when it saves, and its backups are private (`600`) too. If you copy the settings file or backups elsewhere, treat them like passwords. The installer warns you if `config.yml` is readable by other users.
- **Never expose Frigate to the internet.** Don't set up port forwarding on your router for **8971**, **8554**, **8555** or **5000**. For viewing away from home, use a VPN to your home network instead.
- **Port 5000 stays off.** It is Frigate's unauthenticated access port and is commented out in `docker-compose.yml` on purpose.
- **Port 8554 (RTSP restream) has no password by default.** Anyone on your home network can watch camera streams through it. If you don't use other apps that read these streams, you can remove the `"8554:8554"` line from `docker-compose.yml` and run `docker compose up -d`. Frigate itself doesn't need it.
- **Docker bypasses the `ufw` firewall** for published ports, so Frigate's ports are reachable on your home network even if `ufw` is on. Another reason never to forward them from your router.
- **Change the default Ubuntu password** (`frigate` / `Temp_123!`) at first login with `passwd`. It is published in this guide. Also change the password on every camera and on Frigate's `admin` user.
- Being in the `docker` group is equivalent to admin access on the box. Only add trusted users to it.
- Manifests with plain-text passwords should be `chmod 600`, or use `password_env` instead.
- Keep camera firmware up to date.
