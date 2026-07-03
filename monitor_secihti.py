#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monitor_secihti.py
Monitor autónomo de convocatorias SECIHTI + institutos de C&T de Morelos.

Diseñado para Alberto González (IMTA, Jiutepec, Morelos).
Filtra por líneas de interés: laboratorios nacionales, ciencia básica y de
frontera, sector hidroagrícola, IA / teledetección / SIG, agua.

Estrategia de scraping
----------------------
El portal SECIHTI corre WordPress + Elementor y expone taxonomías con URLs
predecibles. En vez de un navegador headless, se consultan las páginas de
listado (que son HTML estático server-side) y se parsean con BeautifulSoup.

Fuentes:
  1. SECIHTI  -> convocatorias abiertas (todas las páginas del archivo).
  2. Morelos  -> se resuelve dinámicamente vía búsqueda (config abajo); por
     defecto vigila el CIByC-UAEM, CEIB-UNAM (Cuernavaca), IBt-UNAM,
     INSP, CIB-UAEM y el propio IMTA a través de sus feeds/paginas de noticias.

Persistencia: SQLite. Solo notifica lo NUEVO (dedupe por hash de URL).
Notificación: email (SMTP) y/o webhook JSON. Ambos opcionales.

Uso:
    python monitor_secihti.py --once          # una pasada
    python monitor_secihti.py --loop 3600      # cada hora (segundos)
    python monitor_secihti.py --list           # muestra todo lo almacenado
