#!/bin/bash
set -e

# =====================================================
# UNIVERSAL ISDA CSA EXTRACTOR – FULL REPO BUILDER
# Repo: https://github.com/Pyligent/csa_extraction.git
# Spec: A/B/C/D – Full haircuts.matrix[] expansion
# =====================================================

REPO_DIR="csa_extraction"
REMOTE_URL="https://github.com/Pyligent/csa_extraction.git"

echo "Building Universal CSA Extractor in: $REPO_DIR"

# Clean & create
rm -rf "$REPO_DIR"
mkdir -p "$REPO_DIR"/{extractor/parsers,tests/fixtures}
cd "$REPO_DIR"

# === 1. README.md ===
cat > README.md << 'EOF'
# Universal ISDA CSA Extractor

Extracts **1994/2016 ISDA Credit Support Annex** from **HTML, PDF, DOCX**.

**Full `haircuts.matrix[]` expansion** – every Annex A/B/C row → one normalized JSON row.

---

## Features

| Feature | Status |
|-------|--------|
| HTML (EDGAR) | Supported |
| PDF | Supported (`pdfplumber`) |
| DOCX | Supported (`python-docx`) |
| 1994 & 2016 VM CSA | Supported |
| **Full Annex A/B/C expansion** | 100+ rows |
| Regime normalization | S&P, Moody's First/Second Trigger |
| Abstain logic | Only explicit values |
| CLI + REST API | Supported |
| Docker-ready | Supported |

---

## Install

```bash
pip install -r requirements.txt