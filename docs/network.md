# Network access

## Open the port

swiv listens on TCP 6090 (`port` in the config). Allow it if a firewall is
on.

Ubuntu (ufw):

```
sudo ufw allow 6090/tcp
```

Fedora and RHEL (firewalld):

```
sudo firewall-cmd --add-port=6090/tcp --permanent
sudo firewall-cmd --reload
```

## Find the address

```
hostname -I
```

The client opens `http://<one of these addresses>:6090/`.

## Reaching it from outside your network

Traffic is plain HTTP, so do not forward the port on your router. Put the server
and the client on a VPN instead, for example Tailscale (install it on both and
log in to the same account), and use the server's VPN address. To refuse
everything else, set `bind` in the config to that VPN address and run `make
restart`.

## Checking that it works

On the server:

```
make status
make logs
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:6090/login   # 200
```

If the client cannot connect but the curl above prints 200, the firewall or the
network is in the way, not swiv.

Back to the [README](../README.md).