"""

import argparse
import hashlib
import json
import re
import smtplib
import sqlite3
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Faltan dependencias: pip install requests beautifulsoup4 lxml")

# --------------------------------------------------------------------------- #
# CONFIGURACIÓN
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "convocatorias.db"
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    # --- Palabras clave: si el título/categoría contiene ALGUNA, marca match ---
    # Se comparan sin acentos y en minúsculas.
    "keywords": [
        "laboratorio nacional", "laboratorios nacionales",
        "ciencia basica", "ciencia de frontera", "ciencia basica y de frontera",
        "frontera de la ciencia",
        "infraestructura cientifica", "equipamiento",
        "hidroagricola", "agua", "hidrico", "riego", "cuenca",
        "inteligencia artificial", "teledeteccion", "sensores remotos",
        "percepcion remota", "sistemas de informacion geografica", " sig ",
        "geoespacial", "ciencia de datos", "machine learning",
        "ejes estrategicos", "soberania hidrica", "soberania alimentaria",
        "cambio climatico", "recursos hidricos",
        "proyectos de investigacion", "desarrollo tecnologico",
        "centros publicos de investigacion",
    ],
    # keywords que, si aparecen, siempre notifican aunque no cumplan filtro fino.
    # NOTA: se dejan solo categorías realmente alineadas con tu perfil. Antes
    # incluía "Desarrollo Tecnológico..." completo, pero esa categoría es tan
    # amplia que colaba ruido (p.ej. "Copa FutBotMX", premios varios). Ahora
    # esos solo entran si su TÍTULO contiene una keyword tuya.
    # "Inteligencia Artificial" SÍ va aquí: toda convocatoria de esa categoría
    # te interesa aunque su título no mencione tus términos exactos.
    "always_notify_categories": [
        "ciencia basica y de frontera",
        "centros publicos de investigacion",
        "inteligencia artificial",
    ],
    # Si True, guarda TODAS las convocatorias abiertas pero solo marca match las
    # relevantes. Si False, solo guarda las que hacen match con keywords.
    "store_all_open": True,

    # --- Fuentes Morelos CON listado HTML estable (se scrapean directo) -------
    # CCyTEM vive en Webflow servido desde morelos.gob.mx. La página de noticias
    # es la más rica pero trae MUCHO ruido (emprendimiento, robótica, marcas...),
    # así que se filtra fuerte con must_contain + exclude.
    "morelos_sources": [
        {
            "name": "CCyTEM-convocatorias",
            "url": "https://www.morelos.gob.mx/sitios/convocatorias/consejo-de-ciencia-y-tecnologia-del-estado-de-morelos",
            "link_selector": "a[href*='/ultimas-noticias/'], a[href*='/convocatoria']",
            # exige el nombre de un programa real o lenguaje de convocatoria abierta:
            "must_contain": ["remei", "merito estatal de investigacion",
                             "fondo de apoyo", "soluciones estrategicas",
                             "reembolso", "publicar en revistas",
                             "abre convocatoria", "emite convocatoria",
                             "recepcion de solicitudes", "bases de la convocatoria",
                             "convocatoria de posgrado", "beca"],
            # descarta divulgación, eventos, emprendimiento y negocios:
            "exclude": ["taller", "reunio", "fortalece", "reafirma", "dialogo",
                        "mision h2o", "copiem", "noche de las estrellas",
                        "un dia de pinta", "festeja", "ilumina", "acerca",
                        "emprend", "startup", "hackathon", "war room",
                        "tu marca", "marca, tu valor", "inversion",
                        "financiamiento para empresas", "robotix", "robotica",
                        "copa ", "futbot", "concurso nacional de propiedad",
                        "incubacion", "pasarela", "negocios", "firma", "alianza",
                        "acuerdo", "encuentro", "capacitacion visual"]
        },
        {
            "name": "CCyTEM-noticias",
            "url": "https://www.morelos.gob.mx/sitios/noticias/consejo-de-ciencia-y-tecnologia-del-estado-de-morelos",
            "link_selector": "a[href*='/ultimas-noticias/']",
            "must_contain": ["remei", "merito estatal de investigacion",
                             "fondo de apoyo", "soluciones estrategicas",
                             "reembolso", "publicar en revistas",
                             "abre convocatoria", "emite convocatoria",
                             "recepcion de solicitudes", "bases de la convocatoria",
                             "convocatoria de posgrado"],
            "exclude": ["taller", "reunio", "fortalece", "reafirma", "dialogo",
                        "mision h2o", "copiem", "noche de las estrellas",
                        "un dia de pinta", "festeja", "ilumina", "acerca",
                        "emprend", "startup", "hackathon", "war room",
                        "tu marca", "marca, tu valor", "inversion",
                        "financiamiento para empresas", "robotix", "robotica",
                        "copa ", "futbot", "concurso nacional de propiedad",
                        "incubacion", "pasarela", "negocios", "firma", "alianza",
                        "acuerdo", "encuentro", "capacitacion visual"],
            "paginate_param": "1e89f601_page",
            "paginate_pages": 3
        }
    ],

    # --- Fuentes SIN índice estable -> scraping directo de páginas conocidas ---
    # NOTA: se abandonó el buscador DuckDuckGo por frágil (rate-limit). En su
    # lugar, cada institución apunta a su página real de convocatorias/posgrado,
    # verificadas. Si alguna cambia de URL, edítala aquí.
    "search_sources": [
        {
            # IMTA: página de oferta de posgrado. Interesa la convocatoria de
            # admisión, no los menús ni los formatos PDF. Se exige lenguaje de
            # convocatoria y se excluye navegación/formatos.
            "name": "IMTA-posgrado",
            "url": "https://posgrado.imta.edu.mx/index.php/component/content/article?id=181",
            "link_selector": "a",
            "must_contain": ["convocatoria", "admision", "proceso de admision"],
            "exclude": ["formato", "fpa-02", "carta de exposicion", "menu",
                        "sistema posgrado", "consejo de posgrado", "organigrama",
                        "estructura", "biblioteca", "moodle"]
        },
        {
            # UTEZ: página de becas. La convocatoria vigente es el PDF
            # "Convocatoria_Becas_...". Se excluyen resultados y correos.
            "name": "UTEZ-becas",
            "url": "https://www.utez.edu.mx/becas/",
            "link_selector": "a",
            "must_contain": ["convocatoria"],
            "exclude": ["resultado", "mailto", "@", "seminario virtual anuies"]
        }
        # UPEMOR retirada: su página de posgrado solo lista programas (menú),
        # no publica convocatorias con fecha en HTML scrapeable. Si detectas una
        # URL real de convocatorias de UPEMOR, agrégala aquí con el mismo formato.
    ],

    # --- Notificaciones (todo opcional; deja vacío para desactivar) ----------
    "email": {
        "enabled": False,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "user": "tu_correo@gmail.com",
        "password": "app_password_de_16_digitos",
        "to": ["alberto@imta.mx"]
    },
    "webhook": {
        "enabled": False,
        "url": ""  # p.ej. un webhook de Slack/Discord/Teams
    },

    "request_timeout": 25,
    "user_agent": "Mozilla/5.0 (compatible; SECIHTI-Monitor/1.0; +investigacion)",
    # dominios cuyo certificado SSL no valida bien (cadena incompleta) y para
    # los que se acepta conexión sin verificación. Úsalo solo con sitios que
    # conozcas y en los que confíes (aquí, el posgrado del IMTA).
    "insecure_ssl_domains": ["posgrado.imta.edu.mx"]
}

SECIHTI_BASE = "https://secihti.mx"
SECIHTI_OPEN_LIST = "https://secihti.mx/estatus-convocatoria/abierta/"


# --------------------------------------------------------------------------- #
# UTILIDADES
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        # merge superficial con defaults para tolerar configs parciales
        merged = {**DEFAULT_CONFIG, **cfg}
        for k in ("email", "webhook"):
            if k in cfg:
                merged[k] = {**DEFAULT_CONFIG[k], **cfg[k]}

        # Detectar config de una versión anterior: search_sources con el formato
        # viejo (domains/terms del buscador DuckDuckGo) en vez del nuevo (url).
        old = [s for s in merged.get("search_sources", [])
               if "url" not in s and ("domains" in s or "terms" in s)]
        if old:
            print("[config] Tu config.json trae 'search_sources' de una versión "
                  "anterior (domains/terms). Se sustituyen por las fuentes nuevas "
                  "(scraping directo). Tus otras preferencias se conservan.\n"
                  "         Se guardó una copia en config.json.bak")
            CONFIG_PATH.rename(CONFIG_PATH.with_suffix(".json.bak"))
            merged["search_sources"] = DEFAULT_CONFIG["search_sources"]
            # también refrescamos morelos_sources si vienen sin 'exclude' (viejo)
            if any("exclude" not in s for s in merged.get("morelos_sources", [])):
                merged["morelos_sources"] = DEFAULT_CONFIG["morelos_sources"]
            CONFIG_PATH.write_text(
                json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        return merged
    CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(f"[config] Se creó {CONFIG_PATH} con valores por defecto. Edítalo.")
    return DEFAULT_CONFIG


def strip_accents(s: str) -> str:
    table = str.maketrans("áéíóúüñ", "aeiouun")
    return s.lower().translate(table)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(s)).strip()


def uid(url: str) -> str:
    return hashlib.sha256(url.strip().encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# MODELO
# --------------------------------------------------------------------------- #
@dataclass
class Convocatoria:
    id: str
    source: str          # "SECIHTI" o nombre del instituto
    title: str
    url: str
    categories: str      # coma-separada
    periodo: str
    estatus: str
    fecha_cierre: str
    matched_keywords: str
    is_match: int
    first_seen: str


# --------------------------------------------------------------------------- #
# BASE DE DATOS
# --------------------------------------------------------------------------- #
def db_init() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS convocatorias (
            id TEXT PRIMARY KEY,
            source TEXT, title TEXT, url TEXT,
            categories TEXT, periodo TEXT, estatus TEXT,
            fecha_cierre TEXT, matched_keywords TEXT,
            is_match INTEGER, first_seen TEXT
        )""")
    conn.commit()
    return conn


