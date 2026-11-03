# Server on Windows

Untested. Windows has no gnome-terminal to read, so swiv shows a **screen
card** instead: the whole monitor as an image, with taps and keys sent to the
real mouse and keyboard. Browser cards (Chrome) and the file card work as on
Linux. Login uses a separate swiv password, because Windows has no PAM.

## 1. Install Python

Install Python 3.8 or newer from python.org and tick "Add python.exe to PATH".

## 2. Install swiv

In PowerShell, inside the swiv folder:

```
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python server.py --set-password
```

The config file is `%USERPROFILE%\.config\swiv\config.json`. Set
`files_root` there to the folder the file card should show.

## 3. Run it

```
.venv\Scripts\python server.py
```

It must run in your desktop session (not as a service), or it cannot see the
screen. Stop it with Ctrl+C.

## 4. Open the port

In an administrator PowerShell:

```
netsh advfirewall firewall add rule name=swiv dir=in action=allow `
  protocol=TCP localport=6090
```

Find the address with `ipconfig` (the IPv4 address of your network adapter).
Read [network access](network.md) about using a VPN instead of opening the
port to everyone.

## 5. Start at login (optional)

In PowerShell, inside the swiv folder. `pythonw` runs without a console
window:

```
$py = "$PWD\.venv\Scripts\pythonw.exe"; $srv = "$PWD\server.py"
schtasks /Create /SC ONLOGON /TN swiv /TR "`"$py`" `"$srv`""
```

Remove it with `schtasks /Delete /TN swiv /F`.

## Browser cards

Chrome is found in its usual install folders. If it is elsewhere, set
`chrome_bin` in the config to the full path of `chrome.exe`.

## Limits

- Text on the screen card is small: the whole monitor is shrunk to the phone
  width.
- The lock screen and administrator (UAC) prompts run on a separate desktop
  that swiv can neither see nor click.

Back to the [README](../README.md).
