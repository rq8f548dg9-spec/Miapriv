#!/usr/bin/env bash
# Avviatore per macOS: i file .command partono con un doppio clic nel Finder.
# Fa esattamente quello che fa avvia.sh, che sta nella stessa cartella.
cd "$(dirname "$0")"
exec ./avvia.sh