def db_upsert(conn, c: Convocatoria) -> bool:
    """Devuelve True si es un registro NUEVO."""
    exists = conn.execute("SELECT 1 FROM convocatorias WHERE id=?", (c.id,)).fetchone()
    if exists:
        return False
    conn.execute("""INSERT INTO convocatorias VALUES
        (:id,:source,:title,:url,:categories,:periodo,:estatus,
         :fecha_cierre,:matched_keywords,:is_match,:first_seen)""", asdict(c))
    conn.commit()
    return True


# --------------------------------------------------------------------------- #
# SCRAPERS
# --------------------------------------------------------------------------- #
def http_get(url: str, cfg: dict) -> str | None:
    # Algunos sitios de gobierno (p.ej. posgrado.imta.edu.mx) tienen cadenas de
    # certificado incompletas y fallan la verificación SSL. Si el dominio está
    # en cfg["insecure_ssl_domains"], se desactiva la verificación SOLO para él.
    verify = True
    for dom in cfg.get("insecure_ssl_domains", []):
        if dom in url:
            verify = False
            break
    try:
        if not verify:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = requests.get(url, headers={"User-Agent": cfg["user_agent"]},
                         timeout=cfg["request_timeout"], verify=verify)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        print(f"[http] error {url}: {e}")
        return None


