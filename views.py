"""Presentación accesible, separada de la lógica de negocio."""
from html import escape
from urllib.parse import quote
from core import CATEGORIES, LOCATIONS, TRANSITIONS


def h(v):
    return escape(str(v), quote=True)


def layout(title, contents, active="inicio"):
    a1 = 'aria-current="page"' if active == "inicio" else ""
    a2 = 'aria-current="page"' if active == "new" else ""
    return f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{h(title)} | AulaFix</title><meta name="description" content="AulaFix, bitácora universitaria de incidencias de tecnología.">
<link rel="stylesheet" href="/static/style.css"></head><body>
<div class="shell"><aside class="sidebar"><a href="/" class="brand"><span class="brandmark">AF</span><span><strong>AulaFix</strong><small>Laboratorios de informática</small></span></a>
<div class="navtitle">ESPACIO DE TRABAJO</div><nav aria-label="Navegación principal"><a {a1} href="/"><span aria-hidden="true">▦</span> Panel de incidencias</a>
<a {a2} href="/new"><span aria-hidden="true">＋</span> Registrar incidencia</a></nav><div class="sidefoot"><span class="alive">●</span> Prototipo académico v1.0<br><small>Datos de demostración · uso local</small></div></aside>
<div class="mainarea"><header class="topbar"><span class="crumb">Gestión de soporte <span>/</span> {h(title)}</span><span class="pill">● Entorno local</span></header>
<main id="contenido">{contents}</main><footer>Proyecto académico · AulaFix · Sin conexión con sistemas universitarios reales.</footer></div></div></body></html>'''


def status_badge(state):
    classes = {"Abierto": "open", "En proceso": "process", "Resuelto": "resolved"}
    return f'<span class="state {classes.get(state,"open")}">{h(state)}</span>'


def priority_badge(label):
    classes={"Alta":"high","Media":"medium","Baja":"low"}
    return f'<span class="priority {classes.get(label,"low")}"><span class="circle">●</span> {h(label)}</span>'


def dashboard(tickets, metrics, q="", status=""):
    cards = [("Incidencias totales",metrics["total"],"■","blue"),("Pendientes",metrics["open"],"◷","purple"),
             ("En atención",metrics["progress"],"↗","orange"),("Resueltas",metrics["resolved"],"✓","green")]
    stat_html = "".join(f'<div class="stat {color}"><div class="statrow"><span>{h(name)}</span><span class="staticon">{ic}</span></div><strong>{count}</strong><small>Registros en esta versión</small></div>' for name,count,ic,color in cards)
    options = '<option value="">Todos los estados</option>' + ''.join(f'<option value="{h(state)}" {"selected" if status==state else ""}>{h(state)}</option>' for state in ("Abierto","En proceso","Resuelto"))
    rows = "".join(f'''<tr><td><a class="ticketlink" href="/ticket/{t['id']}">{h(t['code'])}</a><span class="tdsecondary">{h(t['category'])}</span></td>
<td class="tname"><a href="/ticket/{t['id']}">{h(t['title'])}</a><span class="tdsecondary">{h(t['location'])}</span></td>
<td>{priority_badge(t['priority'])}</td><td>{status_badge(t['status'])}</td><td class="muted">{h(t['created_at'][:16])}</td>
<td><a class="arrowlink" aria-label="Abrir {h(t['code'])}" href="/ticket/{t['id']}">↗</a></td></tr>''' for t in tickets)
    if not rows:
        rows='<tr><td colspan="6" class="empty"><div class="emptyicon">⌁</div><strong>Aún no hay incidencias para mostrar</strong><p>Registra la primera o cambia los filtros de búsqueda.</p><a class="btn primary small" href="/new">Crear incidencia</a></td></tr>'
    html=f'''<section class="heading"><div><div class="eyebrow">PANEL GENERAL <span class="dash"></span> SOPORTE UNIVERSITARIO</div><h1>Incidencias en un solo lugar<span class="bluepoint">.</span></h1>
