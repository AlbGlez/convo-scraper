# Monitor de Convocatorias SECIHTI + Morelos

Bot autónomo que vigila continuamente las convocatorias abiertas de SECIHTI
(laboratorios nacionales, ciencia básica y de frontera, desarrollo tecnológico,
proyectos de investigación, ejes estratégicos) y fuentes de C&T de institutos
de Morelos, filtrando por tus líneas: agua / hidroagrícola, IA, teledetección,
SIG, recursos hídricos.

Solo notifica lo **nuevo** (deduplicación persistente en SQLite).

## Instalación

En Debian bookworm (Python gestionado por el sistema) conviene usar un entorno
virtual — el `actualizar.sh` lo detecta automáticamente si se llama `venv`:

```bash
sudo apt install python3-venv git       # si no los tienes
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

(Alternativa rápida sin venv: `pip install -r requirements.txt --break-system-packages`.)

## Uso

```bash
python monitor_secihti.py --once           # scrapea, guarda y exporta docs/data.json
python monitor_secihti.py --loop 3600       # revisa cada hora (y exporta en cada vuelta)
python monitor_secihti.py --list            # muestra todo lo almacenado (★ = relevante)
python monitor_secihti.py --list --relevant # solo lo marcado como relevante
python monitor_secihti.py --reset           # borra la base de datos y empieza de cero
python monitor_secihti.py --reeval          # re-clasifica lo guardado (solo SECIHTI)
python monitor_secihti.py --export-json     # regenera docs/data.json sin scrapear
python monitor_secihti.py --once --no-export # scrapea pero NO toca el JSON
```

`--once` ahora también **exporta `docs/data.json`** al terminar, que es lo que
consume la interfaz web (ver más abajo). Si no quieres ese comportamiento, usa
`--no-export`.

En la primera ejecución se crea `config.json`. Edítalo para:

- **keywords**: palabras que marcan una convocatoria como relevante (sin acentos, minúsculas).
- **morelos_sources** / **search_sources**: páginas a vigilar (nombre, URL,
  `must_contain`, `exclude`). Ajusta si alguna institución cambia su maquetado.
- **email** / **webhook**: canales de notificación (ambos opcionales).
  - Email Gmail: usa una *App Password* de 16 dígitos, no tu contraseña normal.
  - Webhook: URL de Slack / Discord / Teams / n8n.

## Interfaz web (Federal / Estatal)

El bot genera `docs/data.json`, y `docs/index.html` es una página que lo muestra
en dos pestañas: **Federal · SECIHTI** y **Estatal · Morelos**. Cada convocatoria
aparece como una tarjeta con su fuente, fecha de cierre (en rojo si cierra en ≤14
días) y una estrella ★ si es relevante para tu perfil. Incluye buscador y un
filtro "solo relevantes". Es de solo lectura.

> **Importante:** la página usa `fetch()` para leer `data.json`, y eso **no
> funciona abriendo el HTML con doble clic** (protocolo `file://`). Necesita
> servirse por HTTP. Para probarla en tu PC:
> ```bash
> cd docs
> python -m http.server 8000
> # abre http://localhost:8000 en el navegador
> ```
> En GitHub Pages se sirve por HTTP automáticamente, así que ahí funciona sin más.

## Publicar la web gratis (GitHub Pages) — enfoque híbrido

El scraping corre en **tu PC** (donde SECIHTI no bloquea la IP) y GitHub solo
aloja la web. Pasos, una sola vez:

1. **Crea un repositorio público** en GitHub, por ejemplo `convo-scraper`.
2. En tu PC, dentro de la carpeta del proyecto:
   ```bash
   git init
   git add .
   git commit -m "Monitor de convocatorias + interfaz web"
   git branch -M main
   git remote add origin https://github.com/TU-USUARIO/convo-scraper.git
   git push -u origin main
   ```
   (El `.gitignore` ya evita subir `convocatorias.db` y `config.json`, que se
   quedan solo en tu PC. La base de datos y tus credenciales nunca salen de casa.)
