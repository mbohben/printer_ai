#!/bin/bash

sudo systemctl stop printer-ai
sudo systemctl stop printer-ai-ui

sudo systemctl disable printer-ai
sudo systemctl disable printer-ai-ui

sudo rm /etc/systemd/system/printer-ai.service
sudo rm /etc/systemd/system/printer-ai-ui.service

sudo systemctl daemon-reload

echo "Uninstalled Printer AI"
