#!/bin/sh

set -e

if [ $EUID -ne 0 ] ; then
    echo "Superuser privileges required"
    exit 1
fi

WORKDIR="$(dirname "$(readlink -f "${0}")")"

echo "Install launchd file /Library/LaunchDaemons/dockerhosts.plist"
cp "${WORKDIR}/dockerhosts.plist" "/Library/LaunchDaemons/dockerhosts.plist"

echo "Install file /usr/local/bin/dockerhosts"
cp "${WORKDIR}/dockerhosts.py" "/usr/local/bin/dockerhosts"

echo "Install file /etc/dockerhosts.conf.json"
cp "${WORKDIR}/dockerhosts.conf.json.mac" "/usr/local/etc/dockerhosts.conf.json"

echo "Load service: launchctl load /Library/LaunchDaemons/dockerhosts.plist"
launchctl load /Library/LaunchDaemons/dockerhosts.plist

echo "Enable service: launchctl enable system/dockerhosts"
launchctl enable system/dockerhosts

echo "Starting service: launchctl start dockerhosts"
launchctl start system/dockerhosts

echo "Checking the results... Please review:"
launchctl print system/dockerhosts