3. En GitHub: **Settings → Pages**. En "Source" elige **Deploy from a branch**,
   rama **main**, carpeta **/docs**. Guarda.
4. En 1–2 minutos tu web estará en:
   `https://TU-USUARIO.github.io/convo-scraper/`

### Mantenerla actualizada

Cada vez que quieras refrescar las convocatorias, ejecuta el script que hace las
tres cosas (scrapea, exporta el JSON y lo sube a GitHub):

- **Linux / servidor Debian:** `./actualizar.sh`
- **Windows:** `actualizar.bat`

Edita el script y pon tu usuario en la URL final. También puedes correr a mano:
```bash
python3 monitor_secihti.py --once
git add docs/data.json
git commit -m "Actualiza convocatorias"
git push
```

### Automatizarlo del todo (Linux / Debian bookworm)

Tienes dos opciones. **cron** es la más simple:

```bash
crontab -e
# añade esta línea para correr todos los días a las 8:00
0 8 * * * /ruta/al/proyecto/actualizar.sh >> /ruta/al/proyecto/monitor.log 2>&1
```

**systemd timer** es más robusto para un servidor (mejor logging, reintentos).
Crea `/etc/systemd/system/convo.service`:
```ini
[Unit]
Description=Monitor de convocatorias SECIHTI/Morelos
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=TU_USUARIO
WorkingDirectory=/ruta/al/proyecto
ExecStart=/ruta/al/proyecto/actualizar.sh
```
Y `/etc/systemd/system/convo.timer`:
```ini
[Unit]
Description=Ejecuta el monitor de convocatorias a diario

[Timer]
OnCalendar=*-*-* 08:00:00
Persistent=true

[Install]
WantedBy=timers.target
```
Actívalo:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now convo.timer
systemctl list-timers convo.timer     # verifica la próxima ejecución
```
La ventaja de `Persistent=true`: si el servidor estaba apagado a las 8:00, la
tarea se ejecuta al encender, sin saltarse el día.

### Credenciales de git en el servidor

Para que `git push` funcione sin pedir contraseña cada vez, en el servidor
configura una de estas dos:
- **Token en HTTPS:** genera un *Personal Access Token* en GitHub (permiso
  `repo`) y guárdalo con `git config --global credential.helper store` (la
  primera vez que hagas push te lo pedirá y lo recordará). Sencillo pero el
  token queda en texto plano en `~/.git-credentials`.
- **Llave SSH (recomendado en servidor):** `ssh-keygen`, sube la pública a
  GitHub (Settings → SSH keys), y usa la URL SSH del repo
  (`git@github.com:TU-USUARIO/convo-scraper.git`). Más seguro y sin tokens que
  caduquen.

## Nota sobre el WAF de SECIHTI (importante en servidor)

El portal SECIHTI usa un firewall que puede devolver **403** a IPs de
datacenter/nube. Esto es clave ahora que corres en servidor: si tu Debian es un
**VPS** (DigitalOcean, AWS, etc.) es probable que reciba 403; si es un servidor
en la **red del IMTA** o similar, debería funcionar como desde una PC doméstica.

Compruébalo antes de automatizar, con una sola línea en el servidor:
```bash
curl -s -o /dev/null -w "%{http_code}\n" https://secihti.mx/estatus-convocatoria/abierta/
```
Si responde `200`, todo bien. Si responde `403`, SECIHTI está bloqueando esa IP;
opciones:

1. Corre el scraping en una máquina de la red del IMTA (o tu PC) y deja que el
   servidor solo publique. El diseño ya es híbrido, así que esto encaja.
2. Enruta las peticiones por un proxy que no sea de datacenter (añádelo en
   `http_get`).
3. Como último recurso, sustituye `requests` por Playwright headless para esa
   fuente (más pesado, pero a veces evade el WAF).

Las fuentes de Morelos (morelos.gob.mx, IMTA, UTEZ) no tienen este problema; el
403 afecta solo a SECIHTI.

## Fuentes vigiladas por defecto

**SECIHTI** — `https://secihti.mx/estatus-convocatoria/abierta/` (todas las páginas).

