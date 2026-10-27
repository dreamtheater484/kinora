#!/bin/sh
# Python caches bytecode next to the program when it runs as root (as a normal user it cannot write there). Those
# caches are not in the package, so remove them, or /opt/kinora stays behind. The data in home folders is kept.
rm -rf /opt/kinora/__pycache__ /opt/kinora/tools/__pycache__
