@echo off
REM ============================================================
REM  actualizar.bat - Scrapea, exporta el JSON y lo publica.
REM  Uso: doble clic, o programar en el Programador de tareas.
REM ============================================================

cd /d "%~dp0"

echo [1/3] Ejecutando el monitor (scraping + exportacion)...
python monitor_secihti.py --once
if errorlevel 1 (
    echo ERROR: el monitor fallo. Revisa el mensaje de arriba.
    pause
    exit /b 1
)

echo.
echo [2/3] Subiendo docs\data.json a GitHub...
git add docs/data.json
git commit -m "Actualiza convocatorias (%date% %time%)" 2>nul
if errorlevel 1 (
    echo   No habia cambios que subir. Todo al dia.
) else (
    git push
    echo   Publicado.
)

echo.
echo [3/3] Listo. La web se actualizara en 1-2 minutos:
echo   https://TU-USUARIO.github.io/convo-scraper/
echo.
pause