**Morelos, con listado HTML** (`morelos_sources`, scraping directo):
- **CCyTEM** — dos rutas del portal Webflow en `morelos.gob.mx`:
  - `/sitios/convocatorias/consejo-de-ciencia-y-tecnologia-del-estado-de-morelos`
  - `/sitios/noticias/consejo-de-ciencia-y-tecnologia-del-estado-de-morelos`
    (la de noticias es la más rica: ahí salen REMEI, Soluciones Estratégicas,
    Fondos de Apoyo, etc., con sus fechas; se rastrean sus primeras 3 páginas).

**Morelos, sin índice del gobierno estatal** (`search_sources`, scraping directo
de páginas institucionales):
- **IMTA** — página de convocatoria del posgrado (`posgrado.imta.edu.mx`).
- **UTEZ** — página de becas (`utez.edu.mx/becas/`).
- **UPEMOR** — sección de posgrado/investigación.

### Por qué scraping directo y no un buscador

La primera versión usaba DuckDuckGo (`site:dominio convocatoria`), pero resultó
frágil: aplica rate-limit y devolvía resultados inconsistentes entre corridas
(por eso en tu primera prueba dio "7 entradas" y en la siguiente 0). Ahora cada
institución apunta directo a su página real de convocatorias/posgrado/becas,
verificadas. Es más estable y preciso. Si una institución cambia su URL, edita
la entrada en `search_sources` dentro de `config.json`.

> **INEEL e ITESM/Tec** se retiraron de la lista por ahora: el INEEL solo
> publica *licitaciones* (no convocatorias de investigación) en una URL estable,
> y sus estancias de investigación no tienen página fija; el Tec de Monterrey no
> tiene un índice de convocatorias de investigación filtrable por Morelos. Si
> encuentras sus URLs reales de convocatorias, agrégalas con el mismo formato.

### Filtro must_contain + exclude

Cada fuente scrapeada filtra por dos listas: `must_contain` (el título debe
tener al menos una de estas palabras) y `exclude` (si el título contiene alguna,
se descarta). Esto es clave para CCyTEM, cuya página de noticias mezcla
convocatorias de investigación (REMEI, Soluciones Estratégicas, Fondos de Apoyo)
con mucho ruido de emprendimiento, robótica, registro de marcas y hackathons.
Los `exclude` por defecto ya filtran ese ruido; ajústalos si ves algo colarse.

## Afinar el filtrado (reducir ruido)

Las convocatorias SECIHTI se marcan como *relevantes* de dos formas:

1. **Keyword en el título** (`keywords`): match preciso sobre el nombre de la
   convocatoria, no sobre su categoría. Así se evita que una categoría amplia
   como "Desarrollo Tecnológico, Vinculación e Innovación" cuele cosas sin
   relación (p.ej. "Copa FutBotMX", premios varios).
2. **Categoría en `always_notify_categories`**: toda convocatoria de esas
   categorías te llega aunque su título no mencione tus términos. Por defecto:
   Ciencia Básica y de Frontera, Centros Públicos de Investigación e
   Inteligencia Artificial. La categoría se detecta tanto por el texto de la
   tarjeta como, de forma más fiable, por el slug de la URL
   (`/convocatoria/inteligencia-artificial/...`).

Si notas ruido o ausencias, edita esas dos listas en `config.json`. Por ejemplo,
para dejar de recibir todo lo de IA, quita `"inteligencia artificial"` de
`always_notify_categories`.

## Extensión futura

Si alguna institución habilita un feed RSS o un índice HTML estable de
convocatorias, muévela de `search_sources` a `morelos_sources` con su selector
CSS: el scraping directo es más preciso y no depende de un buscador externo.
