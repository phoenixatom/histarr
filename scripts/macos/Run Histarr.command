#!/bin/zsh
# SPDX-License-Identifier: GPL-3.0-only
set -u

PROJECT_DIR="${0:A:h:h:h}"
OUTPUT_DIR="$PROJECT_DIR/local-data/histarr-exports"
cd "$PROJECT_DIR" || exit 1

PYTHONPATH="$PROJECT_DIR/src" python3 -m histarr --output-dir "$OUTPUT_DIR"
STATUS=$?

echo
if [[ $STATUS -eq 0 ]]; then
  echo "Done. Your private files are in $OUTPUT_DIR."
else
  echo "Histarr stopped with status $STATUS. Review the message above."
fi
echo "Press Return to close this window."
read -r
exit $STATUS