<p>Registra, prioriza y da seguimiento a las fallas de los espacios tecnológicos.</p></div><a class="btn primary" href="/new">＋ &nbsp; Nueva incidencia</a></section>
<section class="stats" aria-label="Indicadores">{stat_html}</section>
<section class="notice"><div class="noticeicon">!</div><div><strong>{metrics['high_pending']} reportes de prioridad alta pendientes.</strong><p>La prioridad se calcula automáticamente por impacto × urgencia, no depende de una decisión arbitraria.</p></div><a href="/new" class="noticeaction">Registrar →</a></section>
<section class="surface"><div class="surfacehead"><div><div class="eyebrow">BITÁCORA ACTIVA</div><h2>Seguimiento de reportes</h2><p>Consulta los detalles o actualiza el estado de cada incidencia.</p></div><a class="export" href="/export.csv">↓ Exportar CSV</a></div>
<form class="filters" action="/" method="get"><label for="q" class="sr-only">Buscar incidencias</label><input id="q" name="q" value="{h(q)}" placeholder="⌕  Buscar por folio, título o ubicación..." maxlength="90">
<label for="status" class="sr-only">Filtrar por estado</label><select id="status" name="status">{options}</select><button class="btn secondary" type="submit">Aplicar filtros</button></form>
<div class="tablewrap"><table><thead><tr><th>FOLIO</th><th>INCIDENCIA / UBICACIÓN</th><th>PRIORIDAD</th><th>ESTADO</th><th>REGISTRADO (UTC)</th><th></th></tr></thead><tbody>{rows}</tbody></table></div></section>
<div class="bottomhint">ⓘ <strong>Alcance del prototipo:</strong> diseñado para uso local y datos ficticios; no incluye autenticación ni avisos automáticos.</div>'''
    return layout("Panel general", html, "inicio")


def select_field(name, label, values, selected="", hint=""):
    opts = '<option value="" disabled ' + ('selected' if not selected else '') + '>Selecciona una opción</option>'
    opts += ''.join(f'<option value="{h(x)}" {"selected" if selected==x else ""}>{h(x)}</option>' for x in values)
    return f'<div class="field"><label for="{name}">{h(label)} <span>*</span></label><select name="{name}" id="{name}" required>{opts}</select>{f"<small>{h(hint)}</small>" if hint else ""}</div>'


def new_ticket(data=None, errors=None):
    data = data or {}
    errors = errors or []
    alert = '<div class="errors" role="alert"><strong>Revisa estos datos:</strong><ul>' + ''.join(f'<li>{h(e)}</li>' for e in errors) + '</ul></div>' if errors else ""
    scales=[('1', '1 · Bajo'),('2', '2 · Medio'),('3', '3 · Alto')]
    def num_select(name, label, desc):
        opt=''.join(f'<option value="{num}" {"selected" if str(data.get(name,"2"))==num else ""}>{lab}</option>' for num,lab in scales)
        return f'<div class="field"><label for="{name}">{h(label)} *</label><select name="{name}" id="{name}">{opt}</select><small>{h(desc)}</small></div>'
    html=f'''<div class="back"><a href="/">← Volver al panel</a></div><section class="pageintro"><div class="eyebrow">REGISTRO / NUEVO REPORTE</div><h1>Registrar una incidencia<span class="bluepoint">.</span></h1><p>Completa la información con claridad; la prioridad se calcula automáticamente.</p></section>
{alert}<form method="post" action="/tickets" class="formlayout"><section class="surface formcard"><div class="formsectiontitle"><span>01</span><div><h2>Detalles del problema</h2><p>Describe lo que está ocurriendo y dónde se detectó.</p></div></div>
<div class="field"><label for="title">Título del reporte <span>*</span></label><input id="title" name="title" maxlength="100" minlength="8" required value="{h(data.get('title',''))}" placeholder="Ej. No hay conexión en las computadoras del laboratorio"></div>
<div class="fields2">{select_field('category','Categoría',CATEGORIES,data.get('category',''))}{select_field('location','Ubicación',LOCATIONS,data.get('location',''))}</div>
<div class="field"><label for="description">Descripción detallada <span>*</span></label><textarea id="description" name="description" rows="5" minlength="20" maxlength="700" required placeholder="Explica el error, cuándo ocurrió y cómo afecta el trabajo...">{h(data.get('description',''))}</textarea><small>Mínimo 20 caracteres; no incluyas contraseñas ni datos personales.</small></div>
<div class="field"><label for="reporter">Identificador del solicitante <span>*</span></label><input id="reporter" name="reporter" minlength="2" maxlength="40" required value="{h(data.get('reporter',''))}" placeholder="Ej. estudiante-07 / técnico-turno-B"><small>Utiliza un alias, no un nombre real.</small></div></section>
<section class="surface formcard"><div class="formsectiontitle"><span>02</span><div><h2>Evaluación de impacto</h2><p>Dos factores determinan una prioridad consistente.</p></div></div>
<div class="fields2">{num_select('impact','Impacto','¿A cuántas actividades o personas afecta?')}{num_select('urgency','Urgencia','¿Qué tan pronto necesita atención?')}</div>
<div class="matrix"><strong>¿Cómo se calcula?</strong><p>Impacto × urgencia = puntuación (1–9). Baja: 1–2 · Media: 3–4 · Alta: 6–9.</p></div>
<div class="formactions"><a href="/" class="btn secondary">Cancelar</a><button class="btn primary" type="submit">Guardar incidencia →</button></div></section></form>'''
    return layout("Registrar incidencia",html,"new")


def ticket_details(t, events, errors=None):
    errors=errors or []
    alert = '<div class="errors" role="alert">' + " ".join(h(e) for e in errors) + '</div>' if errors else ''
    nexts=TRANSITIONS[t['status']]
    opts=''.join(f'<option value="{h(x)}">{h(x)}</option>' for x in nexts)
    events_html=''.join(f'<li class="event"><span class="eventdot"></span><div><strong>{h(e["new_status"])}</strong><small>{h(e["created_at"])}</small><p>{h(e["note"])}</p></div></li>' for e in events)
    html=f'''<div class="back"><a href="/">← Volver al panel</a></div><section class="pageintro"><div class="eyebrow">DETALLE DEL REPORTE / {h(t['code'])}</div><h1>{h(t['title'])}</h1><p>Seguimiento detallado y registro de decisiones.</p></section>{alert}