def match_keywords(text: str, cfg: dict) -> list[str]:
    t = " " + norm(text) + " "
    hits = [kw for kw in cfg["keywords"] if norm(kw) in t]
    return sorted(set(hits))


def parse_secihti_listing(html: str, cfg: dict) -> list[Convocatoria]:
    """
    Cada tarjeta de convocatoria en el archivo Elementor tiene:
      - una o más categorías (texto antes del año)
      - año (periodo)
      - estatus (Abierta / Cerrada)
      - <a> con href al detalle y el título como texto
      - fechas en <em> ('Cierre: 10 Jul 2026', etc.)
    El markup varía, así que se localiza por los enlaces a /convocatoria/... y se
    reconstruye el contexto desde el contenedor padre.
    """
    soup = BeautifulSoup(html, "lxml")
    out: list[Convocatoria] = []
    seen_urls = set()

    for a in soup.select('a[href*="/convocatoria/"]'):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        # Filtra enlaces de navegación por categoría (contienen /convocatoria_categoria/)
        if "/convocatoria_categoria/" in href or not title or len(title) < 15:
            continue
        url = urljoin(SECIHTI_BASE, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # contexto acotado a la tarjeta: subir hasta un ancestro "razonable"
        # (uno que contenga exactamente 1 enlace a /convocatoria/, es decir,
        # que no abarque tarjetas vecinas). Así evitamos contaminación cruzada.
        card = a
        chosen = a.parent or a
        for _ in range(6):
            p = card.parent
            if p is None:
                break
            links = p.select('a[href*="/convocatoria/"]')
            links = [l for l in links if "/convocatoria_categoria/" not in l.get("href", "")]
            if len(links) > 1:
                break  # este ancestro ya abarca otra tarjeta -> quedarnos con el previo
            chosen = p
            card = p
        ctx = chosen.get_text(" ", strip=True) if chosen else title

        periodo = ""
        m = re.search(r"\b(20\d{2})\b", ctx)
        if m:
            periodo = m.group(1)

        estatus = "Abierta" if re.search(r"\bAbierta\b", ctx, re.I) else \
                  ("Cerrada" if re.search(r"\bCerrada\b", ctx, re.I) else "")

        cierre = ""
        mc = re.search(r"Cierre:\s*([0-9]{1,2}\s+\w+\s+20\d{2})", ctx, re.I)
        if mc:
            cierre = mc.group(1)

        # categorías: heurística sobre el texto + refuerzo con el slug de la URL.
        # El path /convocatoria/<categoria-slug>/... es más fiable que el texto,
        # que a veces no aparece en el contexto de la tarjeta.
        cats = extract_categories(ctx)
        cats = merge_categories_from_url(cats, href)

        # Las keywords se evalúan SOLO sobre el título, no sobre la categoría:
        # así una categoría amplia (p.ej. "Desarrollo Tecnológico...") no marca
        # como relevante algo cuyo título no tiene nada que ver con tu perfil.
        # La categoría influye únicamente vía always_notify_categories.
        hits = match_keywords(title, cfg)
        always = any(norm(ac) in norm(cats) for ac in cfg["always_notify_categories"])
        is_match = 1 if (hits or always) else 0

        if not cfg["store_all_open"] and not is_match:
            continue

        out.append(Convocatoria(
            id=uid(url), source="SECIHTI", title=title, url=url,
            categories=cats, periodo=periodo, estatus=estatus or "Abierta",
            fecha_cierre=cierre, matched_keywords=", ".join(hits),
            is_match=is_match,
            first_seen=datetime.now(timezone.utc).isoformat(timespec="seconds")
        ))
    return out


KNOWN_CATEGORIES = [
    "Becas al extranjero", "Becas Nacionales", "Centros Públicos de Investigación",
    "Ciencias y Humanidades", "Ciencia Básica y de Frontera",
    "Cátedras de la Diáspora Mexicana", "Investigación Humanística",
    "Programa de Inserción Laboral (PIL)", "Proyectos de Investigación",
    "ECOS Nord", "Desarrollo Tecnológico, Vinculación e Innovación",
    "Inteligencia Artificial", "Especialidades Médicas",
    "Posgrado en Ciencias y Humanidades",
    "Doble grado México-Francia Ingenierías STEM",
]


def extract_categories(ctx: str) -> str:
    found = [c for c in KNOWN_CATEGORIES if norm(c) in norm(ctx)]
    return ", ".join(sorted(set(found)))


# slug de URL -> nombre canónico de categoría
URL_SLUG_TO_CATEGORY = {
    "inteligencia-artificial": "Inteligencia Artificial",
    "ciencia-basica-y-de-frontera": "Ciencia Básica y de Frontera",
    "centros-publicos-de-investigacion": "Centros Públicos de Investigación",
    "desarrollo-tecnologico-vinculacion-e-innovacion":
        "Desarrollo Tecnológico, Vinculación e Innovación",
    "becas-nacionales": "Becas Nacionales",
    "becas-al-extranjero": "Becas al extranjero",
    "ciencias-y-humanidades": "Ciencias y Humanidades",
    "proyectos-de-investigacion": "Proyectos de Investigación",
}


def merge_categories_from_url(cats: str, href: str) -> str:
    """Añade la categoría inferida del slug de la URL si no estaba ya."""
    existing = [c.strip() for c in cats.split(",") if c.strip()]
    for slug, name in URL_SLUG_TO_CATEGORY.items():
        if f"/{slug}/" in href and name not in existing:
            existing.append(name)
    return ", ".join(sorted(set(existing)))


def scrape_secihti(cfg: dict) -> list[Convocatoria]:
    """Recorre todas las páginas del listado de convocatorias abiertas."""
    results: list[Convocatoria] = []
    page = 1
    while True:
        url = SECIHTI_OPEN_LIST if page == 1 else f"{SECIHTI_OPEN_LIST}page/{page}/"
        html = http_get(url, cfg)
        if not html:
            break
        batch = parse_secihti_listing(html, cfg)
        if not batch:
            break
        results.extend(batch)
        # ¿hay página siguiente?
        if f"/page/{page+1}/" not in html:
            break
        page += 1
        time.sleep(1.0)  # cortesía
    # dedupe por id
    uniq = {c.id: c for c in results}
    return list(uniq.values())


def scrape_morelos(cfg: dict) -> list[Convocatoria]:
    return _scrape_source_list(cfg, cfg.get("morelos_sources", []))


def _scrape_source_list(cfg: dict, sources: list) -> list[Convocatoria]:
    """Scraper genérico de listados HTML. Soporta paginación, must_contain y
    exclude. Usado tanto por scrape_morelos como por scrape_search."""
    results: list[Convocatoria] = []
    for src in sources:
        # tolerar configs antiguos o entradas mal formadas: una fuente sin "url"
        # (p.ej. del formato viejo con domains/terms) se avisa y se omite, en vez
        # de tumbar todo el programa.
        base_url = src.get("url")
        if not base_url:
            print(f"[config] fuente '{src.get('name','?')}' sin 'url' "
                  f"(¿config.json de una versión anterior?) — se omite. "
                  f"Borra config.json para regenerarlo.")
            continue
        # construir lista de URLs a visitar (con paginación opcional)
        urls = [base_url]
        pparam = src.get("paginate_param")
        ppages = src.get("paginate_pages", 1)
        if pparam and ppages > 1:
            sep = "&" if "?" in base_url else "?"
            urls += [f"{base_url}{sep}{pparam}={n}"
                     for n in range(2, ppages + 1)]

        must = [norm(x) for x in src.get("must_contain", [])]
        excl = [norm(x) for x in src.get("exclude", [])]
        for page_url in urls:
            html = http_get(page_url, cfg)
            if not html:
                continue
            soup = BeautifulSoup(html, "lxml")
            for a in soup.select(src.get("link_selector", "a")):
                # título: texto directo o el de un hijo (Webflow envuelve texto)
                title = a.get_text(" ", strip=True)
                href = a.get("href", "")
                if not title or not href or len(title) < 12:
                    continue
                nt = norm(title)
                if must and not any(k in nt for k in must):
                    continue
                if excl and any(k in nt for k in excl):
                    continue  # descartar ruido explícito
                url = urljoin(page_url, href)
                hits = match_keywords(title, cfg)
                # En estas fuentes, el filtro must_contain/exclude ya está
                # diseñado para dejar pasar SOLO convocatorias reales. Por eso,
                # haber pasado el filtro implica relevancia (is_match=1), aunque
                # el título no contenga una keyword del perfil (esas son para el
                # matching fino de SECIHTI). Si una fuente no define must_contain,
                # se cae al criterio por keyword para no marcar todo.
                passed_filter = bool(must)  # tenía filtro y lo pasó
                is_match = 1 if (passed_filter or hits) else 0
                results.append(Convocatoria(
                    id=uid(url), source=src["name"], title=title[:300], url=url,
                    categories="", periodo="", estatus="",
                    fecha_cierre="", matched_keywords=", ".join(hits),
                    is_match=is_match,
                    first_seen=datetime.now(timezone.utc).isoformat(timespec="seconds")
                ))
            time.sleep(1.0)
    uniq = {c.id: c for c in results}
    return list(uniq.values())


def scrape_search(cfg: dict) -> list[Convocatoria]:
    """
    Fuentes SIN índice de convocatorias del gobierno estatal (IMTA, UTEZ,
    UPEMOR): se scrapean directamente sus páginas institucionales de
    posgrado/becas/convocatorias, con el mismo motor y filtros (must_contain +
    exclude) que las fuentes de Morelos. Se abandonó el buscador DuckDuckGo por
    ser frágil (rate-limit e inconsistencia entre corridas).

    Si una institución cambia la URL de su página de convocatorias, edita la
    entrada correspondiente en cfg["search_sources"].
    """
    # reutiliza exactamente la lógica de scrape_sources con la otra lista
    return _scrape_source_list(cfg, cfg.get("search_sources", []))


# --------------------------------------------------------------------------- #
# NOTIFICACIONES
# --------------------------------------------------------------------------- #
def notify(new_items: list[Convocatoria], cfg: dict):
    relevant = [c for c in new_items if c.is_match]
    if not relevant:
        return
    lines = []
    for c in relevant:
        kw = f" [{c.matched_keywords}]" if c.matched_keywords else ""
        cierre = f" — cierra {c.fecha_cierre}" if c.fecha_cierre else ""
        lines.append(f"• [{c.source}] {c.title}{cierre}{kw}\n  {c.url}")
    body = ("Nuevas convocatorias relevantes detectadas:\n\n" +
            "\n\n".join(lines) +
            f"\n\n({len(relevant)} nuevas · {datetime.now():%Y-%m-%d %H:%M})")

    # Email
    e = cfg["email"]
    if e.get("enabled"):
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = f"[SECIHTI] {len(relevant)} convocatoria(s) nueva(s)"
            msg["From"] = e["user"]
            msg["To"] = ", ".join(e["to"])
            with smtplib.SMTP(e["smtp_host"], e["smtp_port"]) as s:
                s.starttls()
                s.login(e["user"], e["password"])
                s.sendmail(e["user"], e["to"], msg.as_string())
            print(f"[email] enviado a {e['to']}")
        except Exception as ex:
            print(f"[email] fallo: {ex}")

    # Webhook
    w = cfg["webhook"]
    if w.get("enabled") and w.get("url"):
        try:
            requests.post(w["url"], json={"text": body}, timeout=cfg["request_timeout"])
            print("[webhook] enviado")
        except Exception as ex:
            print(f"[webhook] fallo: {ex}")

    # Consola siempre
    print("\n" + body + "\n")


# --------------------------------------------------------------------------- #
# ORQUESTACIÓN
# --------------------------------------------------------------------------- #
def run_once(cfg: dict, conn) -> int:
    print(f"[{datetime.now():%H:%M:%S}] Escaneando SECIHTI...  (BD: {DB_PATH})")
    items = scrape_secihti(cfg)
    print(f"  SECIHTI: {len(items)} convocatorias abiertas encontradas.")
    if cfg.get("morelos_sources"):
        m = scrape_morelos(cfg)
        print(f"  Morelos (listado): {len(m)} entradas.")
        items += m
    if cfg.get("search_sources"):
        s = scrape_search(cfg)
        print(f"  Morelos (búsqueda): {len(s)} entradas.")
        items += s

    new = [c for c in items if db_upsert(conn, c)]
    new_relevant = [c for c in new if c.is_match]
    print(f"  Nuevas: {len(new)} (relevantes: {len(new_relevant)})")
    if new:
        notify(new, cfg)
    return len(new_relevant)


def list_stored(conn, only_relevant: bool = False):
    q = ("SELECT source,title,fecha_cierre,is_match,url FROM convocatorias "
         + ("WHERE is_match=1 " if only_relevant else "")
         + "ORDER BY is_match DESC, first_seen DESC")
    rows = conn.execute(q).fetchall()
    print(f"Base de datos: {DB_PATH}")
    if not rows:
        print("(vacía) — aún no se ha guardado nada, o estás ejecutando desde\n"
              "otra carpeta que la usada en --once. Corre primero:\n"
              "    python monitor_secihti.py --once")
        return
    for src, title, cierre, m, url in rows:
        flag = "★" if m else " "
        cc = f" (cierra {cierre})" if cierre else ""
        print(f"{flag} [{src}] {title}{cc}\n    {url}")
    print(f"\nTotal: {len(rows)}")


def reeval_stored(conn, cfg) -> None:
    """Re-clasifica is_match de los registros de SECIHTI con los filtros
    actuales (keywords + always_notify). 

    IMPORTANTE: solo re-evalúa SECIHTI de forma fiable. Para las fuentes
    scrapeadas (CCyTEM, IMTA, UTEZ) la relevancia depende del filtro
    must_contain/exclude de cada fuente, que no se puede reconstruir desde el
    registro guardado. Si cambiaste esos filtros y quieres limpiar falsos
    positivos viejos de esas fuentes, usa --reset y vuelve a correr --once."""
    rows = conn.execute(
        "SELECT id,source,title,categories,url,is_match FROM convocatorias "
        "WHERE source='SECIHTI'"
    ).fetchall()
    cambios = 0
    for rid, source, title, cats, url, old_match in rows:
        hits = match_keywords(title, cfg)
        cats2 = merge_categories_from_url(cats or "", url or "")
        always = any(norm(ac) in norm(cats2)
                     for ac in cfg["always_notify_categories"])
        new_match = 1 if (hits or always) else 0
        if new_match != old_match:
            conn.execute("UPDATE convocatorias SET is_match=?, matched_keywords=? "
                         "WHERE id=?", (new_match, ", ".join(hits), rid))
            cambios += 1
    conn.commit()
    print(f"Re-evaluados {len(rows)} registros de SECIHTI; {cambios} cambiaron.")
    print("Nota: para limpiar falsos positivos viejos de CCyTEM/IMTA/UTEZ,\n"
          "      usa 'python monitor_secihti.py --reset' y vuelve a correr --once.")


def nivel_de_fuente(source: str) -> str:
    """Clasifica una convocatoria en 'federal' o 'estatal' según su fuente.
    SECIHTI es federal; el resto (CCyTEM, IMTA, UTEZ, ...) es estatal (Morelos)."""
    return "federal" if source.upper().startswith("SECIHTI") else "estatal"


# ruta del JSON que consume la interfaz web (carpeta docs/ para GitHub Pages)
EXPORT_PATH = BASE_DIR / "docs" / "data.json"


def export_json(conn) -> None:
    """Vuelca la base de datos a docs/data.json, separada por nivel federal y
    estatal, en el formato que consume la interfaz web (GitHub Pages)."""
    rows = conn.execute(
        "SELECT source,title,url,categories,periodo,estatus,fecha_cierre,"
        "matched_keywords,is_match,first_seen FROM convocatorias "
        "ORDER BY is_match DESC, first_seen DESC"
    ).fetchall()

    federal, estatal = [], []
    for (source, title, url, cats, periodo, estatus, cierre,
         kw, is_match, first_seen) in rows:
        item = {
            "titulo": title,
            "fuente": source,
            "url": url,
            "categoria": cats or "",
            "periodo": periodo or "",
            "estatus": estatus or "",
            "cierre": cierre or "",
            "keywords": kw or "",
            "relevante": bool(is_match),
            "visto": first_seen or "",
        }
        (federal if nivel_de_fuente(source) == "federal" else estatal).append(item)

    data = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totales": {
            "federal": len(federal),
            "estatal": len(estatal),
            "federal_relevantes": sum(1 for x in federal if x["relevante"]),
            "estatal_relevantes": sum(1 for x in estatal if x["relevante"]),
        },
        "federal": federal,
        "estatal": estatal,
    }

    EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exportado a {EXPORT_PATH}")
    print(f"  Federal: {len(federal)} ({data['totales']['federal_relevantes']} relevantes)")
    print(f"  Estatal: {len(estatal)} ({data['totales']['estatal_relevantes']} relevantes)")


