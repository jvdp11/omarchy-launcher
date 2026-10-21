#!/bin/bash
# An example script command (Raycast's format). Copy it to
# ~/.config/omarchy/launcher/commands/ and make it your own.
#
# @raycast.schemaVersion 1
# @raycast.title IP addresses
# @raycast.mode fullOutput
# @raycast.icon 󰩟
# @raycast.packageName Network
# @raycast.description This machine's addresses, per interface

ip -brief address | grep -v '^lo ' | awk '{ printf "%-16s %-8s %s\n", $1, $2, $3 }'
echo
echo "Default route: $(ip route show default | awk '{print $3 " via " $5; exit}')"
