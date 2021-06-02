### NOTICE

This is a fork from https://github.com/nicolai-budico/dockerhosts
I needed a few little changes for use with a local docker testbed.

### Dev

Tested on Fedora 33; it "should" work ok with any linux distro using systemd-resolved but ymmv.

### About

This tool is a linux service that provides DNS for docker containers. You may reach your containers by hostname, e.g.

```
$ docker network create --driver=bridge --subnet=172.16.0.0/24 defaultset.lan
d5be3f63c626820031533793adcc77bc895dee76f00a127d60a35a2db233eb45

... (presuming you have an image wikimedia-dumps/snapshot:latest, as I do, already built :-P)

$ docker create --rm --name defaultset-snapshot-02 --net defaultset.lan --domainname defaultset.lan -it wikimedia-dumps/snapshot:latest
bd4c0c312045227893b78eb1ca73c2d878e5b88c62b0dc24331bedc8855b1c4b
$ docker start defaultset-snapshot-02
defaultset-snapshot-02
$ dig  defaultset-snapshot-02.defaultset.lan

; <<>> DiG 9.11.32-RedHat-9.11.32-1.fc33 <<>> defaultset-snapshot-02.defaultset.lan
;; global options: +cmd
;; Got answer:
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 8567
;; flags: qr rd ra; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 1

;; OPT PSEUDOSECTION:
; EDNS: version: 0, flags:; udp: 65494
;; QUESTION SECTION:
;defaultset-snapshot-02.defaultset.lan. IN A

;; ANSWER SECTION:
defaultset-snapshot-02.defaultset.lan. 0 IN A	172.16.0.4

;; Query time: 0 msec
;; SERVER: 127.0.0.53#53(127.0.0.53)
;; WHEN: Τρι Ιουν 01 14:37:49 EEST 2021
;; MSG SIZE  rcvd: 82

$ ping defaultset-snapshot-02.defaultset.lan
PING defaultset-snapshot-02.defaultset.lan (172.16.0.4) 56(84) bytes of data.
64 bytes from e9d7a5950ee7 (172.16.0.4): icmp_seq=1 ttl=64 time=0.152 ms
64 bytes from e9d7a5950ee7 (172.16.0.4): icmp_seq=2 ttl=64 time=0.064 ms
^C
--- defaultset-snapshot-02.defaultset.lan ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1001ms
rtt min/avg/max/mdev = 0.064/0.108/0.152/0.044 ms
```

### Requirements

1. Python 3
2. Dnsmasq
3. Docker
4. docker-py (docker SDK for python)

### Install
```
sudo ./install.sh
```
This command will install `dockerhosts` as a systemd service, along with the executable and the configuration file.

### Uninstall
```
sudo ./uninstall.sh
```
This command will remove `the dockerhosts` service and all associated files.

### Configuration

This tool uses `dnsmasq` to provide associations between container hosnames and theirs IP addresses.
By default dnsmasq listens on 127.0.0.54:53, to make this DNS available to the system,
add IP 127.0.0.54 to the property `DNS` in file `/etc/systemd/resolved.conf`:
```
cat /etc/systemd/resolved.conf
#  This file is part of systemd.
#
#  systemd is free software; you can redistribute it and/or modify it
#  under the terms of the GNU Lesser General Public License as published by
#  the Free Software Foundation; either version 2.1 of the License, or
#  (at your option) any later version.
#
# Entries in this file show the compile time defaults.
# You can change settings by editing this file.
# Defaults can be restored by simply deleting this file.
#
# See resolved.conf(5) for details

[Resolve]
DNS=127.0.0.54
#FallbackDNS=
#Domains=
#LLMNR=yes
#MulticastDNS=yes
#DNSSEC=no
#Cache=yes
#DNSStubListener=udp
```

### How it works

The script starts dnsmasq, which listens on 127.0.0.54, port 53, and reads the file /var/run/docker-hosts/hosts for name resolution.

In the meantime, the script polls docker every two seconds (configurable) via the docker api, and retrieves the names and ids of all containers, adding them to the above file. The script also writes the names qualified by the network name to which each container is attached.

When the list of containers changes, containers missing from the new list will be removed from the file, and containers new in the list will be checked for their IPS, and new entries added. The dnsmasq process will also be sent a SIGHUP to clear its cache; there is no mechanism for dnsmasq to drop entries removed from the file, and this is by design. See https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=798653

When there are no containers running, the file will be empty except for a header line containing the name of the file.

If the docker process is not running when this service starts, no hosts file will be created until the docker daemon finally starts up. It can take up to 60 seconds (configurable) for this service to notice that the docker daemon has become available.

### Recommended use

Your resolver will likely not route resolution of unqualified hostnames anywhere useful. It is recommended that you add your containers to a network with a name ending in a reserved TLD such as .lan, for example "test.lan". In this case, all entries in /var/run/docker-hosts/hosts will be listed in short form but also with <containername>.test.lan and <containerid>.test.lan as aliases. Once you have edited /etc/systemd/resolved.conf to add the new dnsmasq process to the DNS entry as described above in 'Configuration', you should be able to reference your containers from the local host by providing the fully qualified name, either based on the container name or the container id.

### Issues

I'm sure there are many. In particular, nslookup does NOT work with the fqdn or with the short name either, although dig and ssh and probably most everything else does just fine.

Currently dns doesn't resolve for the short name of a container, from outside of the container. Because I expect to have multiple test docker networks in play at once, and adding each of these as a search domain to systemd-resolved would be tedious, I'm ignoring this for now.
