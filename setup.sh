#!/bin/bash
# Setup script for NSO GameCube Controller Bridge

set -e

echo "Setting up NSO GameCube Controller Bridge..."
echo ""

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "✓ Setup complete!"
echo ""
echo "To use the tools, activate the virtual environment first:"
echo "  source venv/bin/activate"
echo ""
echo "Then run:"
echo "  python3 diagnose.py        # HID-level diagnostic"
echo "  python3 usb_explore.py     # USB-level exploration"
echo "  python3 nso_gc_bridge.py   # Main bridge script"
