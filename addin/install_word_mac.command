#!/bin/sh
# Copy the add-in manifest into Word for Mac's add-in folder. Run from Terminal.app
# (double-click this file): macOS only lets apps you allow write into Word's container.
DEST="$HOME/Library/Containers/com.microsoft.Word/Data/Documents/wef"
mkdir -p "$DEST" && cp "$(dirname "$0")/manifest.xml" "$DEST/" && echo "DONE: add-in installed in $DEST" \
  && echo "Now quit Word (Cmd+Q), reopen it, and look under Home > Add-ins > Developer Add-ins."
