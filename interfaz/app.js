// Interfaz del servidor OPC UA (Fase C). Sin bibliotecas externas.
"use strict";

const $ = (id) => document.getElementById(id);
const estado = { drivers: [], config: null, modelo: null, logDesde: 0 };

async function api(metodo, ruta, cuerpo, formulario) {
  const opciones = { method: metodo, headers: { "X-Interfaz": "1" }, credentials: "same-origin" };
  if (formulario) opciones.body = formulario;
  else if (cuerpo !== undefined) {
    opciones.headers["Content-Type"] = "application/json";
    opciones.body = JSON.stringify(cuerpo);
  }
  const r = await fetch(ruta, opciones);
  if (r.status === 401 && ruta !== "/api/login") mostrarLogin();
  let datos = null;
  try { datos = await r.json(); } catch (_) { /* sin cuerpo */ }
  return { ok: r.ok, status: r.status, datos: datos || {} };
}

function lista(id, elementos) {
  const ul = $(id);
  ul.replaceChildren(...(elementos || []).map((t) => Object.assign(document.createElement("li"), { textContent: t })));
}

// --- Sesion ---
function mostrarLogin() { $("login").hidden = false; }
$("form-login").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const r = await api("POST", "/api/login", { usuario: $("login-usuario").value, clave: $("login-clave").value });
  if (r.ok) { $("login").hidden = true; $("login-clave").value = ""; $("salir").hidden = false; iniciar(); }
  else $("login-error").textContent = (r.datos.errores || ["Error"]).join(" ");
});
$("salir").addEventListener("click", async () => { await api("POST", "/api/logout"); mostrarLogin(); });

// --- Pestanas ---
document.querySelectorAll("#pestanas button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("#pestanas button").forEach((x) => x.classList.toggle("activa", x === b));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("activo", p.id === b.dataset.panel));
}));

// --- 1. Conexion: formulario generado desde los parametros que declara el driver ---
function campoParametro(p, valor) {
  const etiqueta = document.createElement("label");
  etiqueta.textContent = p.descripcion || p.nombre;
  let entrada;
  if (p.tipo === "opcion") {
    entrada = document.createElement("select");
    p.opciones.forEach((o) => entrada.append(new Option(o, o)));
  } else {
    entrada = document.createElement("input");
    if (p.tipo === "entero" || p.tipo === "real") {
      entrada.type = "number";
      entrada.step = p.tipo === "entero" ? "1" : "any";
      if (p.minimo !== null) entrada.min = p.minimo;
      if (p.maximo !== null) entrada.max = p.maximo;
    } else if (p.tipo === "bool") entrada.type = "checkbox";
  }
  entrada.dataset.parametro = p.nombre;
  entrada.dataset.tipo = p.tipo;
  const v = valor !== undefined && valor !== null ? valor : p.defecto;
  if (p.tipo === "bool") entrada.checked = !!v; else entrada.value = v ?? "";
  entrada.required = !!p.obligatorio;
  etiqueta.append(entrada);
  return etiqueta;
}

function pintarDriver(nombre, valores) {
  const d = estado.drivers.find((x) => x.nombre === nombre);
  $("driver-descripcion").textContent = d ? d.descripcion : "";
  const normales = $("driver-parametros"), avanzados = $("driver-avanzados").querySelector(".rejilla");
  normales.replaceChildren(); avanzados.replaceChildren();
  (d ? d.parametros : []).forEach((p) => (p.avanzado ? avanzados : normales).append(campoParametro(p, (valores || {})[p.nombre])));
  $("driver-avanzados").hidden = !avanzados.children.length;
}
$("driver-nombre").addEventListener("change", (ev) => pintarDriver(ev.target.value, {}));

function filaUsuario(u) {
  const tr = document.createElement("tr");
  const nombre = Object.assign(document.createElement("input"), { value: u.nombre || "", className: "u-nombre" });
  const clave = Object.assign(document.createElement("input"), { type: "password", className: "u-clave",
    placeholder: u.tiene_clave ? "(sin cambios)" : "contraseña", autocomplete: "new-password" });
  const quitar = Object.assign(document.createElement("button"), { textContent: "Quitar", className: "secundario" });
  quitar.addEventListener("click", () => tr.remove());
  [nombre, clave, quitar].forEach((e) => { const td = document.createElement("td"); td.append(e); tr.append(td); });
  $("usuarios").querySelector("tbody").append(tr);
}
$("usuario-nuevo").addEventListener("click", () => filaUsuario({}));

