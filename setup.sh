#!/bin/sh
# NewProjectX RE workbench — reproducible setup (Debian 13, user-space, no root)
set -e

mkdir -p ~/tools/bin ~/re-lab

# --- Java 25 (Ghidra 12.2 requires min JDK 25) ---
command -v java >/dev/null || {
  cd /tmp
  gh release download --repo adoptium/temurin25-binaries \
    --pattern "OpenJDK25U-jdk_x64_linux_hotspot_*.tar.gz" --clobber
  tar xzf OpenJDK25U-jdk_x64_linux_hotspot_*.tar.gz
  mv jdk-25* ~/tools/jdk-25
}

export JAVA_HOME=$HOME/tools/jdk-25
export PATH=$JAVA_HOME/bin:$HOME/tools/bin:$PATH

# --- Ghidra + GhidraMCP ---
[ -d ~/tools/ghidra_12.2_PUBLIC ] || {
  cd /tmp
  gh release download --repo NationalSecurityAgency/ghidra \
    --pattern "ghidra_*_PUBLIC_*.zip" --clobber
  unzip -q ghidra_*_PUBLIC_*.zip -d ~/tools/
}
[ -d ~/tools/ghidra_12.2_PUBLIC/Extensions/Ghidra/GhidraMCP-release-2-0 ] || {
  cd /tmp
  gh release download --repo LaurieWired/GhidraMCP --pattern "GhidraMCP-release-*.zip" --clobber
  unzip -q GhidraMCP-release-*.zip -d ~/tools/ghidra_12.2_PUBLIC/Extensions/Ghidra/
}
pip3 install -q ghidra-mcp

# --- jadx + Apktool ---
[ -x ~/tools/bin/jadx ] || {
  cd /tmp
  gh release download --repo skylot/jadx --pattern "jadx-*.zip" --clobber
  unzip -q jadx-*.zip -d ~/tools/jadx
  ln -sf ~/tools/jadx/bin/jadx ~/tools/bin/jadx
  ln -sf ~/tools/jadx/bin/jadx-gui ~/tools/bin/jadx-gui
}
[ -f ~/tools/apktool.jar ] || {
  cd /tmp
  gh release download --repo iBotPeaches/Apktool --pattern "apktool_*.jar" --clobber
  mv apktool_*.jar ~/tools/apktool.jar
  printf '#!/bin/sh\nexec java -jar "$HOME/tools/apktool.jar" "$@"\n' > ~/tools/bin/apktool
  chmod +x ~/tools/bin/apktool
}

# --- Python tools ---
pip3 install -q frida-tools angr mitmproxy

# --- ILSpy (needs DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1, no libicu on host) ---
[ -x ~/tools/ilspy/ILSpy ] || {
  cd /tmp
  gh release download --repo icsharpcode/ILSpy --pattern "ILSpy_linux-x64_*.zip" --clobber
  unzip -q ILSpy_linux-x64_*.zip -d ~/tools/ilspy
  chmod +x ~/tools/ilspy/ILSpy
  ln -sf ~/tools/ilspy/ILSpy ~/tools/bin/ilspy
}

# --- rizin (static) ---
[ -x ~/tools/bin/rizin ] || {
  cd /tmp
  gh release download --repo rizinorg/rizin --pattern "rizin-*-static-x86_64.tar.xz" --clobber
  python3 - <<'PY'
import lzma, tarfile, io, glob
f = glob.glob('/tmp/rizin-*-static-x86_64.tar.xz')[0]
with lzma.open(f) as fh:
    data = fh.read()
tarfile.open(fileobj=io.BytesIO(data)).extractall('/tmp/rizin-extract')
PY
  mkdir -p ~/tools/rizin && cp -r /tmp/rizin-extract/* ~/tools/rizin/
  ln -sf ~/tools/rizin/bin/rizin ~/tools/bin/rizin
}

# --- radare2 (source build; needs shims for missing pkg-config/patch) ---
[ -x ~/tools/r2/bin/radare2 ] || {
  mkdir -p ~/tools/shims
  printf '#!/bin/sh\nexit 1\n' > ~/tools/shims/pkg-config
  cat > ~/tools/shims/patch <<'EOF'
#!/bin/sh
dir=""; files=""
while [ $# -gt 0 ]; do
  case "$1" in
    -d) dir="$2"; shift 2 ;;
    -p*) shift ;;
    *) files="$files $1"; shift ;;
  esac
done
[ -n "$dir" ] && cd "$dir"
exec git apply $files
EOF
  chmod +x ~/tools/shims/*
  cd /tmp
  [ -d radare2 ] || git clone --depth 1 https://github.com/radareorg/radare2
  cd radare2 && PATH=$HOME/tools/shims:$PATH ./sys/install.sh --prefix=$HOME/tools/r2
}

# --- ImHex (deb extract) ---
[ -x ~/tools/imhex/usr/bin/imhex ] || {
  cd /tmp
  gh release download --repo WerWolv/ImHex --pattern "imhex-*-Ubuntu-24.04-x86_64.deb" --clobber
  mkdir -p imhex-deb && (cd imhex-deb && ar x /tmp/imhex-*-Ubuntu-24.04-x86_64.deb)
  cd imhex-deb && zstd -d data.tar.zst -o data.tar
  mkdir -p ~/tools/imhex && tar xf data.tar -C ~/tools/imhex
}

# --- REA + Craft Anything ---
command -v npm >/dev/null && npm install -g rea-agents
[ -x ~/tools/bin/craft-anything-cli ] || {
  cd /tmp
  gh release download --repo storytold/craft-anything \
    --pattern "craft-anything-*-linux-x86_64.AppImage" --clobber
  chmod +x craft-anything-*-linux-x86_64.AppImage
  ./craft-anything-*-linux-x86_64.AppImage --appimage-extract > /dev/null
  cp squashfs-root/usr/bin/craft-anything-cli ~/tools/bin/
  cp squashfs-root/usr/bin/craft-anything ~/tools/bin/
}

# --- Reference lists ---
cd ~/re-lab
for repo in zhuyansen/awesome-ai-reverse-engineering morluto/awesome-reverse-engineering \
            morluto/rea storytold/craft-anything LaurieWired/GhidraMCP; do
  d=$(basename $repo)
  [ -d $d ] || git clone --depth 1 https://github.com/$repo
done

# --- Shell env ---
grep -q "tools/jdk-25" ~/.bashrc || cat >> ~/.bashrc <<'EOF'
export JAVA_HOME=$HOME/tools/jdk-25
export DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
export PATH=$HOME/tools/jdk-25/bin:$HOME/tools/bin:$HOME/tools/r2/bin:$HOME/tools/rizin/bin:$HOME/.npm-global/bin:$HOME/.local/bin:$PATH
alias imhex=$HOME/tools/imhex/usr/bin/imhex
EOF

echo "Setup complete. Tools: ghidra, jadx, apktool, frida, angr, mitmproxy, ilspy, rizin, radare2, imhex, rea, craft-anything-cli"
