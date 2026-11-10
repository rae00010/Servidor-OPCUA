"""Paso 1: amplia el VariableType Address con 'area' (enum AreaPLC) y 'db',
corrige ValueRank, AccessLevel y los NodeIds cruzados del LedRojo."""
from datetime import datetime, timezone
from lxml import etree

SRC = "/mnt/user-data/uploads/ledmodel.xml"
DST = "/mnt/user-data/outputs/ledmodel_v5.xml"
NS = "http://opcfoundation.org/UA/2011/03/UANodeSet.xsd"
UAX = "http://opcfoundation.org/UA/2008/02/Types.xsd"
q = lambda t: f"{{{NS}}}{t}"
x = lambda t: f"{{{UAX}}}{t}"

AREA_DT = "ns=1;i=3001"
AREAS = [("I", "Entradas (%I)"), ("Q", "Salidas (%Q)"),
         ("M", "Marcas (%M)"), ("DB", "Bloque de datos")]
AREA_CODE = {"I": 0, "Q": 1, "M": 2, "DB": 3}
# Rol de cada variable Address segun su BrowseName -> (area, AccessLevel)
ROL = {"Expandido": ("I", 1), "Retraido": ("I", 1), "Pulsado": ("I", 1),
       "ExpandirSalida": ("Q", 1), "ContraerSalida": ("Q", 1),
       "Value": ("Q", 3)}  # LedRojo: el cliente escribe -> PLC

tree = etree.parse(SRC)
root = tree.getroot()


def el(tag, attrib=None, text=None, parent=None):
    e = etree.SubElement(parent, tag, attrib or {}) if parent is not None \
        else etree.Element(tag, attrib or {})
    if text is not None:
        e.text = text
    return e


def ref(refs, rtype, target, forward=True):
    a = {"ReferenceType": rtype}
    if not forward:
        a["IsForward"] = "false"
    el(q("Reference"), a, target, refs)


def propiedad(nodeid, name, parent, dtype, rule=None, value=None, display=None):
    v = el(q("UAVariable"), {"DataType": dtype, "AccessLevel": "1",
                            "BrowseName": f"1:{name}", "ParentNodeId": parent,
                            "NodeId": nodeid})
    el(q("DisplayName"), text=display or name, parent=v)
    refs = el(q("References"), parent=v)
    ref(refs, "HasTypeDefinition", "i=68")
    if rule:
        ref(refs, "HasModellingRule", rule)
    ref(refs, "HasProperty", parent, forward=False)
    if value is not None:
        val = el(q("Value"), parent=v)
        el(x("Int32"), text=str(value), parent=val)
    return v