function pintarConfig(cfg) {
  estado.config = cfg;
  $("driver-nombre").value = cfg.driver.nombre;
  pintarDriver(cfg.driver.nombre, cfg.driver.parametros);
  for (const [bloque, claves] of Object.entries({ enlace: ["fallos_max", "reintento_max_s"],
    servidor: ["endpoint", "app_uri", "host"], seguridad: ["modo", "cert_dir"],
    medida: ["periodo_ms", "log_csv", "log_accesos"] })) {
    claves.forEach((k) => { $(`${bloque}-${k}`).value = cfg[bloque][k] ?? ""; });
  }
  $("seguridad-lista_confianza").checked = cfg.seguridad.lista_confianza !== false;
  $("usuarios").querySelector("tbody").replaceChildren();
  cfg.seguridad.usuarios.forEach(filaUsuario);
  $("modelo-elegido").textContent = descripcionModelo(cfg.modelo);
}

// Certificados de cliente rechazados (solo lectura)
async function cargarRechazados() {
  const r = await api("GET", "/api/certificados/rechazados");
  if (!r.ok) return;
  $("rechazados-carpeta").textContent = r.datos.carpeta;
  $("confianza-carpeta").textContent = r.datos.confianza;
  $("rechazados").querySelector("tbody").replaceChildren(...r.datos.certificados.map((c) => {
    const tr = document.createElement("tr");
    const accion = c.aceptable ? "moverlo a confianza" : "no: el cliente necesita otro certificado";
    [c.nombre, c.huella, c.fecha, c.motivo, accion].forEach((t) => { const td = document.createElement("td"); td.textContent = t; tr.append(td); });
    return tr;
  }));
}
$("rechazados-refrescar").addEventListener("click", cargarRechazados);

function descripcionModelo(m) {
  if (!m || !m.nodeset) return "—";
  return m.origen === "planta" ? `${m.nodeset} (generado al arrancar desde ${m.planta})` : `${m.nodeset} (NodeSet)`;
}

function numero(id) { const v = $(id).value; return v === "" ? null : Number(v); }
function texto(id) { const v = $(id).value.trim(); return v === "" ? null : v; }

function recogerConfig() {
  const parametros = {};
  document.querySelectorAll("[data-parametro]").forEach((e) => {
    if (e.dataset.tipo === "bool") parametros[e.dataset.parametro] = e.checked;
    else if (e.value !== "") parametros[e.dataset.parametro] =
      e.dataset.tipo === "entero" || e.dataset.tipo === "real" ? Number(e.value) : e.value;
  });
  const usuarios = [...$("usuarios").querySelectorAll("tbody tr")].map((tr) => {
    const u = { nombre: tr.querySelector(".u-nombre").value.trim() };
    const c = tr.querySelector(".u-clave").value;
    if (c) u.clave = c;
    return u;
  }).filter((u) => u.nombre);
  return {
    servidor: { endpoint: texto("servidor-endpoint"), app_uri: texto("servidor-app_uri"), host: texto("servidor-host"),
                perfiles: estado.config ? estado.config.servidor.perfiles : [] },
    driver: { nombre: $("driver-nombre").value, parametros },
    enlace: { fallos_max: numero("enlace-fallos_max"), reintento_max_s: numero("enlace-reintento_max_s") },
    modelo: estado.config ? estado.config.modelo : { origen: "nodeset", nodeset: null, planta: null },
    seguridad: { modo: $("seguridad-modo").value, cert_dir: texto("seguridad-cert_dir"),
                 lista_confianza: $("seguridad-lista_confianza").checked, usuarios },
    medida: { periodo_ms: numero("medida-periodo_ms"), log_csv: texto("medida-log_csv"),
              log_accesos: texto("medida-log_accesos") },
  };
}

// --- 2. Modelo ---
document.querySelectorAll("input[name=via]").forEach((r) => r.addEventListener("change", () => {
  const crear = document.querySelector("input[name=via]:checked").value === "crear";
  $("via-crear").hidden = !crear; $("via-importar").hidden = crear;
}));

