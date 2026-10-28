@echo off
chcp 65001 > nul
title Actualizador de Precios DK - Hardware Deals
color 0B

echo ===============================================================================
echo     ACTUALIZADOR MANUAL DE PRECIOS (MONITORES, GPUS Y DESKTOPS EN DK)
echo ===============================================================================
echo.
echo  Iniciando escaneo en tiempo real de PriceRunner DK y Prisjagt DK...
echo  (Por favor espera ~1 minuto para respetar los limites de peticion respetuosos)
echo.

cd /d "C:\Users\hugod\.gemini\antigravity\scratch\monitor-tracker"

python tracker.py

if %ERRORLEVEL% NEQ 0 (
    color 0C
    echo.
    echo [ERROR] Hubo un problema al ejecutar el tracker. Revisa logs\ para mas detalles.
    pause
    exit /b %ERRORLEVEL%
)

color 0A
echo.
echo ===============================================================================
echo  [OK] Precios actualizados y sincronizados con Vercel correctamente!
echo  Abriendo cuadro de mando en tu navegador...
echo ===============================================================================
echo.

start "" "https://monitor-deals-dk.vercel.app/"

timeout /t 5 > nul
