#!/usr/bin/env bash
# ============================================================
#  actualizar.sh — Scrapea, exporta el JSON y lo publica.
#  Uso:   ./actualizar.sh
#  Cron:  ver README (sección de automatización en Linux).
# ============================================================
set -euo pipefail

# Ir a la carpeta del script (funciona aunque cron lo llame desde otro sitio)
cd "$(dirname "$(readlink -f "$0")")"

# Usar el Python del entorno virtual si existe; si no, el del sistema.
if [ -x "venv/bin/python" ]; then
    PY="venv/bin/python"
elif [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY="python3"
fi

echo "[1/3] Ejecutando el monitor (scraping + exportación)..."
"$PY" monitor_secihti.py --once

echo
echo "[2/3] Subiendo docs/data.json a GitHub..."
git add docs/data.json
if git diff --cached --quiet; then
    echo "  No había cambios que subir. Todo al día."
else
    git commit -m "Actualiza convocatorias ($(date '+%Y-%m-%d %H:%M'))"
    git push
    echo "  Publicado."
fi

echo
echo "[3/3] Listo. La web se actualizará en 1-2 minutos:"
echo "  https://TU-USUARIO.github.io/convo-scraper/"
