#!/bin/sh

set -e

if [ $EUID -ne 0 ] ; then
    echo "Superuser priveleges required"
    exit 1
fi

echo "Stopping service"
launchctl stop dockerhosts

echo "Disabling service"
launchctl disable dockerhosts

if [ -f "/Library/LaunchDaemons/dockerhosts.plist" ] ; then
    echo "Uninstall file /Library/LaunchDaemons/dockerhosts.plist"
    rm "/Library/LaunchDaemons/dockerhosts.plist"
fi

if [ -f "/usr/local/bin/dockerhosts" ] ; then
    echo "Uninstall file /usr/bin/dockerhosts"
    rm "/usr/local/bin/dockerhosts"
fi

if [ -f "/etc/dockerhosts.conf.json" ] ; then
    echo "Uninstall file /etc/dockerhosts.conf.json"
    rm "/etc/dockerhosts.conf.json"
fi

echo "Done."