# --- Metadatos del modelo -------------------------------------------------
model = root.find(f"{q('Models')}/{q('Model')}")
model.set("Version", "1.1.0")
model.set("PublicationDate", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
aliases = root.find(q("Aliases"))
if not any(a.get("Alias") == "LocalizedText" for a in aliases):
    el(q("Alias"), {"Alias": "LocalizedText"}, "i=21", aliases)

# --- 1. Corregir NodeIds cruzados del LedRojo -----------------------------
ren = {"ns=1;s=LedRojo.Value.BitIndex": "ns=1;s=LedRojo.Value.byte",
       "ns=1;s=LedRojo.Value.StartByte": "ns=1;s=LedRojo.Value.bit"}
for e in root.iter():
    for k in ("NodeId", "ParentNodeId"):
        if e.get(k) in ren:
            e.set(k, ren[e.get(k)])
    if e.tag == q("Reference") and e.text in ren:
        e.text = ren[e.text]

# --- 2. Tipo enumerado AreaPLC --------------------------------------------
address = next(e for e in root.iter(q("UAVariableType")) if e.get("NodeId") == "ns=1;i=2003")
idx = list(root).index(address)

com = etree.Comment(" Tipo enumerado del area de memoria (direccionamiento directo IEC 61131-3) ")
dt = el(q("UADataType"), {"NodeId": AREA_DT, "BrowseName": "1:AreaPLC"})
el(q("DisplayName"), text="AreaPLC", parent=dt)
el(q("Description"), text="Area de memoria del controlador", parent=dt)
refs = el(q("References"), parent=dt)
ref(refs, "HasProperty", "ns=1;i=6100")
ref(refs, "HasSubtype", "i=29", forward=False)
defn = el(q("Definition"), {"Name": "1:AreaPLC"}, parent=dt)
for i, (n, d) in enumerate(AREAS):
    f = el(q("Field"), {"Name": n, "Value": str(i)}, parent=defn)
    el(q("Description"), text=d, parent=f)

es = el(q("UAVariable"), {"DataType": "LocalizedText", "ValueRank": "1",
                          "ArrayDimensions": str(len(AREAS)), "BrowseName": "EnumStrings",
                          "ParentNodeId": AREA_DT, "NodeId": "ns=1;i=6100"})
el(q("DisplayName"), text="EnumStrings", parent=es)
r = el(q("References"), parent=es)
ref(r, "HasTypeDefinition", "i=68")
ref(r, "HasModellingRule", "i=78")
ref(r, "HasProperty", AREA_DT, forward=False)
lst = el(x("ListOfLocalizedText"), parent=el(q("Value"), parent=es))
for n, _ in AREAS:
    lt = el(x("LocalizedText"), parent=lst)
    el(x("Locale"), text="", parent=lt)
    el(x("Text"), text=n, parent=lt)

for k, node in enumerate((com, dt, es)):
    root.insert(idx + k, node)

# --- 3. VariableType Address: escalar + propiedades area y db -------------
address.set("ValueRank", "-1")
address.attrib.pop("ArrayDimensions", None)
arefs = address.find(q("References"))
ref(arefs, "HasProperty", "ns=1;i=6101")
ref(arefs, "HasProperty", "ns=1;i=6102")
pos = list(root).index(address) + 1
root.insert(pos, propiedad("ns=1;i=6101", "area", "ns=1;i=2003", AREA_DT, "i=78"))
root.insert(pos + 1, propiedad("ns=1;i=6102", "db", "ns=1;i=2003", "Int32", "i=80"))

# --- 3b. Pulsador: Control.PulsadoRemoto (M0.0), separado de Estado.Pulsado (I0.0)
def variable_address(nodeid, name, parent, rule=None, byte=None, bit=None):
    v = el(q("UAVariable"), {"DataType": "Boolean", "BrowseName": f"1:{name}",
                            "ParentNodeId": parent, "NodeId": nodeid})
    el(q("DisplayName"), text=name, parent=v)
    r_ = el(q("References"), parent=v)
    ref(r_, "HasTypeDefinition", "ns=1;i=2003")
    if rule:
        ref(r_, "HasModellingRule", rule)
    ref(r_, "HasComponent", parent, forward=False)
    if rule:   # declaracion en el tipo
        pb, pbit = "ns=1;i=6061", "ns=1;i=6062"
    else:
        pb, pbit = f"{nodeid}.byte", f"{nodeid}.bit"
        val = el(q("Value"), parent=v)
        el(x("Boolean"), text="false", parent=val)
    ref(r_, "HasProperty", pb)
    ref(r_, "HasProperty", pbit)
    return [v, propiedad(pb, "byte", nodeid, "Int32", rule, byte),
            propiedad(pbit, "bit", nodeid, "Int32", rule, bit)]


def objeto_control(nodeid, parent, rule=None):
    o = el(q("UAObject"), {"BrowseName": "1:Control", "ParentNodeId": parent, "NodeId": nodeid})
    el(q("DisplayName"), text="Control", parent=o)
    r_ = el(q("References"), parent=o)
    ref(r_, "HasTypeDefinition", "i=58")
    if rule:
        ref(r_, "HasModellingRule", rule)
    ref(r_, "HasComponent", parent, forward=False)
    return o, r_


def anadir_control_pulsador(padre_id, ctrl_id, var_id, rule=None, byte=None, bit=None):
    padre = next(e for e in root if e.get("NodeId") == padre_id)
    ref(padre.find(q("References")), "HasComponent", ctrl_id)
    obj, oref = objeto_control(ctrl_id, padre_id, rule)
    ref(oref, "HasComponent", var_id)
    nodos = [etree.Comment(" Pulsacion remota: escrita por el metodo ControlarPulsador "), obj]
    nodos += variable_address(var_id, "PulsadoRemoto", ctrl_id, rule, byte, bit)
    pos = list(root).index(padre) + 1
    for k, n in enumerate(nodos):
        root.insert(pos + k, n)


anadir_control_pulsador("ns=1;i=1014", "ns=1;i=5007", "ns=1;i=6060", rule="i=78")
anadir_control_pulsador("ns=1;s=PulsadorVerde", "ns=1;s=PulsadorVerde.Control",
                        "ns=1;s=PulsadorVerde.Control.PulsadoRemoto", byte=0, bit=0)
ROL["PulsadoRemoto"] = ("M", 3)

# --- 4. Cada variable de tipo Address: propiedad area + AccessLevel --------
next_id = 6200
for var in list(root.iter(q("UAVariable"))):
    refs = var.find(q("References"))
    tds = [r_.text for r_ in refs if r_.get("ReferenceType") == "HasTypeDefinition"]
    if tds != ["ns=1;i=2003"]:
        continue
    name = var.get("BrowseName").split(":", 1)[1]
    area, acc = ROL[name]
    var.set("AccessLevel", str(acc))
    var.set("UserAccessLevel", str(acc))
    nid = var.get("NodeId")
    es_tipo = any(r_.get("ReferenceType") == "HasModellingRule" for r_ in refs)
    if es_tipo:
        pid = f"ns=1;i={next_id}"
        next_id += 1
        nodo = propiedad(pid, "area", nid, AREA_DT, "i=78")
    else:
        pid = f"{nid}.area"
        nodo = propiedad(pid, "area", nid, AREA_DT, value=AREA_CODE[area])
    ref(refs, "HasProperty", pid)
    root.insert(list(root).index(var) + 1, nodo)

etree.indent(tree, space="    ")
tree.write(DST, xml_declaration=True, encoding="utf-8", pretty_print=True)
print("Escrito", DST, "| propiedades area de tipo:", next_id - 6200)
