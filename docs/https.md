# HTTPS and the installable app

swiv serves plain HTTP by default. Setting a certificate in the config turns
on HTTPS, which does two things: the login password can no longer be sniffed
on the network, and the phone browser lets swiv install as an app (the
`install` button appears; over HTTP only a manual "Add to Home screen"
shortcut is possible).

Related: [configuration](configuration.md), [network access](network.md),
[client setup](client.md).

## Turn it on

Set both keys in the config and restart:

```json
"cert": "/home/you/.config/swiv/swiv.pem",
"key": "/home/you/.config/swiv/swiv-key.pem"
```

The startup line then reads `https://`, and plain HTTP on the port is gone,
so change the phone URL to `https://` too.

## Getting a certificate

### Without a domain (local network)

mkcert can issue certificates for IP addresses, so no name is needed.

```
sudo apt install mkcert && mkcert -install
mkcert 192.168.1.20        # the desktop address the phone uses
```

That writes `192.168.1.20.pem` and `192.168.1.20-key.pem`. swiv finds the
mkcert root by itself (no config needed): open `https://<desktop>:6090` on
the phone, proceed past the warning once, tap `install root certificate` on
the login page and install the downloaded file as a CA certificate (Android:
Settings, Security, Install a certificate; the warning is expected). Every
further device repeats this one-time step.

### With your own domain

Point a DNS record at the desktop and use certbot with the DNS challenge:

```
certbot certonly --manual --preferred-challenges dns -d swiv.example.com
```

Certificates are valid for the name, so the phone must use
`https://swiv.example.com:6090`, not the IP. Renewal is every 90 days.

### Tailscale

If the phone reaches the desktop through Tailscale, `tailscale cert` issues
a certificate for the tailnet name; a domain of your own is not needed.

## Offline

An installed app opens even when the desktop is not reachable (VPN off,
another network). It then shows a "connect to the same network or VPN"
notice instead of a browser error, and works again once the route is back.
