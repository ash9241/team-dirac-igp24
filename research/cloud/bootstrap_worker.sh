#!/bin/bash
# IGP24 worker bootstrap: fresh Ubuntu 24.04 -> full generation stack
set -e
sudo apt-get update -qq
sudo apt-get install -y -qq build-essential python3 python3-numpy python3-sympy curl m4 sqlite3 gap gap-transgrp > /dev/null
mkdir -p ~/build && cd ~/build
if ! command -v ~/.local/bin/gp >/dev/null; then
  curl -sSL -o gmp.tar.xz https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz && tar xf gmp.tar.xz
  (cd gmp-6.3.0 && ./configure --prefix="$HOME/.local" >/dev/null && make -j"$(nproc)" >/dev/null 2>&1 && make install >/dev/null)
  curl -sSL -o pari.tar.gz https://pari.math.u-bordeaux.fr/pub/pari/unix/pari-2.17.2.tar.gz && tar xf pari.tar.gz
  (cd pari-2.17.2 && ./Configure --prefix="$HOME/.local" --with-gmp="$HOME/.local" >/dev/null 2>&1 && make -j"$(nproc)" gp >/dev/null 2>&1 && make install >/dev/null 2>&1)
fi
~/.local/bin/gp --version 2>&1 | head -1
gap -q -c 'if LoadPackage("transgrp") = fail then Error("transgrp unavailable"); fi; Print(GAPInfo.Version,"\n"); QUIT;'
mkdir -p ~/.local/share/pari && cd ~/.local/share/pari
curl -sSL -o galdata.tgz https://pari.math.u-bordeaux.fr/pub/pari/packages/galdata.tgz && tar xzf galdata.tgz && mv -f data/galdata . 2>/dev/null || true
echo "BOOTSTRAP DONE"