function pintarResultado(r, titulo) {
  $("modelo-estado").textContent = r.valida ? `${titulo}: válido` : `${titulo}: con errores`;
  $("modelo-estado").className = r.valida ? "nota" : "error";
  lista("modelo-errores", r.errores);
  lista("modelo-avisos", r.avisos);
  const cuerpo = $("tabla-mapeo").querySelector("tbody");
  cuerpo.replaceChildren(...(r.tabla || []).map((f) => {
    const tr = document.createElement("tr");
    [f.ruta, f.direccion, f.tipo, f.sentido].forEach((v, i) => {
      const td = Object.assign(document.createElement("td"), { textContent: v ?? "" });
      if (i === 1) td.className = "dir";
      tr.append(td);
    });
    return tr;
  }));
  $("tabla-mapeo").hidden = !(r.tabla || []).length;
  $("modelo-metodos").textContent = (r.metodos || []).length
    ? "Métodos: " + r.metodos.map((m) => m.ruta).join(", ") : "";
}

function elegirModelo(m) {
  estado.config = estado.config || {};
  estado.config.modelo = m;
  $("modelo-elegido").textContent = descripcionModelo(m) + " · pendiente de guardar";
}

async function cargarFicheros() {
  const r = await api("GET", "/api/ficheros");
  const sel = $("importar-ruta");
  sel.replaceChildren(...(r.datos || []).filter((f) => f.endsWith(".xml")).map((f) => new Option(f, f)));
}

$("importar").addEventListener("click", async () => {
  const r = await api("POST", "/api/modelo/importar", { ruta: $("importar-ruta").value });
  pintarResultado(r.ok ? r.datos : { valida: false, errores: r.datos.errores }, "NodeSet");
  if (r.ok && r.datos.valida) elegirModelo({ origen: "nodeset", nodeset: r.datos.nodeset, planta: null });
});

$("subir").addEventListener("click", async () => {
  const f = $("subir-fichero").files[0];
  if (!f) return;
  const datos = new FormData();
  datos.append("fichero", f);
  datos.append("sobrescribir", $("subir-sobrescribir").checked ? "true" : "false");
  const r = await api("POST", "/api/modelo/subir", undefined, datos);
  pintarResultado(r.ok ? r.datos : { valida: false, errores: r.datos.errores }, "NodeSet subido");
  if (r.ok) { await cargarFicheros(); $("importar-ruta").value = r.datos.nodeset; }
  if (r.ok && r.datos.valida) elegirModelo({ origen: "nodeset", nodeset: r.datos.nodeset, planta: null });
});

// Editor de YAML con validacion inmediata (500 ms sin teclear)
let temporizador = null;
async function validarPlanta() {
  const r = await api("POST", "/api/modelo/validar", { yaml: $("editor").value });
  pintarResultado(r.datos, "Descripción de planta");
  $("generar").disabled = !r.datos.valida;
}
$("editor").addEventListener("input", () => {
  $("generar").disabled = true;
  clearTimeout(temporizador);
  temporizador = setTimeout(validarPlanta, 500);
});
$("editor").addEventListener("keydown", (ev) => {
  if (ev.key === "Tab") {                       // sangria con espacios, no cambio de foco
    ev.preventDefault();
    const e = ev.target, i = e.selectionStart;
    e.setRangeText("  ", i, e.selectionEnd, "end");
    e.dispatchEvent(new Event("input"));
  }
});
$("cargar-plantilla").addEventListener("click", async () => {
  const nombre = $("plantilla").value;
  const r = await api("GET", `/api/plantillas/${encodeURIComponent(nombre)}`);
  if (!r.ok) return;
  $("editor").value = r.datos.yaml;
  $("nombre-planta").value = `planta_${nombre}.yaml`;
  $("nombre-nodeset").value = `modelo_${nombre}.xml`;
  validarPlanta();
});
$("generar").addEventListener("click", async () => {
  const cuerpo = { yaml: $("editor").value, planta: $("nombre-planta").value, nodeset: $("nombre-nodeset").value };
  const r = await api("POST", "/api/modelo/generar", cuerpo);
  pintarResultado(r.ok ? r.datos : { valida: false, errores: r.datos.errores }, "NodeSet generado");
  if (r.ok) {
    elegirModelo($("regenerar").checked ? { origen: "planta", planta: cuerpo.planta, nodeset: cuerpo.nodeset }
                                        : { origen: "nodeset", nodeset: cuerpo.nodeset, planta: null });
    cargarFicheros();
  }
});