<div class="detailgrid"><section class="surface formcard"><div class="detailhead"><h2>Información registrada</h2>{status_badge(t['status'])}</div><div class="details"><div><small>Folio</small><strong>{h(t['code'])}</strong></div><div><small>Prioridad</small>{priority_badge(t['priority'])}</div><div><small>Categoría</small><strong>{h(t['category'])}</strong></div><div><small>Ubicación</small><strong>{h(t['location'])}</strong></div><div><small>Impacto / urgencia</small><strong>{t['impact']} / {t['urgency']} (puntaje {t['score']})</strong></div><div><small>Alias del solicitante</small><strong>{h(t['reporter'])}</strong></div></div>
<h3>Descripción del problema</h3><p class="description">{h(t['description'])}</p><p class="meta">Creado: {h(t['created_at'])} · Último cambio: {h(t['updated_at'])}</p></section>
<aside class="stack"><section class="surface formcard"><h2>Actualizar estado</h2><p class="subnote">El flujo evita saltos que dificulten auditar el seguimiento.</p><form method="post" action="/ticket/{t['id']}/status"><div class="field"><label for="status">Nuevo estado</label><select name="status" id="status">{opts}</select></div><div class="field"><label for="note">Nota de seguimiento *</label><textarea name="note" id="note" minlength="10" maxlength="300" required placeholder="Ej. Se revisó la red y se comprobó el cableado."></textarea></div><button class="btn primary full" type="submit">Guardar seguimiento →</button></form></section>
<section class="surface formcard"><h2>Historial de movimientos</h2><ul class="events">{events_html}</ul></section></aside></div>'''
    return layout(f"Folio {t['code']}",html)


def message_page(title, message, status):
    html=f'<section class="surface message"><div class="msgicon">!</div><h1>{h(title)}</h1><p>{h(message)}</p><p class="muted">Código HTTP: {status}</p><a class="btn primary" href="/">Regresar al panel</a></section>'
    return layout(title,html)
