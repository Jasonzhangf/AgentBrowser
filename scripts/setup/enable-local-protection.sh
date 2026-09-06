#!/bin/sh
set -eu
# Relative hooks protect each checkout using its own versioned hook files.
git config core.hooksPath .githooks