// --- 4. Servidor ---
async function validarConfig() {
  const r = await api("POST", "/api/config/validar", { config: recogerConfig() });
  lista("config-errores", r.datos.errores); lista("config-avisos", r.datos.avisos);
  return r.datos.valida;
}
$("validar-config").addEventListener("click", validarConfig);
$("guardar-config").addEventListener("click", async () => {
  const r = await api("PUT", "/api/config", { config: recogerConfig() });
  lista("config-errores", r.datos.errores);
  lista("config-avisos", r.ok ? ["config.yaml guardado.", ...(r.datos.avisos || [])] : []);
  if (r.ok) { const c = await api("GET", "/api/config"); pintarConfig(c.datos.config); }
});

async function accion(ruta) {
  ["arrancar", "detener", "reiniciar"].forEach((id) => { $(id).disabled = true; });
  const r = await api("POST", ruta);
  lista("config-errores", r.ok ? [] : r.datos.errores);
  if (r.ok && r.datos.forzada) lista("config-avisos", ["El servidor no respondió a la parada ordenada: se detuvo de forma forzada."]);
  if (ruta.endsWith("arrancar") || ruta.endsWith("reiniciar")) { estado.logDesde = 0; $("log").textContent = ""; }
  await refrescarEstado();
}
$("arrancar").addEventListener("click", () => accion("/api/servidor/arrancar"));
$("detener").addEventListener("click", () => accion("/api/servidor/detener"));
$("reiniciar").addEventListener("click", () => accion("/api/servidor/reiniciar"));

async function refrescarEstado() {
  const r = await api("GET", "/api/servidor/estado");
  if (!r.ok) return;
  const e = r.datos;
  const txt = e.en_marcha ? `En marcha · PID ${e.pid}${e.propio ? "" : " (lanzado por otra sesión)"}`
    : e.ultima_salida ? `Detenido · ${e.ultima_salida.forzada ? "parada forzada" : "parada ordenada"}` : "Detenido";
  [$("estado-proceso"), $("estado-cabecera")].forEach((x) => { x.textContent = txt; x.classList.toggle("marcha", e.en_marcha); });
  $("arrancar").disabled = e.en_marcha;
  $("detener").disabled = !e.en_marcha;
  $("reiniciar").disabled = !e.en_marcha;
}

async function refrescarLog() {
  const r = await api("GET", `/api/servidor/log?desde=${estado.logDesde}`);
  if (!r.ok) return;
  if (r.datos.siguiente < estado.logDesde) { estado.logDesde = 0; $("log").textContent = ""; return; }
  if (r.datos.lineas.length) {
    const pre = $("log"), abajo = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 10;
    pre.textContent += r.datos.lineas.join("\n") + "\n";
    if (abajo) pre.scrollTop = pre.scrollHeight;
  }
  estado.logDesde = r.datos.siguiente;
}

// --- Arranque de la pagina ---
async function iniciar() {
  const d = await api("GET", "/api/drivers");
  if (!d.ok) return;
  estado.drivers = d.datos;
  $("driver-nombre").replaceChildren(...estado.drivers.map((x) => new Option(x.nombre, x.nombre)));
  const p = await api("GET", "/api/plantillas");
  $("plantilla").replaceChildren(...(p.datos || []).map((n) => new Option(n, n)));
  const c = await api("GET", "/api/config");
  if (c.ok) pintarConfig(c.datos.config);
  await cargarRechazados();
  await cargarFicheros();
  await refrescarEstado();
}

(async () => {
  const s = await api("GET", "/api/sesion");
  if (s.datos.requiere_login && !s.datos.iniciada) mostrarLogin();
  else { $("salir").hidden = !s.datos.requiere_login; await iniciar(); }
  setInterval(() => { if ($("login").hidden) { refrescarEstado(); refrescarLog(); } }, 1000);
})();