def main():
    ap = argparse.ArgumentParser(description="Monitor de convocatorias SECIHTI/Morelos")
    ap.add_argument("--once", action="store_true", help="una sola pasada")
    ap.add_argument("--loop", type=int, metavar="SEG",
                    help="ejecutar en bucle cada SEG segundos")
    ap.add_argument("--list", action="store_true", help="listar lo almacenado")
    ap.add_argument("--relevant", action="store_true",
                    help="con --list, muestra solo lo marcado como relevante (★)")
    ap.add_argument("--reset", action="store_true",
                    help="borra la base de datos y empieza de cero")
    ap.add_argument("--reeval", action="store_true",
                    help="re-clasifica lo guardado con los filtros actuales")
    ap.add_argument("--export-json", action="store_true",
                    help="exporta docs/data.json (para la interfaz web) y termina")
    ap.add_argument("--no-export", action="store_true",
                    help="con --once, NO exporta el JSON al terminar")
    args = ap.parse_args()

    if args.reset:
        if DB_PATH.exists():
            DB_PATH.unlink()
            print(f"Base de datos borrada: {DB_PATH}")
        else:
            print("No había base de datos que borrar.")
        return

    cfg = load_config()
    conn = db_init()

    if args.reeval:
        reeval_stored(conn, cfg)
        return
    if getattr(args, "export_json", False):
        export_json(conn)
        return
    if args.list:
        list_stored(conn, only_relevant=args.relevant)
        return
    if args.loop:
        print(f"Monitor en bucle cada {args.loop}s. Ctrl+C para salir.")
        try:
            while True:
                run_once(cfg, conn)
                if not args.no_export:
                    export_json(conn)
                time.sleep(args.loop)
        except KeyboardInterrupt:
            print("\nDetenido.")
    else:
        run_once(cfg, conn)
        # tras una pasada, exportar el JSON para la web (salvo que se desactive)
        if not args.no_export:
            export_json(conn)


if __name__ == "__main__":
    main()
