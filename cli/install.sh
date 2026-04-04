#!/bin/bash
# Install RP outreach CLI tools
# Run once: bash install.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Make scripts executable
chmod +x "$SCRIPT_DIR/rp-prospect"
chmod +x "$SCRIPT_DIR/rp-pipeline"
chmod +x "$SCRIPT_DIR/rp-update"
chmod +x "$SCRIPT_DIR/rp-draft"
chmod +x "$SCRIPT_DIR/rp-followup"

# Create symlinks in /usr/local/bin
for cmd in rp-prospect rp-pipeline rp-update rp-draft rp-followup; do
  if [ -L "/usr/local/bin/$cmd" ]; then
    rm "/usr/local/bin/$cmd"
  fi
  ln -s "$SCRIPT_DIR/$cmd" "/usr/local/bin/$cmd"
  echo "✓ Linked: $cmd"
done

echo ""
echo "Done. Available commands:"
echo "  rp-prospect  — Create a new prospect"
echo "  rp-pipeline  — View pipeline status"
echo "  rp-update    — Update prospect status"
echo "  rp-draft     — Draft and open email in Gmail"
echo "  rp-followup  — Check follow-ups due"